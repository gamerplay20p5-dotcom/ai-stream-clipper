"""Automatic publishing for customers (Creator and Pro) on top of the HTS publisher (``clipper.publish``).

Multi-tenant rules, all enforced here and never taken from the browser:
* every destination (``PublishAccount``), rule (``PublishRule``) and publication (``Publication``) of a customer has
  ``owner_id`` = that account; the HTS keeps its own resources (``owner_id`` NULL) and its panel never lists these;
* a customer only publishes Shorts that are in ITS library (an active ``ClipShare`` from one of its monitors), and
  sends exactly the delivered media snapshot;
* plan limits come from the entitlement (signed snapshot or HTS courtesy): ``autoPublish``, ``publishDestinations``
  (connected accounts or Pages) and ``publishDaily`` (TOTAL per day across all destinations);
* automatic publishing starts OFF; the customer turns it on, accepts the terms, and declares per channel that it
  holds the rights to republish it. The HTS can block a customer or a streamer at any time;
* only platforms homologated for customers are offered (``customer_publish_platforms``, YouTube by default).

Daily allowance (``PublishUnit``): one unit per publication, reserved when the upload is about to start, under the
account lock (two workers can never take the last slot twice), never counted twice (unique per publication),
``consumed`` once something may have been published, ``released`` when nothing was (permanent failure before
publishing, cancellation, refusal, an abandoned reservation). The day is the calendar day in the customer's timezone.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import entitlements
from .db import utcnow
from .models import (AccountSubscription, AppSetting, ChannelMonitor, Clip, ClipShare, CustomerPublishSettings,
                     Publication, PublishAccount, PublishRule, PublishUnit, Streamer, User)

log = logging.getLogger(__name__)

PLATFORMS_KEY = "customer_publish_platforms"
DEFAULT_PLATFORMS = ("youtube",)  # the only platform with a real upload validated (private) so far
KNOWN_PLATFORMS = ("youtube", "instagram", "tiktok", "facebook")
NEVER_AUTOMATIC = {"tiktok": "o TikTok exige o consentimento do criador a cada postagem: sem publicação automática"}
MIN_DURATION = {"facebook": 3.0}
RETENTION_DAYS = 7  # a Short waiting for a free slot leaves the distribution queue after this
ABANDONED_AFTER = timedelta(hours=6)
PLAN_PAUSE = "fora do limite de destinos do plano"
OPEN = ("proposed", "waiting", "scheduled", "uploading", "processing", "uncertain")
SLOT_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class Refused(ValueError):
    pass


# ------------------------------------------------------------------ configuration
def enabled_platforms(db: Session) -> list[str]:
    row = db.get(AppSetting, PLATFORMS_KEY)
    value = (row.value or {}).get("platforms") if row and isinstance(row.value, dict) else None
    return [p for p in (value if isinstance(value, list) else DEFAULT_PLATFORMS) if p in KNOWN_PLATFORMS]


def set_enabled_platforms(db: Session, platforms: list[str]) -> list[str]:
    clean = [p for p in dict.fromkeys(platforms) if p in KNOWN_PLATFORMS and p not in NEVER_AUTOMATIC]
    row = db.get(AppSetting, PLATFORMS_KEY)
    if row is None:
        db.add(AppSetting(key=PLATFORMS_KEY, value={"platforms": clean}))
    else:
        row.value = {"platforms": clean}
    return clean


def validate_timezone(name: str) -> str:
    name = (name or "").strip()
    if name not in available_timezones():
        raise Refused("fuso horário inválido (use um nome IANA, por exemplo America/Sao_Paulo)")
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise Refused("fuso horário inválido") from exc
    return name


def settings_for(db: Session, user_id: int, create: bool = False) -> CustomerPublishSettings | None:
    row = db.get(CustomerPublishSettings, user_id)
    if row is None and create:
        row = CustomerPublishSettings(user_id=user_id, enabled=False)
        db.add(row)
        db.flush()
    return row


def local_day(tz: str, when: datetime | None = None) -> str:
    """Calendar day in the customer's timezone (DST-safe: computed from UTC by zoneinfo)."""
    moment = (when or utcnow()).replace(tzinfo=dt_timezone.utc).astimezone(ZoneInfo(tz))
    return moment.date().isoformat()


