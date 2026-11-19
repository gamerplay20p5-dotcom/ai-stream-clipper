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

from .base import App, PublishError, Tokens, UploadResult, check_response as _generic_check, expires_in

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
CHUNK = 1024 * 1024  # upload streamed in 1 MiB pieces: memory stays flat whatever the video size
# Meta Graph error codes (error.code) -> meaning. HTTP status alone is not enough: an invalid or
# revoked token comes back as HTTP 400 with code 190, not as 401.
AUTH_CODES = {190, 102, 10, 200}  # 10/200: permission missing or removed
RATE_CODES = {4, 17, 32, 613, 80002}
TRANSIENT_CODES = {1, 2, -1}


def check_response(r, what: str) -> dict:
    """Meta-aware version of ``base.check_response``: classifies by ``error.code``/``error_subcode``."""
    if r.status_code < 400:
        return _generic_check(r, what)
    try:
        err = (r.json() or {}).get("error") or {}
    except ValueError:
        err = {}
    code, sub = err.get("code"), err.get("error_subcode")
    msg = str(err.get("error_user_msg") or err.get("message") or "")[:300]
    if code in AUTH_CODES:
        raise PublishError(f"{what}: autorização da Meta inválida, expirada ou sem permissão ({msg})",
                           retryable=False, auth=True)
    if code in RATE_CODES:
        raise PublishError(f"{what}: limite da API da Meta atingido ({msg})", retryable=True, retry_after=3600)
    if code in TRANSIENT_CODES or err.get("is_transient"):
        raise PublishError(f"{what}: erro temporário da Meta ({msg})", retryable=True)
    if code is not None:
        raise PublishError(f"{what}: a Meta recusou (código {code}{'/' + str(sub) if sub else ''}): {msg}",
                           retryable=False)
    return _generic_check(r, what)


def _file_chunks(path: Path, chunk: int = CHUNK):
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                return
            yield b


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
    if pub.privacy not in PRIVACY:  # defence in depth: eligibility already refuses it
        raise PublishError(f"privacidade '{pub.privacy}' não existe no Instagram (Reels são públicos)", retryable=False)
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
            caption = _caption(pub)
            r = c.post(f"{base}/{uid}/media", data={"media_type": "REELS", "upload_type": "resumable",
                                                    "caption": caption, "share_to_feed": "true",
                                                    "access_token": tokens.access_token})
            d = check_response(r, "criação do contêiner (Instagram)")
            st.update({"container_id": d["id"], "upload_uri": d.get("uri", ""), "ig_user_id": uid,
                       "graph": base})
            save_state(st)
        if not st.get("uploaded"):
            # streamed in chunks (never the whole file in memory): the publisher worker has 1 GB of RAM. A retry restarts at
            # offset 0 on the SAME container (Meta accepts re-sending the whole file).
            size = video.stat().st_size
            r = c.post(st.get("upload_uri") or f"{RUPLOAD}/{_version(tokens)}/{st['container_id']}",
                       headers={"Authorization": f"OAuth {tokens.access_token}", "offset": "0",
                                "file_size": str(size), "Content-Length": str(size)},
                       content=_file_chunks(video), timeout=httpx.Timeout(600.0, connect=15.0))
            check_response(r, "envio do vídeo (Instagram)")
            st["uploaded"] = True
            save_state(st)
    return UploadResult(remote_id=st["container_id"], remote_status={"container": st["container_id"]},
                        state="processing")


UNCERTAIN = ("o resultado da publicação no Instagram é incerto: o Reel pode já estar no perfil. Nada será "
             "enviado de novo até a conferência (automática ou sua)")
RETRY_PUBLISH_AFTER_S = 600  # container still FINISHED this long after an unanswered media_publish -> not published


def _caption(pub) -> str:
    return (pub.title + "\n\n" + pub.description).strip()[:2200]


