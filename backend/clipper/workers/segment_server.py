"""Authenticated segment server run inside each capture worker.

Other workers request exactly the media they need (one segment, or a time range remuxed
into a single MP4) instead of transferring whole lives. Every response carries a SHA-256
(``X-ASC-SHA256``) that clients verify.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse

from .. import media
from ..config import Settings
from ..security import EMPTY_SHA, sha256_file, verify_signature
from .segment_store import SegmentStore

log = logging.getLogger(__name__)


def create_segment_app(settings: Settings, store: SegmentStore) -> FastAPI:
    app = FastAPI(title="ASC segment server", docs_url=None, redoc_url=None, openapi_url=None)
    sem = asyncio.Semaphore(4)  # concurrent extracts

    def _auth(request: Request) -> None:
        qs = request.url.query
        chk = verify_signature(settings.internal_secret, request.headers, request.method,
                               request.url.path + (f"?{qs}" if qs else ""), EMPTY_SHA, settings.signature_max_skew_s)
        if not chk.ok:
            raise HTTPException(401, f"auth failed: {chk.reason}")

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/segments/{session_id}/{idx}")
    def segment(session_id: int, idx: int, request: Request):
        _auth(request)
        seg = store.get(session_id, idx)
        if seg is None:
            raise HTTPException(404, "segment not available (evicted or unknown)")
        p = store.path(session_id, seg)
        if not p.is_file():
            raise HTTPException(404, "segment file missing")
        return FileResponse(p, media_type="video/mp2t",
                            headers={"X-ASC-SHA256": seg.sha256, "X-ASC-Start": str(seg.start),
                                     "X-ASC-Duration": str(seg.duration)})

    @app.get("/extract")
    async def extract(session_id: int, start: float, end: float, request: Request, background: BackgroundTasks):
        _auth(request)
        if end <= start or end - start > 900:
            raise HTTPException(422, "invalid range (max 15 minutes)")
        chain = store.covering(session_id, start, end)
        if not chain:
            raise HTTPException(404, "range not available in buffer")
        tmpdir = store.session_dir(session_id) / "extracts"
        tmpdir.mkdir(exist_ok=True)
        out = tmpdir / f"{uuid.uuid4().hex}.mp4"
        async with sem:
            try:
                await media.concat_copy([store.path(session_id, s) for s in chain], out, tmpdir)
            except media.MediaError as exc:
                out.unlink(missing_ok=True)
                raise HTTPException(500, f"extract failed: {exc}") from exc
        sha = await asyncio.to_thread(sha256_file, out)
        background.add_task(out.unlink, missing_ok=True)
        dur = chain[-1].end - chain[0].start
        return FileResponse(out, media_type="video/mp4", headers={
            "X-ASC-SHA256": sha, "X-ASC-Source-Start": f"{chain[0].start:.3f}", "X-ASC-Source-Duration": f"{dur:.3f}"})

    return app