def plan_limits(ent: entitlements.Entitlement | None) -> dict:
    f = (ent.limits.get("features") or {}) if ent else {}
    return {"allowed": bool(f.get("autoPublish")), "destinations": int(f.get("publishDestinations") or 0),
            "daily": int(f.get("publishDaily") or 0)}


# ------------------------------------------------------------------ gate (may this customer publish now?)
def gate(db: Session, user_id: int) -> tuple[str, str]:
    """('ok', '') | ('hold', reason): wait, nothing is lost | ('off', reason): nothing new starts."""
    user = db.get(User, user_id)
    if user is None or not user.active:
        # deactivated by the HTS (security, abuse) or deleted: never a new upload, whatever the plan or switch
        return "off", "conta desativada"
    s = settings_for(db, user_id)
    if s is not None and s.blocked_reason:
        return "off", f"publicação bloqueada pela HTS: {s.blocked_reason}"
    ent = entitlements.current(db, user_id)
    if ent is None:
        return "off", "assinatura inativa"
    if not plan_limits(ent)["allowed"]:
        return "off", "o plano não inclui publicação automática"
    sub = db.get(AccountSubscription, ent.subscription_id)
    if sub is not None and sub.review:
        return "hold", "pagamento em revisão: as publicações aguardam a conclusão"
    if s is None or not s.enabled:
        return "hold", "publicação automática pausada"
    if s.terms_accepted_at is None:
        return "off", "autorização de publicação ainda não aceita"
    return "ok", ""


# ------------------------------------------------------------------ destinations
def destinations(db: Session, user_id: int) -> list[PublishAccount]:
    return list(db.scalars(select(PublishAccount).where(PublishAccount.owner_id == user_id,
                                                        PublishAccount.status != "revoked")
                           .order_by(PublishAccount.connected_at, PublishAccount.id)))


def enforce_destinations(db: Session, user_id: int) -> None:
    """Downgrade/upgrade/expiry: destinations beyond the plan are paused (the oldest stay), and come back."""
    limit = plan_limits(entitlements.current(db, user_id))["destinations"]
    for position, acct in enumerate(destinations(db, user_id)):
        if position >= limit:
            acct.paused_reason = PLAN_PAUSE
        elif acct.paused_reason == PLAN_PAUSE:
            acct.paused_reason = ""


def can_connect(db: Session, user_id: int, platform: str) -> None:
    ent = entitlements.current(db, user_id)
    limits = plan_limits(ent)
    if not limits["allowed"]:
        raise Refused("publicação automática disponível nos planos Creator e Pro")
    if platform not in enabled_platforms(db):
        raise Refused("esta rede ainda não está disponível para publicação automática")
    if len(destinations(db, user_id)) >= limits["destinations"]:
        raise Refused(f"o plano permite {limits['destinations']} destino(s) conectado(s)")


def claim_connected(db: Session, user_id: int, acct: PublishAccount, is_new: bool) -> None:
    """Called by the OAuth finish for a customer state. A destination belongs to one owner only: an account the HTS
    or another customer already connected is refused without saying whose it is."""
    if not is_new and acct.owner_id != user_id:
        raise Refused("esta conta já está conectada a outro usuário do AI Stream Clipper")
    if acct.platform not in enabled_platforms(db):
        raise Refused("esta rede ainda não está disponível para publicação automática")
    limit = plan_limits(entitlements.current(db, user_id))["destinations"]
    others = [a for a in destinations(db, user_id) if a.id != acct.id]
    if acct.owner_id != user_id and len(others) >= limit:
        raise Refused(f"o plano permite {limit} destino(s) conectado(s)")
    acct.owner_id = user_id


def disconnect(db: Session, user_id: int, acct: PublishAccount) -> int:
    """Tokens erased; open publications to it cancelled (their units released)."""
    acct.tokens_enc, acct.status = "", "revoked"
    n = 0
    for p in db.scalars(select(Publication).where(Publication.account_id == acct.id, Publication.status.in_(
            ("proposed", "waiting", "scheduled")))):
        cancel(db, p, "destino desconectado")
        n += 1
    return n


# ------------------------------------------------------------------ eligibility
def following(db: Session, user_id: int, streamer_id: int) -> bool:
    return db.scalar(select(ChannelMonitor.id).where(ChannelMonitor.user_id == user_id, ChannelMonitor.streamer_id == streamer_id,
                                                     ChannelMonitor.removed_at.is_(None))) is not None


