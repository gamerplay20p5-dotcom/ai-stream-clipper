"""FastAPI application factory (API gateway + dashboard + task scheduler)."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .. import __version__, maintenance, queue, storage
from ..config import get_settings
from ..db import get_db, init_engine, session_scope
from ..migrate import upgrade_db
from ..models import Clip, StreamSession, User, Worker
from ..security import hash_password
from . import routes_library, routes_refs, routes_speakers, routes_users
from . import (routes_admin, routes_auth, routes_clips, routes_director, routes_internal, routes_publish,
               routes_review, routes_storage, routes_streamers)

log = logging.getLogger(__name__)


def bootstrap_admin() -> None:
    s = get_settings()
    if not (s.admin_email and s.admin_password):
        return
    with session_scope() as db:
        if db.scalar(select(func.count()).select_from(User)):
            return
        db.add(User(email=s.admin_email.strip().lower(), password_hash=hash_password(s.admin_password), role="admin"))
        log.info("bootstrap admin created", extra={"email": s.admin_email})


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    if s.is_production:
        s.require_secrets("jwt_secret", "internal_secret")
        s.require_public_origin()
    init_engine(s.database_url)
    await asyncio.to_thread(upgrade_db, s.database_url)
    bootstrap_admin()
    storage.root()
    task = None
    if app.state.run_maintenance:
        task = asyncio.create_task(maintenance.loop(s.maintenance_interval_s))
    log.info("api started", extra={"version": __version__, "env": s.env})
    yield
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def create_app(run_maintenance: bool = True) -> FastAPI:
    s = get_settings()
    app = FastAPI(title="AI Stream Clipper", version=__version__, lifespan=lifespan,
                  docs_url="/api/docs" if not s.is_production else None,
                  openapi_url=None if s.is_production else "/api/openapi.json")  # no public API map in production
    app.state.run_maintenance = run_maintenance

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        from .deps import via_proxy

        # the public domain (reverse proxy) never reaches the worker API: workers use the LAN/docker network
        if request.url.path.startswith("/internal/") and via_proxy(request):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        resp = await call_next(request)
        if request.url.scheme == "https" or via_proxy(request):
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        if not request.url.path.startswith("/api/docs"):
            resp.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; "
                "connect-src 'self'; frame-ancestors 'none'",
            )
        return resp

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("unhandled error", extra={"path": request.url.path})
        return JSONResponse({"detail": "internal error"}, status_code=500)

    app.include_router(routes_auth.router)
    app.include_router(routes_streamers.router)
    app.include_router(routes_clips.router)
    app.include_router(routes_admin.router)
    from . import routes_account

    app.include_router(routes_account.router)
    app.include_router(routes_account.internal)
    from . import routes_admin_customers, routes_customer_publish

    app.include_router(routes_customer_publish.router)  # customers: their own destinations and publications
    app.include_router(routes_admin_customers.router)  # HTS internal commercial panel (admin only)
    from . import routes_admin_promotions

    app.include_router(routes_admin_promotions.router)  # promotion codes, forwarded to the commercial service (admin only)
    app.include_router(routes_review.router)
    app.include_router(routes_publish.router)
    app.include_router(routes_director.router)
    app.include_router(routes_speakers.router)
    app.include_router(routes_refs.router)
    app.include_router(routes_users.router)
    app.include_router(routes_library.router)
    app.include_router(routes_storage.router)
    app.include_router(routes_internal.router)

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"status": "ok", "version": __version__}

    @app.get("/readyz", include_in_schema=False)
    def readyz(db: Session = Depends(get_db)):
        try:
            db.execute(text("SELECT 1"))
        except Exception as exc:
            raise HTTPException(503, f"database unavailable: {type(exc).__name__}") from exc
        return {"status": "ready"}

    @app.get("/metrics", include_in_schema=False)
    def metrics(request: Request, db: Session = Depends(get_db)):
        token = get_settings().metrics_token
        if token and request.headers.get("authorization") != f"Bearer {token}":
            raise HTTPException(401, "metrics token required")
        lines = ["# TYPE asc_jobs gauge"]
        for t, by in queue.queue_stats(db).items():
            for st, n in by.items():
                lines.append(f'asc_jobs{{type="{t}",status="{st}"}} {n}')
        lines.append("# TYPE asc_clips gauge")
        for st, n in db.execute(select(Clip.status, func.count()).group_by(Clip.status)).all():
            lines.append(f'asc_clips{{status="{st}"}} {n}')
        live = db.scalar(select(func.count()).select_from(StreamSession).where(StreamSession.status == "live")) or 0
        online = db.scalar(select(func.count()).select_from(Worker).where(Worker.status == "online")) or 0
        lines += ["# TYPE asc_live_sessions gauge", f"asc_live_sessions {live}",
                  "# TYPE asc_workers_online gauge", f"asc_workers_online {online}",
                  "# TYPE asc_storage_used_bytes gauge", f"asc_storage_used_bytes {storage.usage_bytes()}"]
        return PlainTextResponse("\n".join(lines) + "\n")

    dash = s.dashboard_dir
    if dash and Path(dash).is_dir():
        root = Path(dash).resolve()

        site = root / "site"
        public_pages = {"privacidade": "privacidade.html", "termos": "termos.html"}

        def _logged_in(request: Request) -> bool:
            from ..security import decode_user_token
            from .deps import COOKIE, jwt_secret

            token = request.cookies.get(COOKIE)
            return bool(token and decode_user_token(jwt_secret(), token))

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str, request: Request):
            if full_path.startswith(("api/", "internal/")):
                raise HTTPException(404)
            # public pages (no login): presentation, privacy policy, terms. The dashboard itself and
            # its authentication are unchanged: "/" serves the dashboard when a session exists.
            page = public_pages.get(full_path.strip("/")) or ("index.html" if not full_path.strip("/") and
                                                              not _logged_in(request) else None)
            if page and (site / page).is_file():
                return FileResponse(site / page, headers={"Cache-Control": "no-cache"})
            candidate = (root / full_path).resolve()
            if full_path and candidate.is_file() and root in candidate.parents:
                headers = {"Cache-Control": "public, max-age=31536000, immutable"} if "/assets/" in \
                    candidate.as_posix() else {}
                return FileResponse(candidate, headers=headers)
            return FileResponse(root / "index.html", headers={"Cache-Control": "no-cache"})

    return app
