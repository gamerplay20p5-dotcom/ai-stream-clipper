"""TikTok Content Posting API (Direct Post, FILE_UPLOAD).

Rules respected (TikTok developer guidelines):
* privacy must be one of ``creator_info.privacy_level_options`` - apps that have not passed
  TikTok's audit can only post ``SELF_ONLY`` (private); we never force or fake another level;
* the creator must consent to each post, so TikTok is MANUAL ONLY here: automatic modes are
  refused by the eligibility rules (see service.py) - no attempt to bypass the audit;
* video duration must fit ``max_video_post_duration_sec`` from creator_info.
Status: implemented against the public API documentation; not validated with a real upload
until a TikTok app (client key/secret, scopes video.publish + video.upload) is configured.
"""

from __future__ import annotations

import math
from pathlib import Path
from urllib.parse import urlencode

import httpx

from .base import App, PublishError, Tokens, UploadResult, check_response, expires_in

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
API = "https://open.tiktokapis.com/v2"
SCOPES = ["user.info.basic", "video.publish", "video.upload"]
CHUNK = 10 * 1024 * 1024  # 5-64 MB per chunk (last chunk may be up to 128 MB)
platform = "tiktok"
AUTO_ALLOWED = False  # see module docstring


def _client() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0))


def auth_url(app: App, state: str, challenge: str) -> str:
    return AUTH_URL + "?" + urlencode({"client_key": app.client_id, "scope": ",".join(SCOPES), "response_type": "code",
                                       "redirect_uri": app.redirect_uri, "state": state, "code_challenge": challenge,
                                       "code_challenge_method": "S256"})


def _tokens(d: dict) -> Tokens:
    if "access_token" not in d:
        raise PublishError(f"TikTok não devolveu token: {d.get('error_description') or d.get('error') or d}",
                           retryable=False)
    return Tokens(d["access_token"], d.get("refresh_token", ""), expires_in(d.get("expires_in")), d.get("scope", ""),
                  {"open_id": d.get("open_id", "")})


def exchange(app: App, code: str, verifier: str) -> Tokens:
    with _client() as c:
        r = c.post(TOKEN_URL, data={"client_key": app.client_id, "client_secret": app.client_secret, "code": code,
                                    "grant_type": "authorization_code", "redirect_uri": app.redirect_uri,
                                    "code_verifier": verifier},
                   headers={"Content-Type": "application/x-www-form-urlencoded"})
    return _tokens(check_response(r, "troca do código OAuth (TikTok)"))


def refresh(app: App, tokens: Tokens) -> Tokens:
    with _client() as c:
        r = c.post(TOKEN_URL, data={"client_key": app.client_id, "client_secret": app.client_secret,
                                    "grant_type": "refresh_token", "refresh_token": tokens.refresh_token},
                   headers={"Content-Type": "application/x-www-form-urlencoded"})
    return _tokens(check_response(r, "renovação do token (TikTok)"))


def _h(tokens: Tokens) -> dict:
    return {"Authorization": f"Bearer {tokens.access_token}", "Content-Type": "application/json; charset=UTF-8"}


def _api_error(data: dict, what: str) -> None:
    err = (data or {}).get("error") or {}
    if err.get("code") not in (None, "", "ok"):
        code = err.get("code")
        retry = code in ("rate_limit_exceeded", "spam_risk_too_many_posts", "internal_error")
        raise PublishError(f"{what}: {code} {err.get('message', '')}", retryable=retry,
                           auth=code in ("access_token_invalid", "scope_not_authorized"))


def creator_info(tokens: Tokens) -> dict:
    with _client() as c:
        r = c.post(f"{API}/post/publish/creator_info/query/", headers=_h(tokens))
    d = check_response(r, "creator_info (TikTok)")
    _api_error(d, "creator_info (TikTok)")
    return d.get("data") or {}