def on_channel_removed(db: Session, user_id: int, streamer_id: int) -> int:
    """The customer stopped following a channel: its publications that did not start are cancelled (units
    released) and its rules are switched off. Uploads already started finish; Shorts in the library stay."""
    n = 0
    clips = select(Clip.id).where(Clip.streamer_id == streamer_id)
    for pub in db.scalars(select(Publication).where(Publication.owner_id == user_id, Publication.clip_id.in_(clips),
                                                    Publication.status.in_(("proposed", "waiting", "scheduled")))):
        if not upload_started(pub):
            cancel(db, pub, "canal removido dos seus canais acompanhados")
            n += 1
    for rule in db.scalars(select(PublishRule).where(PublishRule.owner_id == user_id, PublishRule.streamer_id == streamer_id)):
        rule.enabled = False
    db.flush()
    return n


def library_share(db: Session, user_id: int, clip_id: int) -> ClipShare | None:
    from .sharing import active_share

    share = active_share(db, user_id, clip_id)
    return share if share is not None and share.monitor_id is not None else None


def problems(db: Session, pub_or_none, *, user_id: int, clip: Clip | None, acct: PublishAccount | None,
             rule: PublishRule | None) -> list[str]:
    """Why this Short must not go to this destination now (permanent reasons; the gate covers plan/pause)."""
    from .publish.service import MAX_DURATION, PLATFORM_NAME

    p: list[str] = []
    if clip is None:
        return ["o Short não existe mais"]
    if acct is None or acct.owner_id != user_id:
        return ["destino não encontrado"]
    if acct.status != "connected":
        p.append("a rede foi desconectada ou a autorização expirou: conecte de novo")
    if acct.paused_reason:
        p.append(acct.paused_reason)
    if acct.platform not in enabled_platforms(db):
        p.append("esta rede não está disponível para publicação automática")
    if acct.platform in NEVER_AUTOMATIC:
        p.append(NEVER_AUTOMATIC[acct.platform])
    share = library_share(db, user_id, clip.id)
    if share is None:
        p.append("o Short não está (mais) na sua biblioteca")
    elif not following(db, user_id, clip.streamer_id):
        p.append("você deixou de acompanhar este canal: a publicação automática dele parou")
    if clip.status not in ("ready", "approved") or clip.is_test:
        p.append("o Short não está disponível")
    if rule is None or rule.owner_id != user_id or not rule.enabled:
        p.append("sem regra de publicação ativa para este canal")
    else:
        if rule.rights_confirmed_at is None:
            p.append("confirme que você tem direito de republicar este canal")
        streamer = db.get(Streamer, clip.streamer_id)
        if streamer is None or (streamer.preferences or {}).get("customer_publish_blocked"):
            p.append("a republicação deste canal foi bloqueada pela HTS")
        if streamer is not None and rule.streamer_id != streamer.id:
            p.append("o Short não é do canal desta regra")
        options = (acct.meta or {}).get("privacy_options") or []
        if options and rule.privacy not in options:
            p.append(f"privacidade '{rule.privacy}' não disponível nesta rede ({', '.join(options)})")
    if clip.duration > MAX_DURATION.get(acct.platform, 180):
        p.append(f"duração acima do limite de {PLATFORM_NAME.get(acct.platform, acct.platform)}")
    if clip.duration < MIN_DURATION.get(acct.platform, 0):
        p.append(f"duração abaixo do mínimo de {PLATFORM_NAME.get(acct.platform, acct.platform)}")
    return p


# ------------------------------------------------------------------ daily allowance
def units_used(db: Session, user_id: int, day: str) -> int:
    return int(db.scalar(select(func.count()).select_from(PublishUnit).where(
        PublishUnit.user_id == user_id, PublishUnit.day == day, PublishUnit.state.in_(("reserved", "consumed")))) or 0)


