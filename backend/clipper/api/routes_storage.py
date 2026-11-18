"""Storage management (Phase 5 stage C): overview, simulation, protection, quarantine/restore."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import storage_lifecycle as lc
from ..db import get_db, utcnow
from ..models import CleanupLog, Clip
from ..settings_store import load_ai_settings
from .deps import CurrentUser, audit, current_user, require_admin

router = APIRouter(prefix="/api/storage", tags=["storage"])
RETRO_PHRASE = "AUTORIZO A LIMPEZA RETROATIVA"


@router.get("/overview")
def overview(db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    return lc.overview(db, load_ai_settings(db))


@router.get("/simulation")
def simulation(include_existing: bool = False, db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    """What the cleanup would do with the current settings. Read-only: changes nothing."""
    p = lc.plan(db, load_ai_settings(db), include_existing=include_existing or None)
    p["eligible"] = p["eligible"][:300]
    p["protected"] = p["protected"][:300]
    p["waiting_period"] = p["waiting_period"][:300]
    return p


@router.get("/log")
def cleanup_log(limit: int = 100, db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    rows = db.scalars(select(CleanupLog).order_by(CleanupLog.id.desc()).limit(max(1, min(limit, 500))))
    return [{"id": r.id, "clip_id": r.clip_id, "action": r.action, "reason": r.reason, "bytes_freed": r.bytes_freed,
             "dry_run": r.dry_run, "actor": r.actor, "at": r.created_at.isoformat() + "Z"} for r in rows]


class FlagIn(BaseModel):
    value: bool


def _clip(db: Session, cid: int) -> Clip:
    c = db.get(Clip, cid)
    if c is None:
        raise HTTPException(404, "clip not found")
    return c


@router.post("/clips/{cid}/protect")
def protect(cid: int, body: FlagIn, request: Request, db: Session = Depends(get_db),
            user: CurrentUser = Depends(require_admin)):
    c = _clip(db, cid)
    c.protected = body.value
    if body.value and c.lifecycle == "quarantined":
        lc.restore(db, cid, user.email)
    audit(db, request, user, "storage.protect", f"clip:{cid}", {"value": body.value})
    db.commit()
    return {"protected": c.protected, "lifecycle": c.lifecycle}


@router.post("/clips/{cid}/favorite")
def favorite(cid: int, body: FlagIn, request: Request, db: Session = Depends(get_db),
             user: CurrentUser = Depends(require_admin)):
    c = _clip(db, cid)
    c.favorite = body.value
    if body.value and c.lifecycle == "quarantined":
        lc.restore(db, cid, user.email)
    audit(db, request, user, "storage.favorite", f"clip:{cid}", {"value": body.value})
    db.commit()
    return {"favorite": c.favorite, "lifecycle": c.lifecycle}


@router.post("/clips/{cid}/restore")
def restore(cid: int, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(require_admin)):
    _clip(db, cid)
    if not lc.restore(db, cid, user.email):
        raise HTTPException(409, "só cortes em quarentena podem ser restaurados (depois da exclusão definitiva a mídia não volta)")
    audit(db, request, user, "storage.restore", f"clip:{cid}")
    db.commit()
    return {"ok": True}


class RetroIn(BaseModel):
    confirm: str = Field(..., max_length=80)


@router.post("/authorize-retroactive")
def authorize_retroactive(body: RetroIn, request: Request, db: Session = Depends(get_db),
                          user: CurrentUser = Depends(require_admin)):
    """Explicit owner authorisation to include clips that existed before the cleanup was enabled."""
    if body.confirm.strip().upper() != RETRO_PHRASE:
        raise HTTPException(422, f"para autorizar, digite exatamente: {RETRO_PHRASE}")
    st = lc.state(db)
    st.update({"retro_authorized": True, "retro_authorized_by": user.email, "retro_authorized_at": utcnow().isoformat()})
    lc.save_state(db, st)
    audit(db, request, user, "storage.retro_authorize", "")
    db.commit()
    return {"ok": True}
