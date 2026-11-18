"""YouTube Data API v3: OAuth 2.0 (PKCE), resumable upload of vertical Shorts, status, statistics.

* scopes: youtube.upload (insert) + youtube.readonly (channel, status, statistics);
* videos.insert costs 1600 quota units (default project quota: 10 000/day) - tracked per account;
* resumable protocol: the session URI is persisted before the first byte, so after a timeout
  or restart the upload is RESUMED (or, if it had completed, the created video is recovered
  from the session) - a clip is never inserted twice;
* projects not yet verified by Google can only upload PRIVATE videos (Google policy).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

import httpx

from .base import App, PublishError, Tokens, UploadResult, check_response, expires_in

log = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/youtube/v3"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly"]
INSERT_COST, LIST_COST = 1600, 1
CHUNK = 8 * 1024 * 1024  # multiple of 256 KiB, as the protocol requires
PRIVACY = ("private", "unlisted", "public")
platform = "youtube"


def _client() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0), follow_redirects=False)


def auth_url(app: App, state: str, challenge: str) -> str:
    return AUTH_URL + "?" + urlencode({
        "client_id": app.client_id, "redirect_uri": app.redirect_uri, "response_type": "code",
        "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent", "include_granted_scopes": "true",
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
    })


def _tokens(d: dict, old: Tokens | None = None) -> Tokens:
    return Tokens(access_token=d["access_token"], refresh_token=d.get("refresh_token") or (old.refresh_token if old else ""),
                  expires_at=expires_in(d.get("expires_in")), scope=d.get("scope", old.scope if old else ""))


def exchange(app: App, code: str, verifier: str) -> Tokens:
    with _client() as c:
        r = c.post(TOKEN_URL, data={"code": code, "client_id": app.client_id, "client_secret": app.client_secret,
                                    "redirect_uri": app.redirect_uri, "grant_type": "authorization_code",
                                    "code_verifier": verifier})
    t = _tokens(check_response(r, "troca do código OAuth"))
    if not t.refresh_token:
        raise PublishError("o Google não devolveu refresh token (remova o acesso do app na conta Google e conecte "
                           "de novo)", retryable=False)
    return t


def refresh(app: App, tokens: Tokens) -> Tokens:
    if not tokens.refresh_token:
        raise PublishError("sem refresh token: reconecte a conta", retryable=False, auth=True)
    with _client() as c:
        r = c.post(TOKEN_URL, data={"client_id": app.client_id, "client_secret": app.client_secret,
                                    "refresh_token": tokens.refresh_token, "grant_type": "refresh_token"})
    if r.status_code == 400 and "invalid_grant" in r.text:
        raise PublishError("o Google revogou o acesso (invalid_grant): reconecte a conta", retryable=False, auth=True)
    return _tokens(check_response(r, "renovação do token"), tokens)


def _h(tokens: Tokens) -> dict:
    return {"Authorization": f"Bearer {tokens.access_token}"}


def account_info(tokens: Tokens) -> dict:
    with _client() as c:
        r = c.get(f"{API}/channels", params={"part": "snippet", "mine": "true"}, headers=_h(tokens))
    items = check_response(r, "leitura do canal").get("items") or []
    if not items:
        raise PublishError("esta conta Google não tem canal do YouTube", retryable=False)
    ch = items[0]
    sn = ch.get("snippet") or {}
    return {"external_id": ch["id"], "name": sn.get("title", ""), "handle": sn.get("customUrl", ""),
            "meta": {"privacy_options": list(PRIVACY), "quota_limit": 10000, "insert_cost": INSERT_COST}}


def _metadata(pub) -> dict:
    status = {"privacyStatus": pub.privacy if pub.privacy in PRIVACY else "private",
              "selfDeclaredMadeForKids": False, "embeddable": True}
    sched = (pub.upload_state or {}).get("publish_at")
    if sched:  # YouTube-side scheduling: private until publishAt, then public
        status["privacyStatus"] = "private"
        status["publishAt"] = sched
    title = pub.title.strip()[:100] or "Corte"
    return {"snippet": {"title": title, "description": pub.description[:4900], "tags": list(pub.tags)[:30],
                        "categoryId": "20", "defaultLanguage": "pt", "defaultAudioLanguage": "pt"},
            "status": status}


def _done(data: dict) -> UploadResult:
    vid = data["id"]
    return UploadResult(remote_id=vid, remote_url=f"https://youtube.com/shorts/{vid}",
                        remote_status={"uploadStatus": (data.get("status") or {}).get("uploadStatus"),
                                       "privacyStatus": (data.get("status") or {}).get("privacyStatus")})


def upload(pub, tokens: Tokens, video: Path, save_state) -> UploadResult:
    """Resumable upload. ``save_state(dict)`` persists the session BEFORE bytes are sent."""
    size = video.stat().st_size
    st = dict(pub.upload_state or {})
    with _client() as c:
        session = st.get("session_uri")
        offset = 0
        if session:  # resume: ask the server what it already has
            r = c.put(session, headers={**_h(tokens), "Content-Range": f"bytes */{size}", "Content-Length": "0"})
            if r.status_code in (200, 201):
                log.info("youtube upload had completed before restart: recovered video", extra={"pub": pub.id})
                return _done(r.json())
            if r.status_code == 308:
                rng = r.headers.get("range", "")
                offset = int(rng.split("-")[1]) + 1 if rng.startswith("bytes=") else 0
            elif r.status_code in (404, 410):
                session = None  # expired, never completed -> start a new session (no duplicate possible)
            else:
                check_response(r, "consulta da sessão de upload")
        if not session:
            r = c.post(UPLOAD, params={"uploadType": "resumable", "part": "snippet,status"},
                       headers={**_h(tokens), "Content-Type": "application/json; charset=UTF-8",
                                "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)},
                       content=json.dumps(_metadata(pub)))
            check_response(r, "início do upload")
            session = r.headers.get("location")
            if not session:
                raise PublishError("o YouTube não devolveu a sessão de upload", retryable=True)
            st.update({"session_uri": session, "size": size, "started": datetime.utcnow().isoformat() + "Z"})
            save_state(st)  # persisted before any byte: a restart resumes this same session
            offset = 0
        with open(video, "rb") as f:
            while offset < size:
                f.seek(offset)
                chunk = f.read(CHUNK)
                end = offset + len(chunk) - 1
                r = c.put(session, headers={**_h(tokens), "Content-Type": "video/mp4",
                                            "Content-Range": f"bytes {offset}-{end}/{size}"}, content=chunk,
                          timeout=httpx.Timeout(300.0, connect=15.0))
                if r.status_code == 308:
                    rng = r.headers.get("range", "")
                    offset = int(rng.split("-")[1]) + 1 if rng.startswith("bytes=") else 0
                    st["sent"] = offset
                    save_state(st)
                    continue
                if r.status_code in (200, 201):
                    return _done(r.json())
                check_response(r, "envio do vídeo")
    raise PublishError("upload terminou sem resposta final do YouTube", retryable=True)


def status(pub, tokens: Tokens) -> dict:
    with _client() as c:
        r = c.get(f"{API}/videos", params={"part": "status,processingDetails", "id": pub.remote_id}, headers=_h(tokens))
    items = check_response(r, "consulta do processamento").get("items") or []
    if not items:
        return {"state": "failed", "detail": "vídeo não encontrado no canal (removido?)"}
    v = items[0]
    s = v.get("status") or {}
    up = s.get("uploadStatus")
    proc = (v.get("processingDetails") or {}).get("processingStatus")
    if up in ("failed", "rejected", "deleted"):
        return {"state": "failed", "detail": s.get("failureReason") or s.get("rejectionReason") or up, "raw": s}
    if up == "processed" or proc == "succeeded":
        return {"state": "published", "privacy": s.get("privacyStatus"), "publish_at": s.get("publishAt"), "raw": s}
    return {"state": "processing", "detail": proc or up, "raw": s}


def metrics(pub, tokens: Tokens) -> dict:
    with _client() as c:
        r = c.get(f"{API}/videos", params={"part": "statistics", "id": pub.remote_id}, headers=_h(tokens))
    items = check_response(r, "estatísticas").get("items") or []
    st = (items[0].get("statistics") if items else None) or {}
    return {k: int(v) for k, v in st.items() if str(v).isdigit()}