def reserve(db: Session, pub: Publication) -> str:
    """'' when a unit is held for this publication (new or already held), else why not. Transactional: the
    customer's subscription rows are locked first, so concurrent workers serialize on the last slot."""
    uid = pub.owner_id
    entitlements.lock_accounts(db, [uid])
    unit = db.scalar(select(PublishUnit).where(PublishUnit.publication_id == pub.id))
    if unit is not None and unit.state in ("reserved", "consumed"):
        return ""  # a retry, a restart or a duplicate job: the unit was already counted
    limits = plan_limits(entitlements.current(db, uid))
    s = settings_for(db, uid)
    day = local_day(s.timezone if s else "America/Sao_Paulo")
    if units_used(db, uid, day) >= limits["daily"]:
        return f"limite diário de {limits['daily']} publicações atingido"
    try:
        with db.begin_nested():
            now = utcnow()
            if unit is None:
                unit = PublishUnit(user_id=uid, publication_id=pub.id, day=day, state="reserved", reserved_at=now)
                db.add(unit)
            else:  # a released unit reused: a NEW reservation, with its own moment (the cleaner measures from it)
                unit.day, unit.state, unit.reason, unit.reserved_at = day, "reserved", "", now
            db.flush()
    except IntegrityError:
        return "" if db.scalar(select(PublishUnit.id).where(PublishUnit.publication_id == pub.id,
                                                            PublishUnit.state != "released")) else "concorrência"
    # Second barrier: recount after the insert (SQLite has no row locks)
    if units_used(db, uid, day) > limits["daily"]:
        unit.state, unit.reason = "released", "vaga tomada por outra publicação"
        db.flush()
        return f"limite diário de {limits['daily']} publicações atingido"
    return ""


def _unit(db: Session, pub: Publication) -> PublishUnit | None:
    return db.scalar(select(PublishUnit).where(PublishUnit.publication_id == pub.id))


def settle(db: Session, pub: Publication) -> None:
    """Unit state follows the publication: something may be online -> consumed; nothing was published -> released;
    still pending (a retry) -> kept."""
    unit = _unit(db, pub)
    if unit is None:
        return
    from .publish.service import SAFE_FAILURES

    failure = (pub.remote_status or {}).get("failure")
    if pub.status in ("uploading", "processing", "published", "uncertain"):
        unit.state = "consumed"
    elif pub.status in ("cancelled", "expired", "waiting", "proposed") or (pub.status == "failed" and failure in SAFE_FAILURES):
        if unit.state == "reserved":
            unit.state, unit.reason = "released", pub.last_error[:200] or pub.status
    elif pub.status == "failed":
        unit.state = "consumed"  # an unclear failure may have published: never given back


def upload_started(pub: Publication | None) -> bool:
    """Something may already have reached the platform: an attempt was made, a session or a remote id exists."""
    if pub is None:
        return False
    return bool(pub.status in ("uploading", "processing", "published", "uncertain") or pub.attempts
                or pub.upload_state or pub.remote_id)


def release_abandoned(db: Session, now: datetime | None = None) -> int:
    """Reservations whose upload never started (the worker died between reserving and sending) give their slot
    back after ``ABANDONED_AFTER``, measured from THAT reservation (``reserved_at``). Decided under the account lock,
    re-reading the unit, so a reservation made meanwhile by a worker is never released. An upload started or
    uncertain is never released here (``settle`` decides it from the publication's outcome)."""
    now = now or utcnow()
    cutoff = now - ABANDONED_AFTER
    candidates = db.execute(select(PublishUnit.id, PublishUnit.user_id).where(
        PublishUnit.state == "reserved", PublishUnit.reserved_at < cutoff)).all()
    n = 0
    for unit_id, uid in candidates:
        entitlements.lock_accounts(db, [uid])
        unit = db.get(PublishUnit, unit_id)
        db.refresh(unit)  # the lock is held now: read the latest committed reservation
        if unit.state != "reserved" or unit.reserved_at >= cutoff:
            continue  # reserved again meanwhile
        pub = db.get(Publication, unit.publication_id)
        if pub is not None:
            db.refresh(pub)
        if upload_started(pub):
            settle(db, pub)  # consumed if it may be online; kept while a retry of a real attempt is pending
            continue
        unit.state, unit.reason = "released", "reserva abandonada"
        n += 1
    db.flush()
    return n


# ------------------------------------------------------------------ queue
def cancel(db: Session, pub: Publication, reason: str) -> None:
    if pub.status in OPEN and pub.status not in ("uploading", "processing", "uncertain"):
        pub.status, pub.last_error = "cancelled", reason[:2000]
        from .publish.service import log_event

        log_event(pub, "cancelled", reason)
        settle(db, pub)


def _slots(rule: PublishRule) -> list[str]:
    return sorted(s for s in (rule.slots or []) if SLOT_RE.match(s)) or ["12:00", "18:00", "21:00"]


def deadline(pub: Publication) -> datetime:
    """Last moment this Short may still be posted automatically (the same 7 days for proposed, waiting and scheduled)."""
    return pub.created_at + timedelta(days=RETENTION_DAYS)