def status(pub, tokens: Tokens, save_state=None) -> dict:
    """Container status -> media_publish -> link. Outcomes: processing | published | failed (nothing
    published: before publishing, or refused by Meta) | uncertain (the Reel may exist).

    ``media_publish`` is called at most once per attempt and the attempt is SAVED BEFORE the call
    (``publish_called_at``): if the answer is lost (timeout, 5xx, crash) the next poll reconciles
    instead of publishing again."""
    st = dict(pub.upload_state or {})
    base = st.get("graph") or _base(tokens)
    if not st.get("media_id") and not st.get("container_id"):
        return {"state": "failed", "failure": "before_publish",
                "detail": "publicação sem contêiner do Instagram: o envio não chegou a começar"}
    with _client() as c:
        if st.get("media_id"):
            r = c.get(f"{base}/{st['media_id']}", params={"fields": "permalink", "access_token": tokens.access_token})
            d = check_response(r, "link da publicação (Instagram)")
            return {"state": "published", "media_id": st["media_id"], "url": d.get("permalink", "")}
        r = c.get(f"{base}/{st['container_id']}", params={"fields": "status_code,status",
                                                          "access_token": tokens.access_token})
        d = check_response(r, "processamento (Instagram)")
        code = d.get("status_code")
        called = st.get("publish_called_at")
        if code in ("ERROR", "EXPIRED"):  # e.g. video rejected: Meta confirms nothing was published
            return {"state": "failed", "failure": "rejected_by_platform",
                    "detail": f"o Instagram recusou o vídeo: {d.get('status') or code}"}
        if code == "PUBLISHED" or called:
            if code == "FINISHED" and called and _age(called) >= RETRY_PUBLISH_AFTER_S:
                # the container is still unpublished long after the unanswered call: Meta did NOT
                # publish it -> a new attempt is safe (recorded again before calling)
                st.pop("publish_called_at", None)
                if save_state:
                    save_state(st)
                return {"state": "processing", "detail": "a publicação anterior não se concretizou; nova tentativa"}
            if code == "FINISHED":
                return {"state": "processing", "detail": "aguardando a confirmação da publicação"}
            # PUBLISHED: never call media_publish again - reconcile with strong evidence only
            mid, why = _find_published(c, base, st.get("ig_user_id"), pub, tokens, called)
            if mid:
                return {"state": "processing", "detail": "publicado; recuperando o link", "media_id": mid,
                        "reconciled": why}
            return {"state": "uncertain", "detail": f"{UNCERTAIN} ({why})"}
        if code != "FINISHED":
            return {"state": "processing", "detail": code}
        st["publish_called_at"] = _now_iso()
        if save_state:
            save_state(st)  # committed BEFORE the call
        try:
            r = c.post(f"{base}/{st['ig_user_id']}/media_publish",
                       data={"creation_id": st["container_id"], "access_token": tokens.access_token})
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            return {"state": "uncertain", "detail": f"{UNCERTAIN} (sem resposta da Meta: {type(exc).__name__})"}
        if r.status_code >= 500:
            return {"state": "uncertain", "detail": f"{UNCERTAIN} (erro {r.status_code} da Meta ao publicar)"}
        try:
            d = check_response(r, "publicação (Instagram)")
        except PublishError:
            # a clear 4xx refusal (permission, media not ready...): Meta did not publish it
            st.pop("publish_called_at", None)
            if save_state:
                save_state(st)
            raise
        if not d.get("id"):
            return {"state": "uncertain", "detail": f"{UNCERTAIN} (resposta sem ID)"}
        return {"state": "processing", "detail": "publicado; buscando o link", "media_id": d.get("id")}


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _age(iso: str) -> float:
    from datetime import datetime, timezone

    try:
        t = datetime.fromisoformat(iso)
    except ValueError:
        return 0.0
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - t).total_seconds()


def _find_published(c: httpx.Client, base: str, uid: str | None, pub, tokens: Tokens,
                    called_at: str | None) -> tuple[str | None, str]:
    """Identify OUR Reel among the account's recent media. Meta does not link a media to its
    container, so ALL of these must hold, for exactly ONE media:
      * product type REELS;
      * the FULL caption equals the one we sent (not a prefix);
      * published inside the window of our recorded publish attempt (-2 min .. +30 min).
    Zero or several candidates -> (None, reason): the caller marks the result uncertain."""
    from datetime import datetime, timedelta, timezone

    if not uid:
        return None, "conta sem ID"
    if not called_at:
        return None, "não há registro do horário da tentativa de publicação"
    try:
        t0 = datetime.fromisoformat(called_at)
    except ValueError:
        return None, "registro da tentativa inválido"
    t0 = t0 if t0.tzinfo else t0.replace(tzinfo=timezone.utc)
    lo, hi = t0 - timedelta(minutes=2), t0 + timedelta(minutes=30)
    r = c.get(f"{base}/{uid}/media", params={"fields": "id,caption,timestamp,media_product_type", "limit": 50,
                                            "access_token": tokens.access_token})
    if r.status_code >= 400:
        return None, f"lista de mídias indisponível (HTTP {r.status_code})"
    want = _caption(pub)
    hits = []
    for m in (r.json() or {}).get("data") or []:
        if m.get("media_product_type") not in (None, "REELS") or (m.get("caption") or "").strip() != want:
            continue
        try:
            ts = datetime.strptime(m.get("timestamp", ""), "%Y-%m-%dT%H:%M:%S%z")
        except ValueError:
            continue
        if lo <= ts <= hi and m.get("media_product_type") == "REELS":
            hits.append(str(m["id"]))
    if len(hits) == 1:
        return hits[0], "legenda completa, tipo Reel e horário da tentativa conferem"
    if not hits:
        return None, "nenhum Reel com a mesma legenda no horário da tentativa"
    return None, f"{len(hits)} Reels possíveis: identificação ambígua"


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
