"""Signed HTTP client for the internal API and segment servers."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import httpx

from ..security import EMPTY_SHA, sha256_bytes, sha256_file, sign_request

log = logging.getLogger(__name__)


class InternalError(RuntimeError):
    def __init__(self, status: int, detail: str):
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


class InternalClient:
    def __init__(self, base_url: str, secret: str, worker_id: str, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.secret = secret
        self.worker_id = worker_id
        from ..config import get_settings

        ca = get_settings().api_ca_file
        # verify the API certificate against the project CA (pinned); system CAs otherwise
        verify: str | bool = str(ca) if ca else True
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10.0), follow_redirects=False,
                                       verify=verify)

    async def aclose(self) -> None:
        await self._http.aclose()

    def _url(self, path_or_url: str, params: dict | None) -> httpx.URL:
        url = path_or_url if path_or_url.startswith("http") else self.base_url + path_or_url
        return httpx.URL(url, params={k: v for k, v in (params or {}).items() if v is not None} or None)

    def _headers(self, method: str, url: httpx.URL, body_sha: str) -> dict[str, str]:
        return sign_request(self.secret, self.worker_id, method, url.raw_path.decode(), body_sha)

    async def request(self, method: str, path: str, *, json_body=None, params: dict | None = None,
                      retries: int = 4) -> dict | list:
        import json as _json

        body = b"" if json_body is None else _json.dumps(json_body, separators=(",", ":")).encode()
        url = self._url(path, params)
        delay = 1.0
        for attempt in range(retries + 1):
            headers = self._headers(method, url, sha256_bytes(body) if body else EMPTY_SHA)
            if body:
                headers["Content-Type"] = "application/json"
            try:
                r = await self._http.request(method, url, content=body or None, headers=headers)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                if attempt >= retries:
                    raise InternalError(0, f"{type(exc).__name__}: {exc}") from exc
                log.warning("internal request failed, retrying", extra={"path": url.path, "err": str(exc)})
                await asyncio.sleep(delay)
                delay = min(30.0, delay * 2)
                continue
            if r.status_code >= 500 and attempt < retries:
                await asyncio.sleep(delay)
                delay = min(30.0, delay * 2)
                continue
            if r.status_code >= 400:
                try:
                    detail = r.json().get("detail", r.text)
                except Exception:
                    detail = r.text
                raise InternalError(r.status_code, str(detail)[:500])
            return r.json() if r.content else {}
        raise InternalError(0, "unreachable")

    async def get(self, path: str, **params):
        return await self.request("GET", path, params=params)

    async def post(self, path: str, body=None, **params):
        return await self.request("POST", path, json_body=body if body is not None else {}, params=params)

    async def upload(self, path: str, file: Path, params: dict | None = None, retries: int = 3) -> dict:
        """PUT a file as raw body; the signature covers its SHA-256 (integrity + auth)."""
        sha = sha256_file(file)
        url = self._url(path, params)
        delay = 2.0
        for attempt in range(retries + 1):
            headers = self._headers("PUT", url, sha)
            headers["Content-Type"] = "application/octet-stream"
            headers["Content-Length"] = str(os.path.getsize(file))
            try:
                with open(file, "rb") as f:
                    r = await self._http.put(url, content=_iter_file(f), headers=headers)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                if attempt >= retries:
                    raise InternalError(0, f"upload failed: {exc}") from exc
                await asyncio.sleep(delay)
                delay *= 2
                continue
            if r.status_code >= 500 and attempt < retries:
                await asyncio.sleep(delay)
                delay *= 2
                continue
            if r.status_code >= 400:
                raise InternalError(r.status_code, r.text[:500])
            return r.json()
        raise InternalError(0, "unreachable")

    async def download(self, path_or_url: str, dest: Path, *, expected_sha: str | None = None,
                       params: dict | None = None, retries: int = 3) -> dict[str, str]:
        """Signed GET streamed to ``dest``; verifies SHA-256 against ``expected_sha`` or the
        ``X-ASC-SHA256`` response header. Returns response headers."""
        url = self._url(path_or_url, params)
        delay = 1.0
        for attempt in range(retries + 1):
            headers = self._headers("GET", url, EMPTY_SHA)
            tmp = dest.with_suffix(dest.suffix + ".part")
            try:
                async with self._http.stream("GET", url, headers=headers) as r:
                    if r.status_code >= 400:
                        text = (await r.aread()).decode(errors="replace")[:300]
                        if r.status_code >= 500 and attempt < retries:
                            raise httpx.TransportError(f"server error {r.status_code}")
                        raise InternalError(r.status_code, text)
                    with open(tmp, "wb") as f:
                        async for chunk in r.aiter_bytes(1 << 20):
                            f.write(chunk)
                    resp_headers = dict(r.headers)
                want = expected_sha or resp_headers.get("x-asc-sha256")
                got = sha256_file(tmp)
                if want and got != want:
                    raise httpx.TransportError("integrity check failed (sha256 mismatch)")
                os.replace(tmp, dest)
                return resp_headers
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                tmp.unlink(missing_ok=True)
                if attempt >= retries:
                    raise InternalError(0, f"download failed: {exc}") from exc
                await asyncio.sleep(delay)
                delay *= 2
        raise InternalError(0, "unreachable")


async def _iter_file(f, chunk: int = 1 << 20):
    while True:
        b = f.read(chunk)
        if not b:
            break
        yield b