def expire_overdue(db: Session, user_id: int | None = None, now: datetime | None = None) -> int:
    """Publications not started by their deadline leave the distribution queue (``expired``; unit released).
    The Short itself stays in the customer's library. An upload already started is never expired here."""
    now = now or utcnow()
    q = select(Publication).where(Publication.owner_id.is_not(None), Publication.status.in_(("proposed", "waiting", "scheduled")),
                                  Publication.created_at < now - timedelta(days=RETENTION_DAYS))
    if user_id is not None:
        q = q.where(Publication.owner_id == user_id)
    n = 0
    for pub in db.scalars(q):
        if upload_started(pub):
            continue  # a retry of a real attempt finishes its course
        from .publish.service import log_event

        pub.status, pub.scheduled_at = "expired", None
        pub.last_error = f"não foi publicado dentro do prazo de {RETENTION_DAYS} dias da distribuição"
        log_event(pub, "expired", pub.last_error)
        settle(db, pub)
        n += 1
    db.flush()
    return n


def plan_waiting(db: Session, user_id: int, now: datetime | None = None) -> int:
    """Give the waiting Shorts (best first) the next free preferred slots, without planning more per day than the
    plan's daily total (already used + already scheduled that day), and never after their deadline: a Short that
    cannot fit before it stays waiting and expires."""
    db.flush()  # sessions use autoflush=False: approvals/cancellations made just before must be visible
    now = now or utcnow()
    expire_overdue(db, user_id, now)
    s = settings_for(db, user_id)
    tz = s.timezone if s else "America/Sao_Paulo"
    zone = ZoneInfo(tz)
    daily = plan_limits(entitlements.current(db, user_id))["daily"]
    waiting = list(db.scalars(select(Publication).where(Publication.owner_id == user_id, Publication.status == "waiting")))
    if not waiting or daily <= 0:
        return 0
    clips = {c.id: c for c in db.scalars(select(Clip).where(Clip.id.in_([p.clip_id for p in waiting if p.clip_id])))}
    waiting.sort(key=lambda p: -(clips[p.clip_id].score if p.clip_id in clips else 0))
    scheduled = list(db.scalars(select(Publication).where(Publication.owner_id == user_id,
                                                          Publication.status == "scheduled",
                                                          Publication.scheduled_at.is_not(None))))
    per_day: dict[str, int] = {}
    for p in scheduled:
        d = local_day(tz, p.scheduled_at)
        per_day[d] = per_day.get(d, 0) + 1
    taken = {(p.account_id, p.scheduled_at) for p in scheduled}
    local_now = now.replace(tzinfo=dt_timezone.utc).astimezone(zone)
    planned = 0
    for pub in waiting:
        rule = db.get(PublishRule, pub.rule_id) if pub.rule_id else None
        slots = _slots(rule) if rule else ["12:00", "18:00", "21:00"]
        done = False
        for offset in range(0, 14):
            d = (local_now + timedelta(days=offset)).date()
            key = d.isoformat()
            if per_day.get(key, 0) + (units_used(db, user_id, key) if offset == 0 else 0) >= daily:
                continue
            for slot in slots:
                hh, mm = map(int, slot.split(":"))
                when = datetime(d.year, d.month, d.day, hh, mm, tzinfo=zone).astimezone(dt_timezone.utc).replace(tzinfo=None)
                if when <= now + timedelta(minutes=2) or (pub.account_id, when) in taken:
                    continue
                if when > deadline(pub):
                    break  # never scheduled past the retention deadline
                pub.status, pub.scheduled_at, pub.last_error = "scheduled", when, ""
                per_day[key] = per_day.get(key, 0) + 1
                taken.add((pub.account_id, when))
                planned += 1
                done = True
                break
            if done:
                break
    return planned


