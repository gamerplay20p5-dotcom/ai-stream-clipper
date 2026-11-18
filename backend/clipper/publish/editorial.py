"""Phase 5 stage F: editorial operation of publishing.

* Emergency pause: nothing new is queued or started while paused (an upload already running
  finishes; manual scheduling still works and waits).
* Semi-automatic rules: the system PROPOSES the best clips into the calendar slots; nothing is
  uploaded until a human confirms (one click, or in batch). Unconfirmed proposals expire at their slot.
* Batch scheduling of approved clips into the next free slots.
* Calendar and reports (by platform, category, hour, title).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import utcnow
from ..models import AppSetting, Clip, Publication, PublishAccount, PublishRule, SituationRow
from . import service
from .service import PublishRefused

CONTROL_KEY = "publish_control"
TZ = "America/Sao_Paulo"
CATEGORY_PT = {"police_chase": "Perseguição", "shootout": "Tiroteio", "conflict": "Treta",
               "funny_conversation": "Resenha", "scare": "Susto", "": "Sem história"}


# ------------------------------------------------------------------ emergency pause
def control(db: Session) -> dict:
    row = db.get(AppSetting, CONTROL_KEY)
    return dict(row.value or {}) if row is not None else {"paused": False}


def is_paused(db: Session) -> bool:
    return bool(control(db).get("paused"))


def set_paused(db: Session, paused: bool, reason: str, user: str) -> dict:
    row = db.get(AppSetting, CONTROL_KEY)
    if row is None:
        row = AppSetting(key=CONTROL_KEY, value={})
        db.add(row)
    row.value = {"paused": paused, "reason": reason[:300], "by": user, "at": utcnow().isoformat()}
    db.flush()
    return dict(row.value)


# ------------------------------------------------------------------ proposals (semi-automatic)
def expire_proposals(db: Session) -> int:
    n = 0
    for p in db.scalars(select(Publication).where(Publication.status == "proposed",
                                                  Publication.scheduled_at <= utcnow())):
        p.status = "expired"
        service.log_event(p, "expired", "proposta não confirmada até o horário")
        n += 1
    db.flush()  # autoflush is off: later queries in this session must see it
    return n


def confirm(db: Session, ids: list[int], user: str) -> dict:
    ok, refused = [], {}
    now = utcnow()
    for pid in ids:
        p = db.get(Publication, pid)
        if p is None or p.status != "proposed":
            refused[pid] = ["não é uma proposta pendente"]
            continue
        clip = db.get(Clip, p.clip_id) if p.clip_id else None
        acct = db.get(PublishAccount, p.account_id)
        rule = db.get(PublishRule, p.rule_id) if p.rule_id else None
        problems = ["o corte foi excluído"] if clip is None else service.eligibility(
            db, clip, acct, auto=True, rule=rule, privacy=p.privacy, semi=True)
        problems = [x for x in problems if "momento muito próximo" not in x or str(p.clip_id) in x]
        if problems:
            refused[pid] = problems
            continue
        if p.scheduled_at is None or p.scheduled_at <= now:
            p.scheduled_at = None  # its slot passed while waiting: send now
        p.status, p.created_by = "scheduled", user
        service.log_event(p, "confirmed", f"por {user}")
        ok.append(pid)
    db.flush()
    return {"confirmed": ok, "refused": refused}


def dismiss(db: Session, ids: list[int], user: str) -> list[int]:
    out = []
    for pid in ids:
        p = db.get(Publication, pid)
        if p is not None and p.status == "proposed":
            p.status = "cancelled"
            service.log_event(p, "dismissed", f"proposta recusada por {user}")
            out.append(pid)
    db.flush()
    return out


# ------------------------------------------------------------------ batch scheduling
def default_rule(acct: PublishAccount) -> PublishRule:
    """Slots/limits for an account without a rule (not persisted)."""
    return PublishRule(account_id=acct.id, enabled=False, mode="manual", max_per_day=3, slots=["12:00", "18:00", "21:00"],
                       timezone=TZ, templates={}, min_score=0, privacy="private")


def batch_schedule(db: Session, clip_ids: list[int], acct: PublishAccount, *, privacy: str, user: str,
                   rights_confirmed: bool) -> dict:
    """Each clip goes to the next free calendar slot of the account (best score first)."""
    rule = db.scalar(select(PublishRule).where(PublishRule.account_id == acct.id, PublishRule.enabled.is_(True))
                     .order_by(PublishRule.id)) or default_rule(acct)
    clips = [c for c in (db.get(Clip, i) for i in clip_ids) if c is not None]
    clips.sort(key=lambda c: -(c.editorial_score if c.editorial_score is not None else c.score))
    done, refused = [], {}
    for c in clips:
        try:
            when = service.next_slot(db, acct, rule)
            with db.begin_nested():
                pub = service.create_publication(db, c, acct, origin="manual", user=user, scheduled_at=when,
                                                 privacy=privacy, rights_confirmed=rights_confirmed,
                                                 rule=rule if rule.id else None)
            done.append({"clip_id": c.id, "publication_id": pub.id, "scheduled_at": pub.scheduled_at.isoformat()
                         if pub.scheduled_at else None})
        except PublishRefused as exc:
            refused[c.id] = exc.problems
    missing = set(clip_ids) - {c.id for c in clips}
    for m in missing:
        refused[m] = ["corte não encontrado"]
    return {"scheduled": done, "refused": refused}


# ------------------------------------------------------------------ calendar
def _local(dt: datetime | None, tz: str = TZ) -> str | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(tz)).isoformat()


def calendar(db: Session, days: int = 14) -> dict:
    now = utcnow()
    lo, hi = now - timedelta(days=2), now + timedelta(days=max(1, min(days, 60)))
    rows = db.scalars(select(Publication).where(
        Publication.status.in_(("proposed", "scheduled", "uploading", "processing", "published", "expired", "failed")),
        ((Publication.scheduled_at >= lo) & (Publication.scheduled_at <= hi))
        | ((Publication.scheduled_at.is_(None)) & (Publication.created_at >= lo))
        | ((Publication.published_at >= lo) & (Publication.published_at <= hi))))
    items = []
    for p in rows:
        clip = db.get(Clip, p.clip_id) if p.clip_id else None
        acct = db.get(PublishAccount, p.account_id)
        when = p.published_at if p.status == "published" and p.published_at else (p.scheduled_at or p.created_at)
        items.append({"id": p.id, "status": p.status, "platform": p.platform, "origin": p.origin,
                      "account": (acct.name or acct.handle) if acct else "", "privacy": p.privacy,
                      "when": _local(when), "title": p.title, "clip_id": p.clip_id,
                      "score": (clip.editorial_score if clip and clip.editorial_score is not None else
                                (clip.score if clip else None)),
                      "thumb": bool(clip and clip.thumb_path), "version": clip.version if clip else 0,
                      "last_error": p.last_error[:200]})
    items.sort(key=lambda x: x["when"] or "")
    return {"timezone": TZ, "items": items, "control": control(db)}


def reschedule(db: Session, pid: int, when: datetime | None, user: str) -> Publication:
    p = db.get(Publication, pid)
    if p is None:
        raise LookupError("publication not found")
    if p.status not in ("proposed", "scheduled"):
        raise PublishRefused(["só é possível mudar o horário antes do envio começar"])
    if when is not None and when < utcnow() - timedelta(minutes=1):
        raise PublishRefused(["o horário já passou"])
    p.scheduled_at = when
    service.log_event(p, "rescheduled", f"{when.isoformat() if when else 'imediato'} por {user}")
    db.flush()
    return p


# ------------------------------------------------------------------ reports
def _num(m: dict, *keys: str) -> int:
    for k in keys:
        v = m.get(k)
        if isinstance(v, (int, float)):
            return int(v)
    return 0


def report(db: Session, days: int = 30) -> dict:
    since = utcnow() - timedelta(days=max(1, min(days, 365)))
    pubs = list(db.scalars(select(Publication).where(Publication.created_at >= since)))
    by_platform: dict[str, dict] = defaultdict(lambda: {"published": 0, "views": 0, "likes": 0, "comments": 0,
                                                         "failed": 0, "with_metrics": 0})
    by_cat: dict[str, dict] = defaultdict(lambda: {"published": 0, "views": 0})
    by_hour: dict[int, dict] = defaultdict(lambda: {"published": 0, "views": 0})
    top = []
    origins = Counter()
    for p in pubs:
        origins[(p.origin, p.status)] += 1
        bp = by_platform[p.platform]
        if p.status == "failed":
            bp["failed"] += 1
        if p.status != "published":
            continue
        m = p.metrics or {}
        views = _num(m, "viewCount", "views", "plays", "video_views", "reach")
        likes = _num(m, "likeCount", "likes", "like_count")
        comments = _num(m, "commentCount", "comments", "comments_count")
        bp["published"] += 1
        bp["views"] += views
        bp["likes"] += likes
        bp["comments"] += comments
        bp["with_metrics"] += int(bool(m))
        clip = db.get(Clip, p.clip_id) if p.clip_id else None
        kind = ""
        if clip is not None and clip.situation_id:
            sit = db.get(SituationRow, clip.situation_id)
            kind = sit.kind if sit is not None else ""
        cat = CATEGORY_PT.get(kind, kind)
        by_cat[cat]["published"] += 1
        by_cat[cat]["views"] += views
        if p.published_at:
            h = p.published_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(TZ)).hour
            by_hour[h]["published"] += 1
            by_hour[h]["views"] += views
        top.append({"id": p.id, "title": p.title, "platform": p.platform, "views": views, "likes": likes,
                    "url": p.remote_url, "clip_id": p.clip_id})
    semi = {s: n for (o, s), n in origins.items() if o == "semi"}
    decided = sum(semi.get(s, 0) for s in ("scheduled", "uploading", "processing", "published", "failed"))
    return {
        "days": days,
        "platforms": dict(by_platform),
        "categories": [{"category": k, **v, "avg_views": round(v["views"] / v["published"]) if v["published"] else 0}
                       for k, v in sorted(by_cat.items(), key=lambda kv: -kv[1]["views"])],
        "hours": [{"hour": h, **v, "avg_views": round(v["views"] / v["published"]) if v["published"] else 0}
                  for h, v in sorted(by_hour.items())],
        "top": sorted(top, key=lambda x: -x["views"])[:10],
        "proposals": {"confirmed": decided, "dismissed": semi.get("cancelled", 0), "expired": semi.get("expired", 0),
                      "pending": semi.get("proposed", 0)},
        "note": "Métricas oficiais das plataformas; amostras pequenas são só indícios.",
    }
