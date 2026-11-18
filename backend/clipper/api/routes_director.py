"""Director AI view (Phase 5): live sessions, their story memory and the decisions attached to it."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..ai.story import KIND_PT
from ..db import get_db
from ..models import Candidate, Clip, SituationRow, Streamer, StreamSession
from .deps import CurrentUser, current_user

router = APIRouter(prefix="/api/director", tags=["director"])


def _sit(r: SituationRow, clips: list[Clip]) -> dict:
    d = r.data or {}
    return {"id": r.id, "kind": r.kind, "kind_label": KIND_PT.get(r.kind, r.kind), "start": r.start, "last": r.last,
            "status": r.status, "confidence": r.confidence, "summary": r.summary, "related_to": r.related_to,
            "complete": d.get("complete"), "outcome": d.get("outcome", ""), "climax_t": d.get("climax_t"),
            "events": [{"t": e["t"], "phase": e["phase"], "label": e["label"], "source": e["source"]}
                       for e in (d.get("events") or [])][:24],
            "participants": d.get("participants") or [], "uncertain": d.get("uncertain") or [],
            "changes": d.get("changes") or [], "goals": d.get("goals") or [], "conflicts": d.get("conflicts") or [],
            "clips": [{"id": c.id, "status": c.status, "title": c.title, "start": c.start, "end": c.end,
                       "score": c.score, "prelim_score": c.prelim_score, "editorial_score": c.editorial_score,
                       "confidence": c.confidence, "story_action": (c.provenance or {}).get("story", {}).get("action")}
                      for c in clips if c.situation_id == r.id]}


@router.get("/sessions")
def sessions(limit: int = 12, db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    out = []
    for s in db.scalars(select(StreamSession).order_by(StreamSession.id.desc()).limit(max(1, min(limit, 50)))):
        st = db.get(Streamer, s.streamer_id)
        n = db.query(SituationRow).filter(SituationRow.session_id == s.id, SituationRow.status != "discarded").count()
        out.append({"id": s.id, "streamer": (st.display_name if st else "") or "", "platform": st.platform if st else "",
                    "status": s.status, "title": s.title, "covered_until": s.covered_until,
                    "story_until": (s.stats or {}).get("story_until"), "situations": n,
                    "open": (s.stats or {}).get("situations_open", 0)})
    return out


@router.get("/sessions/{sid}")
def session_story(sid: int, db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    s = db.get(StreamSession, sid)
    if s is None:
        raise HTTPException(404, "session not found")
    rows = list(db.scalars(select(SituationRow).where(SituationRow.session_id == sid, SituationRow.status != "discarded")
                           .order_by(SituationRow.start)))
    clips = list(db.scalars(select(Clip).where(Clip.session_id == sid)))
    counts: dict[str, int] = {}
    for (d,) in db.execute(select(Candidate.decision).where(Candidate.session_id == sid)):
        counts[d] = counts.get(d, 0) + 1
    st = db.get(Streamer, s.streamer_id)
    return {"session": {"id": s.id, "streamer": (st.display_name if st else "") or "", "status": s.status,
                        "covered_until": s.covered_until, "story_until": (s.stats or {}).get("story_until")},
            "situations": [_sit(r, clips) for r in rows[-60:]], "candidates": counts,
            "isolated_clips": [{"id": c.id, "status": c.status, "title": c.title, "start": c.start, "score": c.score}
                               for c in clips if not c.situation_id][-30:]}
