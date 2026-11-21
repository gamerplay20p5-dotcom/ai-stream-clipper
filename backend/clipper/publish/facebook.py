"""Facebook Reels on Facebook PAGES (never personal profiles), through Meta's official Reels Publishing API.

Docs (read 2026-11-21, Graph API v27.0): developers.facebook.com/docs/video-api/guides/reels-publishing
* Login: www.facebook.com/v27.0/dialog/oauth with scopes ``pages_show_list``, ``pages_read_engagement``,
  ``pages_manage_posts``; the code is exchanged server-side (oauth/access_token), then for a long-lived user
  token (``grant_type=fb_exchange_token``); ``/me/accounts`` lists the Pages the person manages with a Page access
  token each. Only Pages where the person has the ``CREATE_CONTENT`` task are offered. Long-lived Page tokens do
  not expire by date (they are invalidated by password changes, removed roles or revoked permissions).
* Upload: ``POST /{page-id}/video_reels`` ``upload_phase=start`` -> ``video_id``; the file goes to
  ``rupload.facebook.com/video-upload/v27.0/{video-id}`` with ``Authorization: OAuth <page token>``, ``offset`` and
  ``file_size``; ``POST /{page-id}/video_reels`` ``upload_phase=finish`` + ``video_state=PUBLISHED`` + description.
* Status: ``GET /{video-id}?fields=status`` (uploading/processing/publishing phases).
* Limits: 9:16, 3-90 s, 24-60 fps, 540x960 minimum; 30 API-published Reels per Page per 24 h (enforced here too).
  Reels on Pages are public (Meta: only the Public audience).
* Access: Advanced Access to those permissions needs Business Verification and App Review before any customer
  without a role on the Meta app can connect (see docs/PUBLICACAO_CLIENTES.md).

The ``finish`` call is the publishing step: it is recorded BEFORE it is sent (``finish_called_at``), so a lost answer
or a crash leads to reconciliation by status, never to a second publication.
Status: implemented from Meta's documentation; NOT validated with a real upload yet (disabled for customers).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import httpx

from .base import App, PublishError, Tokens, UploadResult
from .instagram import check_response

platform = "facebook"
VERSION = "v27.0"
GRAPH = f"https://graph.facebook.com/{VERSION}"
DIALOG = f"https://www.facebook.com/{VERSION}/dialog/oauth"
RUPLOAD = f"https://rupload.facebook.com/video-upload/{VERSION}"
SCOPES = ["pages_show_list", "pages_read_engagement", "pages_manage_posts"]
PRIVACY = ("public",)
MIN_DURATION, MAX_DURATION = 3.0, 90.0
DAILY_LIMIT = 30  # API-published Reels per Page per moving 24 h (Meta)
UNCERTAIN = "a Meta pode ter publicado este Reel; confira a Página antes de tentar de novo"


def _client() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(120.0, connect=15.0))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------ OAuth
def auth_url(app: App, state: str, challenge: str) -> str:  # Meta has no PKCE; the single-use state protects CSRF
    return DIALOG + "?" + urlencode({"client_id": app.client_id, "redirect_uri": app.redirect_uri, "state": state,
                                     "response_type": "code", "scope": ",".join(SCOPES)})


def exchange(app: App, code: str, verifier: str) -> Tokens:
    with _client() as c:
        r = c.get(f"{GRAPH}/oauth/access_token", params={"client_id": app.client_id, "client_secret": app.client_secret,
                                                         "redirect_uri": app.redirect_uri, "code": code.split("#")[0]})
        short = check_response(r, "troca do código OAuth (Facebook)")
        r = c.get(f"{GRAPH}/oauth/access_token", params={"grant_type": "fb_exchange_token", "client_id": app.client_id,
                                                         "client_secret": app.client_secret,
                                                         "fb_exchange_token": short["access_token"]})
        long = check_response(r, "token de longa duração (Facebook)")
    # the user token only lists the Pages; publishing uses each Page token, which has no expiry date
    return Tokens(long["access_token"], "", None, ",".join(SCOPES), {"flow": "facebook_user"})


def refresh(app: App, tokens: Tokens) -> Tokens:
    """Page tokens obtained from a long-lived user token have no expiry date: nothing to refresh. An invalidated
    token surfaces as an auth error (code 190) and the Page must be reconnected by its owner."""
    return tokens


def account_info(tokens: Tokens) -> dict:
    """Pages the person manages and may publish to; each Page becomes one destination (with its own Page token)."""
    with _client() as c:
        r = c.get(f"{GRAPH}/me/accounts", params={"fields": "id,name,access_token,tasks", "limit": 100,
                                                  "access_token": tokens.access_token})
        pages = check_response(r, "Páginas do Facebook").get("data") or []
    usable = [p for p in pages if "CREATE_CONTENT" in (p.get("tasks") or []) and p.get("access_token")]
    if not usable:
        raise PublishError("nenhuma Página do Facebook em que esta pessoa possa publicar (é preciso administrar a "
                           "Página com a tarefa de criar conteúdo; perfis pessoais não são suportados)", retryable=False)
    out = [{"external_id": p["id"], "name": p.get("name", ""), "handle": p.get("name", ""),
            "meta": {"privacy_options": list(PRIVACY), "kind": "page", "daily_limit": DAILY_LIMIT},
            # the Page token replaces the user token for this destination
            "token_extra": {"page_id": p["id"], "page_token": p["access_token"]}} for p in usable]
    return {**out[0], "additional": out[1:]}


def _page(tokens: Tokens) -> tuple[str, str]:
    page_id, token = tokens.extra.get("page_id"), tokens.extra.get("page_token")
    if not page_id or not token:
        raise PublishError("Página do Facebook sem token de Página: reconecte a Página", retryable=False, auth=True)
    return page_id, token


def _description(pub) -> str:
    tags = " ".join(f"#{t.lstrip('#')}" for t in (pub.tags or [])[:6])
    return f"{pub.description or pub.title or ''}\n\n{tags}".strip()[:2200]


# ------------------------------------------------------------------ upload and publish
def upload(pub, tokens: Tokens, video: Path, save_state) -> UploadResult:
    page_id, token = _page(tokens)
    st = dict(pub.upload_state or {})
    if st.get("finish_called_at"):
        # published (or maybe published) already: never call finish twice; the status poll decides
        return UploadResult(st["video_id"], f"https://www.facebook.com/reel/{st['video_id']}", {"phase": "finish_sent"})
    size = video.stat().st_size
    with _client() as c:
        if not st.get("video_id"):
            r = c.post(f"{GRAPH}/{page_id}/video_reels", json={"upload_phase": "start", "access_token": token})
            d = check_response(r, "início do envio (Facebook Reels)")
            st = {"video_id": d["video_id"], "page_id": page_id}
            save_state(st)
        if not st.get("uploaded"):
            with open(video, "rb") as fh:
                r = c.post(f"{RUPLOAD}/{st['video_id']}", content=fh.read(),
                           headers={"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(size),
                                    "Content-Type": "application/octet-stream"})
            d = check_response(r, "envio do vídeo (Facebook Reels)")
            if not d.get("success"):
                raise PublishError("a Meta não confirmou o recebimento do vídeo", retryable=True)
            st["uploaded"] = True
            save_state(st)
        st["finish_called_at"] = _now_iso()
        save_state(st)  # recorded BEFORE the publishing call
        try:
            r = c.post(f"{GRAPH}/{page_id}/video_reels",
                       data={"access_token": token, "video_id": st["video_id"], "upload_phase": "finish",
                             "video_state": "PUBLISHED", "description": _description(pub)})
        except (httpx.TimeoutException, httpx.TransportError):
            return UploadResult(st["video_id"], "", {"phase": "finish_unconfirmed"})
        if r.status_code >= 500:
            return UploadResult(st["video_id"], "", {"phase": "finish_unconfirmed"})
        check_response(r, "publicação (Facebook Reels)")
    return UploadResult(st["video_id"], f"https://www.facebook.com/reel/{st['video_id']}", {"phase": "finish_sent"})


def status(pub, tokens: Tokens) -> dict:
    """processing | published | failed (nothing published) | uncertain (it may exist)."""
    _, token = _page(tokens)
    st = dict(pub.upload_state or {})
    if not st.get("video_id"):
        return {"state": "failed", "failure": "before_publish", "detail": "o envio ao Facebook não chegou a começar"}
    with _client() as c:
        r = c.get(f"{GRAPH}/{st['video_id']}", params={"fields": "status", "access_token": token})
        s = (check_response(r, "status (Facebook Reels)").get("status") or {})
    video_status = s.get("video_status")
    publishing = s.get("publishing_phase") or {}
    if publishing.get("publish_status") == "published":
        return {"state": "published", "url": f"https://www.facebook.com/reel/{st['video_id']}"}
    if video_status in ("error", "upload_failed", "expired") or (s.get("processing_phase") or {}).get("status") == "error":
        failure = "rejected_by_platform" if st.get("finish_called_at") else "before_publish"
        return {"state": "failed", "failure": failure, "detail": f"o Facebook recusou o vídeo ({video_status})"}
    if publishing.get("status") == "error" or publishing.get("publish_status") == "error":
        return {"state": "failed", "failure": "rejected_by_platform", "detail": "o Facebook recusou a publicação"}
    if st.get("finish_called_at") and publishing.get("status") in (None, "not_started"):
        called = datetime.fromisoformat(st["finish_called_at"])
        if (datetime.now(timezone.utc) - called).total_seconds() > 1800:
            return {"state": "uncertain", "detail": UNCERTAIN}
    return {"state": "processing", "detail": video_status or "processando"}


def metrics(pub, tokens: Tokens) -> dict:
    """Reel insights are not wired yet (fields not validated against a real Page): nothing is invented."""
    return {}
