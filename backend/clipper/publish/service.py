"""Publishing rules: OAuth, eligibility, metadata, scheduling and idempotent publications."""

from __future__ import annotations

import logging
import re
import secrets
from collections import Counter
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import queue
from ..db import utcnow
from ..models import (
    Clip,
    EvalExample,
    OAuthState,
    Publication,
    PublishAccount,
    PublishApp,
    PublishRule,
    Review,
    Streamer,
)
from . import crypto, facebook, instagram, tiktok, youtube
from .base import App, PublishError, Tokens, pkce_pair

log = logging.getLogger(__name__)

PROVIDERS = {"youtube": youtube, "tiktok": tiktok, "instagram": instagram, "facebook": facebook}
PLATFORM_NAME = {"youtube": "YouTube Shorts", "tiktok": "TikTok", "instagram": "Instagram Reels",
                 "facebook": "Facebook Reels"}
MAX_DURATION = {"youtube": 180.0, "tiktok": 600.0, "instagram": 900.0, "facebook": 90.0}
STATE_TTL = timedelta(minutes=15)
ACTIVE = ("scheduled", "uploading", "processing", "uncertain")
# a failed publication may only be reused when it is CERTAIN nothing reached the platform
SAFE_FAILURES = ("before_publish", "rejected_by_platform", "confirmed_not_published")


def archive_attempt(pub: Publication, reason: str) -> None:
    """Keep the previous attempt as evidence (remote_status.previous_attempts + log) and invalidate
    its upload session, so the next run creates a new container. Never used on uncertain or
    published publications."""
    if pub.status in ("uncertain", "published"):
        raise ValueError("tentativa com resultado incerto ou publicada não pode ser descartada")
    prev = {"upload_state": dict(pub.upload_state or {}), "remote_id": pub.remote_id, "failure":
            (pub.remote_status or {}).get("failure"), "last_error": (pub.last_error or "")[:500],
            "archived_at": utcnow().isoformat(), "reason": reason}
    rs = dict(pub.remote_status or {})
    rs["previous_attempts"] = (list(rs.get("previous_attempts") or []) + [prev])[-10:]
    rs.pop("failure", None)
    pub.remote_status = rs
    log_event(pub, "previous_attempt", f"contêiner {prev['upload_state'].get('container_id', '-')} arquivado: {reason}")
    pub.upload_state, pub.remote_id = {}, ""


def reuse_blockers(pub: Publication) -> list[str]:
    """Why an existing publication of this clip on this account must NOT be restarted."""
    if pub.status in ("cancelled", "expired"):
        return []
    if pub.status == "uncertain":
        return ["a publicação anterior deste corte nesta conta tem resultado incerto (o vídeo pode já estar no "
                "perfil): confira e registre o resultado antes de publicar de novo"]
    if pub.status == "failed":
        kind = (pub.remote_status or {}).get("failure")
        st = pub.upload_state or {}
        if kind in SAFE_FAILURES:
            return []
        if kind is None and not st.get("publish_called_at") and not st.get("media_id"):
            return []  # older failure from before any publish attempt
        return ["a publicação anterior falhou depois de uma tentativa de publicar: confira no perfil e registre o "
                "resultado antes de publicar de novo"]
    return [f"já existe uma publicação deste corte nesta conta ({pub.status})"]


