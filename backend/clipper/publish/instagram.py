"""Instagram Reels publishing (professional accounts) - two Meta login flows:

* **Unified Login** (``App.config_id`` set; default for Meta apps created from late 2026 with
  "API do Instagram (login unificado)"): https://www.facebook.com/dialog/oauth with client_id +
  config_id + redirect_uri (permissions come from the configuration, no ``scope``); code exchanged
  at graph.facebook.com/v27.0/oauth/access_token for a Business Integration System User token;
  accounts from /me/assigned_instagram_accounts; publishing on graph.facebook.com.
  Docs: developers.facebook.com/documentation/instagram-platform/instagram-api-with-unified-login
* **Instagram Login** (no config_id): instagram.com/oauth/authorize with scopes; long-lived token
  on graph.instagram.com, refreshed with ig_refresh_token.

Common: Reels container with ``upload_type=resumable`` and the file sent to rupload.facebook.com
with the account token (no public URL of the video); container status polled; media_publish;
permalink; insights; ``content_publishing_limit`` respected. Reels are public once published,
so automatic posting needs the explicit "allow public auto" opt-in.
Status: implemented from Meta's documentation; NOT validated with a real upload yet.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode

import httpx

from .base import App, PublishError, Tokens, UploadResult, check_response, expires_in

IG_GRAPH = "https://graph.instagram.com"
FB_GRAPH = "https://graph.facebook.com"
IG_VERSION = "v23.0"
FB_VERSION = "v27.0"
IG_AUTH_URL = "https://www.instagram.com/oauth/authorize"
IG_TOKEN_URL = "https://api.instagram.com/oauth/access_token"
FB_DIALOG = "https://www.facebook.com/dialog/oauth"
RUPLOAD = "https://rupload.facebook.com/ig-api-upload"
SCOPES = ["instagram_business_basic", "instagram_business_content_publish", "instagram_business_manage_insights"]
platform = "instagram"
PRIVACY = ("public",)


def _client() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0))


def _base(tokens: Tokens) -> str:
    """Graph host + version used by this token (Unified Login tokens only work on graph.facebook.com)."""
    if tokens.extra.get("flow") == "unified":
        return f"{FB_GRAPH}/{FB_VERSION}"
    return f"{IG_GRAPH}/{IG_VERSION}"


def _version(tokens: Tokens) -> str:
    return FB_VERSION if tokens.extra.get("flow") == "unified" else IG_VERSION


# ------------------------------------------------------------------ OAuth
def auth_url(app: App, state: str, challenge: str) -> str:  # Meta flows have no PKCE; state protects CSRF
    if app.config_id:
        return FB_DIALOG + "?" + urlencode({"client_id": app.client_id, "config_id": app.config_id,
                                            "redirect_uri": app.redirect_uri, "response_type": "code",
                                            "state": state})
    return IG_AUTH_URL + "?" + urlencode({"client_id": app.client_id, "redirect_uri": app.redirect_uri,
                                          "response_type": "code", "scope": ",".join(SCOPES), "state": state,
                                          "enable_fb_login": "0", "force_authentication": "1"})


def exchange(app: App, code: str, verifier: str) -> Tokens:
    code = code.split("#")[0]
    with _client() as c:
        if app.config_id:
            params = {"client_id": app.client_id, "client_secret": app.client_secret, "code": code,
                      "redirect_uri": app.redirect_uri}
            r = c.get(f"{FB_GRAPH}/{FB_VERSION}/oauth/access_token", params=params)
            if r.status_code >= 400 and "redirect_uri" in r.text:
                # the Unified Login "Get started" exchange lists only client_id/secret/code
                params.pop("redirect_uri")
                r = c.get(f"{FB_GRAPH}/{FB_VERSION}/oauth/access_token", params=params)
            d = check_response(r, "troca do código OAuth (Meta, login unificado)")
            return Tokens(d["access_token"], "", expires_in(d.get("expires_in")) if d.get("expires_in") else None,
                          "", {"flow": "unified"})
        r = c.post(IG_TOKEN_URL, data={"client_id": app.client_id, "client_secret": app.client_secret,
                                       "grant_type": "authorization_code", "redirect_uri": app.redirect_uri,
                                       "code": code})
        short = check_response(r, "troca do código OAuth (Instagram)")
        r = c.get(f"{IG_GRAPH}/access_token", params={"grant_type": "ig_exchange_token",
                                                      "client_secret": app.client_secret,
                                                      "access_token": short["access_token"]})
        long = check_response(r, "token de longa duração (Instagram)")
    return Tokens(long["access_token"], "", expires_in(long.get("expires_in")), ",".join(SCOPES),
                  {"flow": "instagram", "user_id": str(short.get("user_id", ""))})


def refresh(app: App, tokens: Tokens) -> Tokens:
    if tokens.extra.get("flow") == "unified":
        # system-user tokens are managed by the client's business portfolio; when one stops
        # working the client must connect again (Meta's guidance) - no silent retry loop
        raise PublishError("token do login unificado expirou: conecte a conta do Instagram de novo",
                           retryable=False, auth=True)
    with _client() as c:
        r = c.get(f"{IG_GRAPH}/refresh_access_token", params={"grant_type": "ig_refresh_token",
                                                              "access_token": tokens.access_token})
    d = check_response(r, "renovação do token (Instagram)")
    return Tokens(d["access_token"], "", expires_in(d.get("expires_in")), tokens.scope, tokens.extra)


def account_info(tokens: Tokens) -> dict:
    with _client() as c:
        if tokens.extra.get("flow") == "unified":
            r = c.get(f"{FB_GRAPH}/{FB_VERSION}/me/assigned_instagram_accounts",
                      params={"fields": "id,username", "access_token": tokens.access_token})
            accts = check_response(r, "contas do Instagram compartilhadas (Meta)").get("data") or []
            if not accts:
                raise PublishError("nenhuma conta do Instagram foi compartilhada com o app na autorização "
                                   "(selecione a conta profissional na tela da Meta)", retryable=False)
            infos = [{"external_id": str(a["id"]), "name": a.get("username", ""), "handle": a.get("username", ""),
                      "meta": {"privacy_options": list(PRIVACY), "flow": "unified"},
                      "token_extra": {"user_id": str(a["id"])}} for a in accts]
            first = infos[0]
            first["additional"] = infos[1:]
            return first
        r = c.get(f"{IG_GRAPH}/{IG_VERSION}/me", params={"fields": "user_id,username,name,account_type",
                                                          "access_token": tokens.access_token})
    d = check_response(r, "dados da conta (Instagram)")
    if d.get("account_type") not in ("BUSINESS", "MEDIA_CREATOR", None):
        raise PublishError("a conta do Instagram precisa ser profissional (Empresa ou Criador)", retryable=False)
    return {"external_id": str(d.get("user_id") or d.get("id")), "name": d.get("name", ""),
            "handle": d.get("username", ""), "meta": {"privacy_options": list(PRIVACY), "flow": "instagram"}}


# ------------------------------------------------------------------ publishing
def _limit_ok(c: httpx.Client, base: str, uid: str, token: str) -> None:
    r = c.get(f"{base}/{uid}/content_publishing_limit", params={"fields": "quota_usage,config", "access_token": token})
    if r.status_code >= 400:
        return
    data = ((r.json() or {}).get("data") or [{}])[0]
    total = (data.get("config") or {}).get("quota_total")
    if total is not None and data.get("quota_usage", 0) >= total:
        raise PublishError("limite de publicações do Instagram nas últimas 24 h atingido", retryable=True,
                           retry_after=3600)


def upload(pub, tokens: Tokens, video: Path, save_state) -> UploadResult:
    st = dict(pub.upload_state or {})
    base = _base(tokens)
    uid = st.get("ig_user_id") or tokens.extra.get("user_id")
    if not uid:
        raise PublishError("conta do Instagram sem ID: reconecte a conta", retryable=False, auth=True)
    with _client() as c:
        if st.get("media_id"):
            return UploadResult(remote_id=st["media_id"], state="published")
        if not st.get("container_id"):  # created once: a restart never creates a second container
            _limit_ok(c, base, uid, tokens.access_token)
            caption = (pub.title + "\n\n" + pub.description).strip()[:2200]
            r = c.post(f"{base}/{uid}/media", data={"media_type": "REELS", "upload_type": "resumable",
                                                    "caption": caption, "share_to_feed": "true",
                                                    "access_token": tokens.access_token})
            d = check_response(r, "criação do contêiner (Instagram)")
            st.update({"container_id": d["id"], "upload_uri": d.get("uri", ""), "ig_user_id": uid,
                       "graph": base})
            save_state(st)
        if not st.get("uploaded"):
            size = video.stat().st_size
            with open(video, "rb") as f:
                r = c.post(st.get("upload_uri") or f"{RUPLOAD}/{_version(tokens)}/{st['container_id']}",
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
    base = st.get("graph") or _base(tokens)
    if not st.get("media_id") and not st.get("container_id"):
        return {"state": "failed", "detail": "publicação sem contêiner do Instagram: o envio não chegou a começar"}
    with _client() as c:
        if st.get("media_id"):
            r = c.get(f"{base}/{st['media_id']}", params={"fields": "permalink", "access_token": tokens.access_token})
            d = check_response(r, "link da publicação (Instagram)")
            return {"state": "published", "media_id": st["media_id"], "url": d.get("permalink", "")}
        r = c.get(f"{base}/{st['container_id']}", params={"fields": "status_code,status",
                                                          "access_token": tokens.access_token})
        d = check_response(r, "processamento (Instagram)")
        code = d.get("status_code")
        if code in ("ERROR", "EXPIRED"):
            return {"state": "failed", "detail": d.get("status") or code}
        if code != "FINISHED":
            return {"state": "processing", "detail": code}
        r = c.post(f"{base}/{st['ig_user_id']}/media_publish",
                   data={"creation_id": st["container_id"], "access_token": tokens.access_token})
        d = check_response(r, "publicação (Instagram)")
        return {"state": "processing", "detail": "publicado; buscando o link", "media_id": d.get("id")}


def metrics(pub, tokens: Tokens) -> dict:
    st = dict(pub.upload_state or {})
    mid = st.get("media_id")
    if not mid:
        return {}
    base = st.get("graph") or _base(tokens)
    with _client() as c:
        r = c.get(f"{base}/{mid}/insights", params={"metric": "views,reach,likes,comments,shares,saved",
                                                    "access_token": tokens.access_token})
    if r.status_code >= 400:
        return {}
    return {m["name"]: (m.get("values") or [{}])[0].get("value") for m in (r.json() or {}).get("data", [])}