def account_info(tokens: Tokens) -> dict:
    with _client() as c:
        r = c.get(f"{API}/user/info/", params={"fields": "open_id,display_name,username"}, headers=_h(tokens))
    d = check_response(r, "dados do usuário (TikTok)")
    _api_error(d, "dados do usuário (TikTok)")
    u = (d.get("data") or {}).get("user") or {}
    ci = creator_info(tokens)
    return {"external_id": u.get("open_id") or tokens.extra.get("open_id", ""), "name": u.get("display_name", ""),
            "handle": u.get("username", ""),
            "meta": {"privacy_options": ci.get("privacy_level_options") or [],
                     "max_duration_s": ci.get("max_video_post_duration_sec"),
                     "comment_disabled": ci.get("comment_disabled"), "auto_allowed": AUTO_ALLOWED}}


def upload(pub, tokens: Tokens, video: Path, save_state) -> UploadResult:
    st = dict(pub.upload_state or {})
    if st.get("publish_id"):  # init already accepted: never init twice (would create a second post)
        return UploadResult(remote_id=st["publish_id"], remote_status={"status": "resumed"}, state="processing")
    ci = creator_info(tokens)
    options = ci.get("privacy_level_options") or []
    if pub.privacy not in options:
        raise PublishError(f"privacidade {pub.privacy!r} não é permitida para esta conta (opções do TikTok: {options})",
                           retryable=False)
    size = video.stat().st_size
    chunk = size if size <= 64 * 1024 * 1024 else CHUNK
    count = max(1, math.floor(size / chunk))
    body = {"post_info": {"title": (pub.title + "\n" + pub.description).strip()[:2200], "privacy_level": pub.privacy,
                          "disable_comment": bool(ci.get("comment_disabled")), "disable_duet": False,
                          "disable_stitch": False, "brand_content_toggle": False, "brand_organic_toggle": False},
            "source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk,
                            "total_chunk_count": count}}
    with _client() as c:
        r = c.post(f"{API}/post/publish/video/init/", headers=_h(tokens), json=body)
        d = check_response(r, "início da postagem (TikTok)")
        _api_error(d, "início da postagem (TikTok)")
        data = d.get("data") or {}
        st.update({"publish_id": data.get("publish_id"), "size": size})
        save_state(st)
        url = data.get("upload_url")
        with open(video, "rb") as f:
            for i in range(count):
                start = i * chunk
                end = size - 1 if i == count - 1 else start + chunk - 1
                f.seek(start)
                part = f.read(end - start + 1)
                r = c.put(url, content=part, headers={"Content-Type": "video/mp4", "Content-Length": str(len(part)),
                                                      "Content-Range": f"bytes {start}-{end}/{size}"},
                          timeout=httpx.Timeout(300.0, connect=15.0))
                if r.status_code not in (200, 201, 206):
                    check_response(r, "envio do vídeo (TikTok)")
    return UploadResult(remote_id=st["publish_id"], remote_status={"status": "PROCESSING_UPLOAD"}, state="processing")


def status(pub, tokens: Tokens) -> dict:
    with _client() as c:
        r = c.post(f"{API}/post/publish/status/fetch/", headers=_h(tokens), json={"publish_id": pub.remote_id})
    d = check_response(r, "status da postagem (TikTok)")
    _api_error(d, "status da postagem (TikTok)")
    data = d.get("data") or {}
    s = data.get("status")
    if s == "PUBLISH_COMPLETE":
        ids = data.get("publicaly_available_post_id") or []
        return {"state": "published", "post_id": str(ids[0]) if ids else "", "raw": data}
    if s == "FAILED":
        return {"state": "failed", "detail": data.get("fail_reason", "falhou"), "raw": data}
    return {"state": "processing", "detail": s, "raw": data}


def metrics(pub, tokens: Tokens) -> dict:
    post_id = (pub.remote_status or {}).get("post_id")
    if not post_id:
        return {}
    with _client() as c:
        r = c.post(f"{API}/video/query/", params={"fields": "id,view_count,like_count,comment_count,share_count"},
                   headers=_h(tokens), json={"filters": {"video_ids": [post_id]}})
    if r.status_code >= 400:
        return {}  # needs the video.list scope; metrics are optional
    vids = ((r.json() or {}).get("data") or {}).get("videos") or []
    return {k: v for k, v in (vids[0] if vids else {}).items() if k != "id"}