class PublishRefused(ValueError):
    """Eligibility problems (list of PT-BR explanations)."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def provider(platform: str):
    if platform not in PROVIDERS:
        raise PublishRefused([f"plataforma desconhecida: {platform}"])
    return PROVIDERS[platform]


def log_event(pub: Publication, event: str, detail: str = "") -> None:
    pub.log = (list(pub.log or []) + [{"at": utcnow().isoformat() + "Z", "event": event, "detail": detail[:500]}])[-60:]


# ------------------------------------------------------------------ apps / OAuth
def save_app(db: Session, platform: str, client_id: str, client_secret: str | None, redirect_uri: str, user: str,
             config_id: str | None = None):
    provider(platform)
    row = db.get(PublishApp, platform) or PublishApp(platform=platform)
    if client_id.strip():
        row.client_id = client_id.strip()
    if not row.client_id:
        raise PublishRefused(["informe o Client ID do app"])
    if client_secret:  # empty = keep the stored secret
        row.client_secret_enc = crypto.encrypt(client_secret.strip())
    row.redirect_uri = redirect_uri.strip()
    if config_id is not None:
        if config_id.strip() and not re.match(r"^[0-9]{5,30}$", config_id.strip()):
            raise PublishRefused(["config_id inválido (é um número, copiado da configuração do Login para Empresas)"])
        row.config_id = config_id.strip()
    row.updated_by = user
    db.merge(row)
    return row


def delete_app(db: Session, platform: str) -> None:
    connected = db.scalar(select(func.count()).select_from(PublishAccount).where(
        PublishAccount.platform == platform, PublishAccount.status == "connected"))
    if connected:
        raise PublishRefused([f"desconecte as {connected} conta(s) de {PLATFORM_NAME.get(platform, platform)} antes "
                              "de remover o app"])
    row = db.get(PublishApp, platform)
    if row is not None:
        db.delete(row)
    db.query(OAuthState).filter(OAuthState.platform == platform).delete()


def get_app(db: Session, platform: str) -> App:
    row = db.get(PublishApp, platform)
    if row is None or not row.client_id or not row.client_secret_enc or not row.redirect_uri:
        raise PublishRefused([f"configure o app OAuth de {PLATFORM_NAME.get(platform, platform)} primeiro"])
    return App(platform, row.client_id, crypto.decrypt(row.client_secret_enc), row.redirect_uri, row.config_id or "")


def start_oauth(db: Session, platform: str, user: str, return_to: str = "", owner_id: int | None = None) -> dict:
    """``owner_id`` set: a CUSTOMER connects its own destination (the account will belong to it)."""
    app = get_app(db, platform)
    if platform == "instagram" and not app.config_id:
        # apps created with "API do Instagram (login unificado)" only accept the Facebook Login for
        # Business dialog; the legacy instagram.com/oauth/authorize answers "Invalid platform app"
        raise PublishRefused(["informe o config_id da configuração do login unificado da Meta (Publicação → "
                              "Instagram) antes de conectar"])
    db.query(OAuthState).filter(OAuthState.created_at < utcnow() - STATE_TTL).delete()
    state = secrets.token_urlsafe(24)
    verifier, challenge = pkce_pair()
    db.add(OAuthState(state=state, platform=platform, created_by=user, owner_id=owner_id,
                      verifier_enc=crypto.encrypt({"v": verifier, "r": return_to[:300]})))
    return {"auth_url": provider(platform).auth_url(app, state, challenge), "state": state,
            "redirect_uri": app.redirect_uri}


def _state_data(st: OAuthState) -> dict:
    data = crypto.decrypt(st.verifier_enc)
    return data if isinstance(data, dict) else {"v": data, "r": ""}  # states created before v0.4.3


def return_to(db: Session, state: str) -> str:
    """Panel origin (scheme://host:port) that started this authorization, '' if unknown."""
    st = db.get(OAuthState, state) if state else None
    return _state_data(st).get("r", "") if st is not None else ""


def state_owner(db: Session, state: str) -> int | None:
    """Customer that started this authorization (None: the HTS)."""
    st = db.get(OAuthState, state) if state else None
    return st.owner_id if st is not None else None


def parse_redirect(text: str) -> tuple[str, str]:
    """Accept the full redirected URL (paste mode) and return (code, state)."""
    q = parse_qs(urlsplit(text.strip()).query)
    if "error" in q:
        raise PublishRefused([f"autorização negada: {q.get('error_description', q['error'])[0]}"])
    code, state = (q.get("code") or [""])[0], (q.get("state") or [""])[0]
    if not code or not state:
        raise PublishRefused(["cole o endereço completo da página para onde você foi redirecionado (com ?code=...)"])
    return code, state


def finish_oauth(db: Session, platform: str, code: str, state: str, user: str | None) -> PublishAccount:
    """``user=None``: platform redirect without the panel cookie (SameSite=strict); the single-use
    state, created by a logged-in admin minutes ago, authenticates the flow and names the admin."""
    st = db.get(OAuthState, state)
    if st is None or st.platform != platform or st.created_at < utcnow() - STATE_TTL:
        raise PublishRefused(["autorização expirada ou inválida: comece de novo em Conectar conta"])
    user = user or st.created_by
    verifier = _state_data(st)["v"]
    owner_id = st.owner_id
    db.delete(st)  # one-shot (replay protection)
    app = get_app(db, platform)
    prov = provider(platform)
    try:
        tokens = prov.exchange(app, code, verifier)
        info = prov.account_info(tokens)
    except PublishError as exc:
        raise PublishRefused([str(exc)]) from exc
    first = None
    refused: list[str] = []
    for one in [info] + list(info.get("additional") or []):  # Meta can share several accounts at once
        tk = Tokens(tokens.access_token, tokens.refresh_token, tokens.expires_at, tokens.scope,
                    {**tokens.extra, **(one.get("token_extra") or {})})
        acct = db.scalar(select(PublishAccount).where(PublishAccount.platform == platform,
                                                      PublishAccount.external_id == one["external_id"]))
        is_new = acct is None
        if owner_id is None and acct is not None and acct.owner_id is not None:
            refused.append("esta conta pertence a um cliente e não pode ser conectada pela HTS")
            continue
        if acct is None:
            acct = PublishAccount(platform=platform, external_id=one["external_id"])
        if owner_id is not None:
            from ..customer_publish import Refused, claim_connected

            try:
                claim_connected(db, owner_id, acct, is_new)
            except Refused as exc:  # beyond the plan, or someone else's account: that one is skipped
                refused.append(str(exc))
                continue
        if is_new:
            db.add(acct)
        acct.name, acct.handle, acct.meta = one.get("name", ""), one.get("handle", ""), one.get("meta") or {}
        acct.tokens_enc = crypto.encrypt(tk.as_dict())
        acct.scopes = tk.scope.split() if " " in tk.scope else [s for s in tk.scope.split(",") if s]
        acct.status, acct.last_error, acct.token_expires_at = "connected", "", tk.expires_at
        acct.connected_by, acct.connected_at = user, utcnow()
        db.flush()
        first = first or acct
    if first is None:
        raise PublishRefused(refused or ["nenhuma conta foi conectada"])
    return first


def tokens_for(db: Session, acct: PublishAccount) -> Tokens:
    """Current tokens, refreshed when about to expire (persisted encrypted)."""
    if acct.status != "connected":
        raise PublishError(f"conta {acct.name or acct.external_id} não está conectada ({acct.status})",
                           retryable=False, auth=True)
    tokens = Tokens.from_dict(crypto.decrypt(acct.tokens_enc) or {})
    if tokens.expiring():
        try:
            tokens = provider(acct.platform).refresh(get_app(db, acct.platform), tokens)
        except PublishError as exc:
            if exc.auth:
                acct.status, acct.last_error = "expired", str(exc)
            raise
        acct.tokens_enc = crypto.encrypt(tokens.as_dict())
        acct.token_expires_at = tokens.expires_at
    return tokens


# ------------------------------------------------------------------ eligibility & metadata
def _review_of_clip(db: Session, clip_id: int) -> Review | None:
    ex = db.scalar(select(EvalExample).where(EvalExample.clip_id == clip_id))
    if ex is None:
        return None
    return db.scalar(select(Review).where(Review.example_id == ex.id).order_by(Review.updated_at.desc()))


def eligibility(db: Session, clip: Clip, acct: PublishAccount, *, auto: bool, rule: PublishRule | None = None,
                privacy: str = "private", rights_confirmed: bool = False, semi: bool = False) -> list[str]:
    """``semi``: a semi-automatic proposal - a human confirms each one, so the public-auto opt-in
    does not apply (every other automatic safeguard does)."""
    """Return the reasons why this clip must NOT be published to this account (empty = OK)."""
    p: list[str] = []
    streamer = db.get(Streamer, clip.streamer_id)
    prefs = (streamer.preferences or {}) if streamer else {}
    if acct.status != "connected":
        p.append("a conta não está conectada/autorizada")
    if clip.is_test and auto:
        p.append("cortes de TESTE MANUAL nunca são publicados automaticamente")
    if clip.status == "rejected":
        p.append("corte rejeitado")
    elif clip.status not in ("ready", "approved"):
        p.append(f"corte ainda não está pronto (estado: {clip.status})")
    if not clip.video_path:
        p.append("o vídeo renderizado não existe")
    if not clip.is_test and (clip.analysis_meta is None or clip.vision_state not in ("done", "skipped")):
        p.append("análise incompleta (visão/diretor não concluídos)")
    if clip.duration > MAX_DURATION.get(acct.platform, 180):
        p.append(f"duração {clip.duration:.0f}s acima do limite de {PLATFORM_NAME[acct.platform]}")
    rv = _review_of_clip(db, clip.id)
    if rv is not None:
        if rv.rating in ("bad", "should_not_exist"):
            p.append("a avaliação humana marcou este corte como ruim/indevido")
        if "missing_context" in (rv.editorial_reasons or []):
            p.append("a avaliação humana marcou falta de contexto")
        if "duplicate" in (rv.editorial_reasons or []):
            p.append("a avaliação humana marcou como duplicado")
    # duplicate of something already published on this account (same session, overlapping time)
    for other in db.scalars(select(Publication).where(Publication.account_id == acct.id,
                                                      Publication.status.in_(ACTIVE + ("published",)),
                                                      Publication.clip_id != clip.id)):
        oc = db.get(Clip, other.clip_id) if other.clip_id else None
        if oc is not None and oc.session_id == clip.session_id and min(oc.end, clip.end) - max(oc.start, clip.start) > -20:
            p.append(f"momento muito próximo de um corte já publicado nesta conta (#{oc.id})")
            break
    options = (acct.meta or {}).get("privacy_options") or []
    if options and privacy not in options:
        p.append(f"privacidade {privacy!r} não disponível nesta conta ({', '.join(options)})")
    if auto:
        if not prefs.get("republish_allowed"):
            p.append("o streamer não está marcado com direito de republicação (Streamers → editar)")
        if rule is None or not rule.enabled:
            p.append("sem regra de publicação automática ativa")
        else:
            if rule.require_approval and clip.status != "approved":
                p.append("a regra exige aprovação humana e o corte não foi aprovado")
            if clip.score < rule.min_score:
                p.append(f"nota {clip.score:.0f} abaixo do mínimo da regra ({rule.min_score:.0f})")
            if privacy == "public" and not rule.allow_public_auto and not semi:
                p.append("postagem PÚBLICA automática exige ativação explícita na regra")
        if acct.platform == "tiktok":
            p.append("TikTok exige consentimento do criador a cada postagem: só publicação manual")
    elif not rights_confirmed and not prefs.get("republish_allowed"):
        p.append("confirme que você tem direito de republicar o conteúdo deste streamer")
    return p


def credit_line(streamer: Streamer | None) -> str:
    if streamer is None:
        return ""
    custom = ((streamer.preferences or {}).get("credit") or "").strip()
    if custom:
        return custom[:300]
    where = {"twitch": "Twitch", "youtube": "YouTube"}.get(streamer.platform, streamer.platform)
    return f"🎥 Créditos: {streamer.display_name or streamer.channel} ({where}) — {streamer.url}"


SITUATION_TAGS = {"police_chase": "perseguicao", "shootout": "tiroteio", "conflict": "treta",
                  "funny_conversation": "resenha", "scare": "susto"}
# per platform: how many hashtags read well, caption limit
PLATFORM_RULES = {"youtube": {"tags": 8, "limit": 4900}, "instagram": {"tags": 6, "limit": 2150},
                  "tiktok": {"tags": 5, "limit": 2150}}


def _story_tag(db: Session, clip: Clip) -> str:
    if not clip.situation_id:
        return ""
    from ..models import SituationRow

    row = db.get(SituationRow, clip.situation_id)
    return SITUATION_TAGS.get(row.kind, "") if row is not None and row.status != "discarded" else ""


def suggest_metadata(db: Session, clip: Clip, platform: str, rule: PublishRule | None = None) -> dict:
    """Title, caption and tags for one platform: the clip's own text (written from the streamer's
    speech and the story memory), the publishing rule's templates and the channel branding."""
    from ..settings_store import load_ai_settings

    settings = load_ai_settings(db)
    pr = PLATFORM_RULES.get(platform, PLATFORM_RULES["youtube"])
    streamer = db.get(Streamer, clip.streamer_id)
    tpl = ((rule.templates or {}).get(platform) if rule else None) or {}
    name = (streamer.display_name if streamer else "") or ""
    title = (tpl.get("title") or "{title}").replace("{title}", clip.title or "Corte").replace("{streamer}", name)
    tags = [t.lstrip("#") for t in (clip.hashtags or [])][:12]
    story = _story_tag(db, clip)
    extra = [t.lstrip("#") for t in re.split(r"[\s,]+", tpl.get("hashtags", "")) if t.strip()]
    brand = [t.lstrip("#") for t in settings.brand_hashtags if t.strip()]
    tags = list(dict.fromkeys(([story] if story else []) + tags + extra + brand))[:15]
    body = (tpl.get("description") or "{description}").replace("{description}", clip.description or "").replace(
        "{streamer}", name)
    hashtag_line = " ".join(f"#{t}" for t in tags[:pr["tags"]])
    footer = settings.brand_description_footer.strip()
    if platform == "youtube":
        title = title[:95] + (" #Shorts" if "#shorts" not in title.lower() and len(title) <= 92 else "")
        if "#Shorts" not in hashtag_line:
            hashtag_line = (hashtag_line + " #Shorts").strip()
        parts = (body.strip(), credit_line(streamer), footer, hashtag_line)
    elif platform == "tiktok":  # short caption: the hook first, the long description is not read there
        parts = (title.strip(), credit_line(streamer), hashtag_line)
    else:  # instagram: first line is what shows under the Reel
        parts = (title.strip(), body.strip(), credit_line(streamer), footer, hashtag_line)
    desc = "\n\n".join(x for x in parts if x)
    return {"title": title[:100] if platform == "youtube" else title[:150], "description": desc[:pr["limit"]],
            "tags": tags, "credit": credit_line(streamer)}


# ------------------------------------------------------------------ publications
def published_today(db: Session, acct: PublishAccount, tz: str, day: datetime | None = None) -> int:
    zone = ZoneInfo(tz)
    ref = (day or utcnow()).replace(tzinfo=ZoneInfo("UTC")).astimezone(zone)
    start = ref.replace(hour=0, minute=0, second=0, microsecond=0)
    lo = start.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    hi = lo + timedelta(days=1)
    return db.scalar(select(func.count()).select_from(Publication).where(
        Publication.account_id == acct.id, Publication.status.in_(ACTIVE + ("published",)),
        func.coalesce(Publication.scheduled_at, Publication.created_at) >= lo,
        func.coalesce(Publication.scheduled_at, Publication.created_at) < hi)) or 0


def next_slot(db: Session, acct: PublishAccount, rule: PublishRule, now: datetime | None = None) -> datetime:
    """Next calendar slot (rule.slots in local time) with free daily capacity."""
    now = now or utcnow()
    zone = ZoneInfo(rule.timezone or "America/Sao_Paulo")
    slots = sorted(s for s in (rule.slots or []) if re.match(r"^\d{2}:\d{2}$", s)) or ["12:00", "18:00", "21:00"]
    taken = Counter(p.scheduled_at for p in db.scalars(select(Publication).where(
        Publication.account_id == acct.id, Publication.status.in_(ACTIVE), Publication.scheduled_at.is_not(None))))
    per_slot = max(1, int(getattr(rule, "per_slot", 1) or 1))
    local_now = now.replace(tzinfo=ZoneInfo("UTC")).astimezone(zone)
    for day in range(0, 30):
        d = (local_now + timedelta(days=day)).date()
        for s in slots:
            hh, mm = map(int, s.split(":"))
            cand = datetime(d.year, d.month, d.day, hh, mm, tzinfo=zone).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
            if cand <= now + timedelta(minutes=2) or taken[cand] >= per_slot:
                continue
            if published_today(db, acct, rule.timezone, cand) >= rule.max_per_day:
                break  # day full -> next day
            return cand
    raise PublishRefused(["sem horário livre nos próximos 30 dias (limite diário)"])


def create_publication(db: Session, clip: Clip, acct: PublishAccount, *, origin: str, user: str,
                       rule: PublishRule | None = None, scheduled_at: datetime | None = None,
                       title: str | None = None, description: str | None = None, tags: list[str] | None = None,
                       privacy: str = "private", rights_confirmed: bool = False) -> Publication:
    """Idempotent: an existing publication of this clip on this account is returned, never duplicated."""
    existing = db.scalar(select(Publication).where(Publication.clip_id == clip.id, Publication.account_id == acct.id))
    if existing is not None and existing.status not in ("cancelled", "failed", "expired"):
        if existing.status == "uncertain":
            raise PublishRefused(reuse_blockers(existing))
        return existing
    if existing is not None:
        blockers = reuse_blockers(existing)
        if blockers:
            raise PublishRefused(blockers)
    problems = eligibility(db, clip, acct, auto=origin in ("auto", "semi"), rule=rule, privacy=privacy,
                           rights_confirmed=rights_confirmed, semi=origin == "semi")
    if problems:
        raise PublishRefused(problems)
    max_day = rule.max_per_day if rule else 20
    if origin == "manual" and scheduled_at is None and published_today(db, acct, "America/Sao_Paulo") >= max_day:
        raise PublishRefused([f"limite diário de {max_day} publicações atingido nesta conta"])
    meta = suggest_metadata(db, clip, acct.platform, rule)
    if existing is not None and (existing.upload_state or existing.remote_id):
        archive_attempt(existing, f"nova publicação criada por {user}")  # evidence kept, session invalidated
    pub = existing or Publication(clip_id=clip.id, account_id=acct.id, platform=acct.platform)
    pub.rule_id = rule.id if rule else None
    pub.origin, pub.created_by = origin, user
    pub.title = (title if title is not None else meta["title"])[:255]
    pub.description = description if description is not None else meta["description"]
    pub.tags = tags if tags is not None else meta["tags"]
    pub.privacy = privacy
    pub.scheduled_at = scheduled_at
    pub.status = "proposed" if origin == "semi" else "scheduled"  # a proposal waits for a human
    pub.last_error, pub.upload_state, pub.attempts = "", {}, 0
    pub.remote_id = pub.remote_url = ""
    pub.video_sha256, pub.video_bytes, pub.video_path = clip.sha256, clip.file_size, clip.video_path
    if existing is None:
        db.add(pub)
    try:
        db.flush()
    except IntegrityError as exc:  # concurrent creation of the same (clip, account); caller rolls back
        raise PublishRefused(["este corte já está sendo publicado nesta conta"]) from exc
    log_event(pub, "created", f"{origin} por {user}; {'agendado ' + scheduled_at.isoformat() if scheduled_at else 'imediato'}")
    return pub


def enqueue_due(db: Session) -> int:
    """Queue the upload job of publications whose time has come (idempotent per publication+attempt)."""
    from .editorial import is_paused

    if is_paused(db):
        return 0
    now = utcnow()
    from .editorial import respread_stale

    respread_stale(db, now)
    n = 0
    for pub in db.scalars(select(Publication).where(Publication.status == "scheduled",
                                                    (Publication.scheduled_at.is_(None)) | (Publication.scheduled_at <= now))):
        # a new round (re-created, admin retry) must get a new key: the queue keeps finished jobs forever
        # (the log is bounded, so the round is identified by its start time, not by counting events)
        rnd = next((e.get("at", "") for e in reversed(pub.log or []) if e.get("event") in ("created", "retry_admin")), "")
        # deferrals (rate gap, API quota) and manual re-timing change scheduled_at without spending an
        # attempt: before v0.7.3 they reused the finished job's key and the publication was never sent
        when = pub.scheduled_at.isoformat(timespec="seconds") if pub.scheduled_at else "now"
        queue.enqueue(db, "publish", {"publication_id": pub.id},
                      idempotency_key=f"publish:{pub.id}:{rnd[:26]}:{pub.attempts}:{when}",
                      priority=50, max_attempts=6)
        n += 1
    return n


def auto_scan(db: Session, lookback_hours: int = 48) -> int:
    """Create publications for eligible clips under automatic rules (never for test clips)."""
    from .editorial import is_paused

    if is_paused(db):
        return 0
    made = 0
    since = utcnow() - timedelta(hours=lookback_hours)
    # HTS rules only: customer rules are planned by clipper.customer_publish (own limits and authorization)
    for rule in db.scalars(select(PublishRule).where(PublishRule.enabled.is_(True), PublishRule.mode != "manual",
                                                     PublishRule.owner_id.is_(None))):
        acct = db.get(PublishAccount, rule.account_id)
        if acct is None or acct.status != "connected":
            continue
        q = select(Clip).where(Clip.is_test.is_(False), Clip.status.in_(("ready", "approved")), Clip.created_at >= since,
                               Clip.score >= rule.min_score)
        if rule.streamer_id:
            q = q.where(Clip.streamer_id == rule.streamer_id)
        for clip in db.scalars(q.order_by(Clip.score.desc())):
            if db.scalar(select(Publication.id).where(Publication.clip_id == clip.id, Publication.account_id == acct.id,
                                                      Publication.status != "expired")):
                continue
            try:
                when = None if rule.mode == "immediate" else next_slot(db, acct, rule)
                if rule.mode == "immediate" and published_today(db, acct, rule.timezone) >= rule.max_per_day:
                    when = next_slot(db, acct, rule)
                with db.begin_nested():
                    create_publication(db, clip, acct, origin="semi" if rule.mode == "semi" else "auto",
                                       user="auto", rule=rule, scheduled_at=when, privacy=rule.privacy)
                made += 1
            except PublishRefused:
                continue
    return made