def create(db: Session, user_id: int, clip: Clip, acct: PublishAccount, rule: PublishRule) -> Publication | None:
    """One Short to one destination of this customer (idempotent: never twice on the same destination)."""
    from .publish.service import log_event, suggest_metadata

    if db.scalar(select(Publication.id).where(Publication.clip_id == clip.id, Publication.account_id == acct.id)):
        return None
    if problems(db, None, user_id=user_id, clip=clip, acct=acct, rule=rule):
        return None
    share = library_share(db, user_id, clip.id)
    meta = suggest_metadata(db, clip, acct.platform, rule)
    pub = Publication(clip_id=clip.id, account_id=acct.id, rule_id=rule.id, platform=acct.platform, owner_id=user_id,
                      origin="auto", created_by=f"customer:{user_id}", title=meta["title"][:255],
                      description=meta["description"], tags=meta["tags"], privacy=rule.privacy,
                      status="proposed" if rule.require_approval else "waiting",
                      video_path=share.video_path or clip.video_path, video_sha256=share.video_sha256 or clip.sha256,
                      video_bytes=share.video_bytes or clip.file_size)
    try:
        with db.begin_nested():
            db.add(pub)
            db.flush()
    except IntegrityError:
        return None  # created concurrently (unique clip+destination)
    log_event(pub, "created", "automática (cliente)" + (": aguarda sua aprovação" if rule.require_approval else ""))
    return pub


def scan_customer(db: Session, user_id: int, now: datetime | None = None) -> int:
    """New Shorts of the customer's library -> its destinations, per its rules. Nothing while the gate is closed."""
    state, _ = gate(db, user_id)
    if state != "ok":
        return 0
    now = now or utcnow()
    made = 0
    since = now - timedelta(days=RETENTION_DAYS)
    for rule in db.scalars(select(PublishRule).where(PublishRule.owner_id == user_id, PublishRule.enabled.is_(True))):
        acct = db.get(PublishAccount, rule.account_id)
        if acct is None or acct.owner_id != user_id or acct.status != "connected" or acct.paused_reason:
            continue
        # only a channel the customer STILL follows: a removed monitor never produces new publications
        monitors = select(ChannelMonitor.id).where(ChannelMonitor.user_id == user_id, ChannelMonitor.streamer_id == rule.streamer_id,
                                                   ChannelMonitor.removed_at.is_(None))
        rows = db.execute(select(ClipShare, Clip).join(Clip, Clip.id == ClipShare.clip_id).where(
            ClipShare.user_id == user_id, ClipShare.revoked_at.is_(None), ClipShare.monitor_id.in_(monitors),
            ClipShare.created_at >= since, Clip.score >= rule.min_score).order_by(Clip.score.desc())).all()
        for _, clip in rows:
            if create(db, user_id, clip, acct, rule) is not None:
                made += 1
    plan_waiting(db, user_id, now)
    return made


def tick(db: Session) -> dict:
    """Publisher worker tick for customers (runs next to the HTS scan)."""
    made = planned = 0
    release_abandoned(db)
    expire_overdue(db)  # every customer, also the paused ones
    for (uid,) in db.execute(select(CustomerPublishSettings.user_id).where(CustomerPublishSettings.enabled.is_(True))):
        made += scan_customer(db, uid)
    return {"created": made, "planned": planned}


# ------------------------------------------------------------------ worker hooks (publish.runner)
def before_upload(db: Session, pub: Publication) -> tuple[str, str]:
    """('go', '') | ('defer', reason): back to the waiting queue | ('refuse', reason): permanent."""
    state, reason = gate(db, pub.owner_id)
    if state != "ok":
        return "defer", reason
    clip = db.get(Clip, pub.clip_id) if pub.clip_id else None
    acct = db.get(PublishAccount, pub.account_id)
    rule = db.get(PublishRule, pub.rule_id) if pub.rule_id else None
    if utcnow() > deadline(pub) and not upload_started(pub):
        return "expire", f"não foi publicado dentro do prazo de {RETENTION_DAYS} dias da distribuição"
    found = problems(db, pub, user_id=pub.owner_id, clip=clip, acct=acct, rule=rule)
    if found:
        return "refuse", "; ".join(found)
    why = reserve(db, pub)
    if why:
        return "defer", why
    return "go", ""


def defer(db: Session, pub: Publication, reason: str) -> None:
    from .publish.service import log_event

    pub.status, pub.scheduled_at, pub.last_error = "waiting", None, reason[:2000]
    log_event(pub, "waiting", reason)
    settle(db, pub)


# ------------------------------------------------------------------ views
def summary(db: Session, user_id: int) -> dict:
    s = settings_for(db, user_id)
    tz = s.timezone if s else "America/Sao_Paulo"
    limits = plan_limits(entitlements.current(db, user_id))
    day = local_day(tz)
    used = units_used(db, user_id, day)
    return {"day": day, "timezone": tz, "used": used, "limit": limits["daily"], "remaining": max(0, limits["daily"] - used)}
