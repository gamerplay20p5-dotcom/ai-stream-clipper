"""Command line entry point.

    clipper api                       # API + dashboard + scheduler/maintenance
    clipper worker --roles capture,monitor
    clipper worker --roles audio,render
    clipper worker --roles vision
    clipper migrate
    clipper create-admin EMAIL        # password read from ASC_NEW_ADMIN_PASSWORD or prompted
    clipper probe-gpu                 # print OpenVINO devices (GPU validation helper)
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
import os
import signal
import sys

from .config import get_settings
from .logging_setup import setup_logging

log = logging.getLogger("clipper")


def cmd_api(args) -> None:
    import uvicorn

    from .api.app import create_app

    s = get_settings()
    setup_logging("api", s.log_level, s.log_json)
    tls = {}
    if s.tls_cert and s.tls_key:
        tls = {"ssl_certfile": str(s.tls_cert), "ssl_keyfile": str(s.tls_key)}
    elif s.is_production:
        logging.getLogger("clipper").warning("API running WITHOUT TLS - logins would travel in clear text")
    uvicorn.run(create_app(), host=args.host, port=args.port, log_config=None, proxy_headers=False,
                access_log=False, **tls)


async def _run_worker(roles: list[str], stop_event=None) -> None:
    """``stop_event``: optional ``threading.Event`` used by tests to stop the worker."""
    from .workers import handlers
    from .workers.runtime import RoleSpec, WorkerRuntime

    s = get_settings()
    specs: list[RoleSpec] = []
    background = []
    extra_caps: dict = {}
    base_url = ""
    server = None
    if "capture" in roles:
        import uvicorn

        from .workers.capture import CaptureManager
        from .workers.segment_server import create_segment_app
        from .workers.segment_store import SegmentStore

        store = SegmentStore(s.capture_dir)
        base_url = s.segment_server_public_url or f"http://127.0.0.1:{s.segment_server_port}"
        mgr = CaptureManager(s, store, base_url)
        specs.append(RoleSpec("capture", mgr.handle, s.max_concurrent_captures))
        extra_caps["max_concurrent_captures"] = s.max_concurrent_captures
        server = uvicorn.Server(uvicorn.Config(create_segment_app(s, store), host=s.segment_server_bind,
                                               port=s.segment_server_port, log_config=None, access_log=False))
    if "audio" in roles:
        from .ai.registry import installed as installed_models

        specs.append(RoleSpec("audio_analyze", handlers.handle_audio, s.concurrency_audio))
        extra_caps["whisper_model"] = s.whisper_model
        extra_caps["acoustic_models"] = installed_models("audio") if s.yamnet_enabled else []
    if "scoring" in roles:
        specs.append(RoleSpec("score", handlers.handle_score, s.concurrency_scoring))
    metrics_hook = None
    if "vision" in roles:
        from .ai.manager import get_manager, gpu_memory
        from .ai.registry import installed

        specs.append(RoleSpec("vision_analyze", handlers.handle_vision, s.concurrency_vision))
        models = [m for m in installed() if not s.ai_models or m in s.ai_models]
        vram = gpu_memory()
        extra_caps.update({
            "vision_provider": s.vision_provider, "ai_models": models, "vision_concurrency": s.concurrency_vision,
            "ai_device": s.openvino_device, "cpu_fallback": s.ai_allow_cpu_fallback,
            "vram_total_gb": round(vram.get("total_bytes", 0) / 1024**3, 2), "gpu_name": vram.get("name", ""),
        })
        manager = get_manager(s.openvino_device, s.ai_allow_cpu_fallback)

        def metrics_hook() -> dict:
            mem = gpu_memory() if s.gpu_device or s.openvino_device == "GPU" else {}
            return {"ai": manager.snapshot(), "vram_used_bytes": mem.get("used_bytes"),
                    "vram_total_bytes": mem.get("total_bytes")}
    if "render" in roles:
        specs.append(RoleSpec("render", handlers.handle_render, s.concurrency_render))
        specs.append(RoleSpec("dataset_media", handlers.handle_dataset_media, 1))
    if "monitor" in roles:
        background.append(handlers.monitor_loop)

    rt = WorkerRuntime(s, roles, specs, background=background, base_url=base_url, extra_caps=extra_caps,
                       metrics_hook=metrics_hook)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, rt.stop)
        except (NotImplementedError, RuntimeError):  # Windows
            pass
    tasks = [asyncio.create_task(rt.run())]
    if stop_event is not None:
        async def _watch_stop():
            while not stop_event.is_set():
                await asyncio.sleep(0.2)
            rt.stop()
        tasks.append(asyncio.create_task(_watch_stop()))
    if server is not None:
        tasks.append(asyncio.create_task(server.serve()))
    try:
        await tasks[0]
    finally:
        if server is not None:
            server.should_exit = True
        for t in tasks[1:]:
            if t.get_coro().__name__ == "_watch_stop":
                t.cancel()
        await asyncio.gather(*tasks[1:], return_exceptions=True)


def cmd_worker(args) -> None:
    s = get_settings()
    roles = [r.strip() for r in (args.roles or ",".join(s.worker_roles)).split(",") if r.strip()]
    valid = {"capture", "monitor", "audio", "scoring", "vision", "render"}
    bad = set(roles) - valid
    if not roles or bad:
        sys.exit(f"invalid roles {sorted(bad)}; choose from {sorted(valid)}")
    s.worker_roles = roles
    setup_logging("worker:" + ",".join(roles), s.log_level, s.log_json)
    try:
        asyncio.run(_run_worker(roles))
    except KeyboardInterrupt:
        pass


def cmd_migrate(args) -> None:
    from .migrate import downgrade_db, upgrade_db

    s = get_settings()
    setup_logging("migrate", s.log_level, s.log_json)
    if args.downgrade:
        downgrade_db(s.database_url, args.downgrade)
    else:
        upgrade_db(s.database_url)
    print("ok")


def cmd_create_admin(args) -> None:
    from sqlalchemy import select

    from .db import init_engine, session_scope
    from .migrate import upgrade_db
    from .models import User
    from .security import hash_password

    s = get_settings()
    pw = os.environ.get("ASC_NEW_ADMIN_PASSWORD") or getpass.getpass("password: ")
    if len(pw) < 10:
        sys.exit("password must have at least 10 characters")
    init_engine(s.database_url)
    upgrade_db(s.database_url)
    with session_scope() as db:
        u = db.scalar(select(User).where(User.email == args.email.lower()))
        if u is None:
            db.add(User(email=args.email.lower(), password_hash=hash_password(pw), role=args.role))
        else:
            u.password_hash = hash_password(pw)
            u.role = args.role
    print(f"user {args.email} saved")


def cmd_healthcheck(args) -> None:
    """Container health: API -> /healthz; worker -> recent successful heartbeat to the API."""
    import time
    import urllib.request

    if args.target == "api":
        import ssl

        port = os.environ.get("ASC_API_PORT", "8780")
        scheme = "https" if get_settings().tls_cert else "http"
        ctx = ssl.create_default_context()
        ctx.check_hostname = False  # loopback probe of our own listener
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(f"{scheme}://127.0.0.1:{port}/healthz", timeout=5,
                                    context=ctx if scheme == "https" else None) as r:
            sys.exit(0 if r.status == 200 else 1)
    from .workers.runtime import HEARTBEAT_FILE

    try:
        age = time.time() - float(HEARTBEAT_FILE.read_text())
    except (OSError, ValueError):
        sys.exit(1)
    sys.exit(0 if age < args.max_age else 1)


def cmd_publisher(args) -> None:
    """Phase 4 publisher service (YouTube / TikTok / Instagram)."""
    s = get_settings()
    s.require_secrets("jwt_secret")  # tokens are encrypted with a key derived from it
    setup_logging("publisher", s.log_level, s.log_json)
    from .publish.runner import main as run

    run()


def cmd_probe_gpu(args) -> None:
    from .workers.sysinfo import gpu_info

    print(gpu_info(get_settings().gpu_device or "/dev/dri/renderD128"))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="clipper")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("api")
    a.add_argument("--host", default=os.environ.get("ASC_API_BIND", "0.0.0.0"))
    a.add_argument("--port", type=int, default=int(os.environ.get("ASC_API_PORT", "8780")))
    a.set_defaults(fn=cmd_api)
    w = sub.add_parser("worker")
    w.add_argument("--roles", default="")
    w.set_defaults(fn=cmd_worker)
    m = sub.add_parser("migrate")
    m.add_argument("--downgrade", default="")
    m.set_defaults(fn=cmd_migrate)
    c = sub.add_parser("create-admin")
    c.add_argument("email")
    c.add_argument("--role", default="admin", choices=["admin", "viewer"])
    c.set_defaults(fn=cmd_create_admin)
    h = sub.add_parser("healthcheck")
    h.add_argument("target", choices=["api", "worker"])
    h.add_argument("--max-age", type=float, default=90.0)
    h.set_defaults(fn=cmd_healthcheck)
    pb = sub.add_parser("publisher")
    pb.set_defaults(fn=cmd_publisher)
    g = sub.add_parser("probe-gpu")
    g.set_defaults(fn=cmd_probe_gpu)
    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
