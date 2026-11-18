"""Instagram API with Instagram Login - Reels publishing (professional accounts).

* scopes: instagram_business_basic, instagram_business_content_publish
  (+ instagram_business_manage_insights for metrics);
* file transfer: resumable upload to rupload.facebook.com with the account token, so the
  video never has to be exposed on a public URL;
* Reels on Instagram are public once published - there is no private mode, so automatic
  posting to Instagram always requires the explicit "allow public auto" opt-in;
* respects ``content_publishing_limit`` (posts per 24 h).
Status: implemented against the public documentation; not validated with a real upload until
a Meta app with Instagram Login is configured and the permissions are granted.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode

import httpx

from .base import App, PublishError, Tokens, UploadResult, check_response, expires_in

GRAPH = "https://graph.instagram.com"
VERSION = "v23.0"
AUTH_URL = "https://www.instagram.com/oauth/authorize"
TOKEN_URL = "https://api.instagram.com/oauth/access_token"
RUPLOAD = "https://rupload.facebook.com/ig-api-upload"
SCOPES = ["instagram_business_basic", "instagram_business_content_publish", "instagram_business_manage_insights"]
platform = "instagram"
PRIVACY = ("public",)


def _client() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0))


def auth_url(app: App, state: str, challenge: str) -> str:  # Instagram Login has no PKCE; state still protects CSRF
    return AUTH_URL + "?" + urlencode({"client_id": app.client_id, "redirect_uri": app.redirect_uri,
                                       "response_type": "code", "scope": ",".join(SCOPES), "state": state,
                                       "enable_fb_login": "0", "force_authentication": "1"})


def exchange(app: App, code: str, verifier: str) -> Tokens:
    with _client() as c:
        r = c.post(TOKEN_URL, data={"client_id": app.client_id, "client_secret": app.client_secret,
                                    "grant_type": "authorization_code", "redirect_uri": app.redirect_uri,
                                    "code": code.split("#")[0]})
        short = check_response(r, "troca do código OAuth (Instagram)")
        r = c.get(f"{GRAPH}/access_token", params={"grant_type": "ig_exchange_token", "client_secret": app.client_secret,
                                                   "access_token": short["access_token"]})
        long = check_response(r, "token de longa duração (Instagram)")
    return Tokens(long["access_token"], "", expires_in(long.get("expires_in")), ",".join(SCOPES),
                  {"user_id": str(short.get("user_id", ""))})


def refresh(app: App, tokens: Tokens) -> Tokens:
    with _client() as c:
        r = c.get(f"{GRAPH}/refresh_access_token", params={"grant_type": "ig_refresh_token",
                                                           "access_token": tokens.access_token})
    d = check_response(r, "renovação do token (Instagram)")
    return Tokens(d["access_token"], "", expires_in(d.get("expires_in")), tokens.scope, tokens.extra)


def account_info(tokens: Tokens) -> dict:
    with _client() as c:
        r = c.get(f"{GRAPH}/{VERSION}/me", params={"fields": "user_id,username,name,account_type",
                                                    "access_token": tokens.access_token})
    d = check_response(r, "dados da conta (Instagram)")
    if d.get("account_type") not in ("BUSINESS", "MEDIA_CREATOR", None):
        raise PublishError("a conta do Instagram precisa ser profissional (Empresa ou Criador)", retryable=False)
    return {"external_id": str(d.get("user_id") or d.get("id")), "name": d.get("name", ""),
            "handle": d.get("username", ""), "meta": {"privacy_options": list(PRIVACY)}}


def _limit_ok(c: httpx.Client, uid: str, token: str) -> None:
    r = c.get(f"{GRAPH}/{VERSION}/{uid}/content_publishing_limit", params={"fields": "quota_usage,config",
                                                                         "access_token": token})
    if r.status_code >= 400:
        return
    data = ((r.json() or {}).get("data") or [{}])[0]
    total = (data.get("config") or {}).get("quota_total")
    if total is not None and data.get("quota_usage", 0) >= total:
        raise PublishError("limite de publicações do Instagram nas últimas 24 h atingido", retryable=True,
                           retry_after=3600)


def upload(pub, tokens: Tokens, video: Path, save_state) -> UploadResult:
    st = dict(pub.upload_state or {})
    acct_uid = (pub.upload_state or {}).get("ig_user_id") or tokens.extra.get("user_id")
    with _client() as c:
        if st.get("media_id"):
            return UploadResult(remote_id=st["media_id"], state="published")
        if not st.get("container_id"):
            _limit_ok(c, acct_uid, tokens.access_token)
            caption = (pub.title + "\n\n" + pub.description).strip()[:2200]
            r = c.post(f"{GRAPH}/{VERSION}/{acct_uid}/media",
                       data={"media_type": "REELS", "upload_type": "resumable", "caption": caption,
                             "share_to_feed": "true", "access_token": tokens.access_token})
            d = check_response(r, "criação do contêiner (Instagram)")
            st.update({"container_id": d["id"], "upload_uri": d.get("uri", ""), "ig_user_id": acct_uid})
            save_state(st)
        if not st.get("uploaded"):
            size = video.stat().st_size
            with open(video, "rb") as f:
                r = c.post(st.get("upload_uri") or f"{RUPLOAD}/{VERSION}/{st['container_id']}",
                           headers={"Authorization": f"OAuth {tokens.access_token}", "offset": "0",
                                    "file_size": str(size)}, content=f.read(),
                           timeout=httpx.Timeout(600.0, connect=15.0))
            check_response(r, "envio do vídeo (Instagram)")
            st["uploaded"] = True
            save_state(st)
    return UploadResult(remote_id=st["container_id"], remote_status={"container": st["container_id"]},
                        state="processing")


def status(pub, tokens: Tokens) -> dict:
    st = dict(pub.upload_state or {})
    with _client() as c:
        if st.get("media_id"):
            r = c.get(f"{GRAPH}/{VERSION}/{st['media_id']}", params={"fields": "permalink",
                                                                     "access_token": tokens.access_token})
            d = check_response(r, "link da publicação (Instagram)")
            return {"state": "published", "media_id": st["media_id"], "url": d.get("permalink", "")}
        r = c.get(f"{GRAPH}/{VERSION}/{st['container_id']}", params={"fields": "status_code,status",
                                                                     "access_token": tokens.access_token})
        d = check_response(r, "processamento (Instagram)")
        code = d.get("status_code")
        if code in ("ERROR", "EXPIRED"):
            return {"state": "failed", "detail": d.get("status") or code}
        if code != "FINISHED":
            return {"state": "processing", "detail": code}
        r = c.post(f"{GRAPH}/{VERSION}/{st['ig_user_id']}/media_publish",
                   data={"creation_id": st["container_id"], "access_token": tokens.access_token})
        d = check_response(r, "publicação (Instagram)")
        return {"state": "processing", "detail": "publicado; buscando o link", "media_id": d.get("id")}


def metrics(pub, tokens: Tokens) -> dict:
    mid = (pub.upload_state or {}).get("media_id")
    if not mid:
        return {}
    with _client() as c:
        r = c.get(f"{GRAPH}/{VERSION}/{mid}/insights", params={"metric": "views,reach,likes,comments,shares,saved",
                                                                "access_token": tokens.access_token})
    if r.status_code >= 400:
        return {}
    return {m["name"]: (m.get("values") or [{}])[0].get("value") for m in (r.json() or {}).get("data", [])}
