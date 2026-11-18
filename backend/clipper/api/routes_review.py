"""Editorial evaluation API (Phase 3): review queue, human feedback, metrics, dataset export, rankers."""

from __future__ import annotations

import csv
import io
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db, utcnow
from ..evaluation import dataset, metrics, ranker, taxonomy
from ..evaluation import media as ds_media
from ..models import Candidate, Clip, EvalExample, RankerModel, Review
from ..settings_store import load_ai_settings
from .deps import CurrentUser, audit, current_user, require_admin

router = APIRouter(prefix="/api/review", tags=["review"])


def _iso(dt):
    return dt.isoformat() + "Z" if dt else None


def _review_dict(rv: Review | None) -> dict | None:
    if rv is None:
        return None
    return {"id": rv.id, "reviewer": rv.reviewer, "rating": rv.rating, "category": rv.category,
            "editorial_reasons": rv.editorial_reasons, "technical_issues": rv.technical_issues,
            "description": rv.description, "notes": rv.notes, "context_quality": rv.context_quality,
            "ideal_duration": rv.ideal_duration, "event_start": rv.event_start, "event_peak": rv.event_peak,
            "event_end": rv.event_end, "short_start": rv.short_start, "short_end": rv.short_end,
            "updated_at": _iso(rv.updated_at)}


def _example_dict(db: Session, ex: EvalExample, full: bool = False) -> dict:
    d = {"id": ex.id, "clip_id": ex.clip_id, "candidate_id": ex.candidate_id, "session_id": ex.session_id,
         "streamer_id": ex.streamer_id, "streamer_name": ex.streamer_name, "platform": ex.platform,
         "stratum": ex.stratum, "stratum_label": taxonomy.STRATA.get(ex.stratum, ex.stratum),
         "priority": ex.priority, "split": ex.split, "is_test": ex.is_test,
         "media_start": ex.media_start, "media_end": ex.media_end, "media_state": ex.media_state,
         "media_error": ex.media_error, "ai": ex.ai, "created_at": _iso(ex.created_at),
         "reviewed_at": _iso(ex.reviewed_at), "review": _review_dict(dataset.current_review(db, ex))}
    if full:
        cand = db.get(Candidate, ex.candidate_id) if ex.candidate_id else None
        d.update({"context": ex.context, "provenance": ex.provenance, "ai_runs": ex.ai_runs,
                  "features": cand.features if cand else None,
                  "clip_exists": bool(ex.clip_id and db.get(Clip, ex.clip_id))})
    return d


def _get(db: Session, eid: int) -> EvalExample:
    ex = db.get(EvalExample, eid)
    if ex is None:
        raise HTTPException(404, "example not found")
    return ex


@router.get("/taxonomy")
def get_taxonomy(_: CurrentUser = Depends(current_user)):
    return {"ratings": taxonomy.RATINGS, "editorial_reasons": taxonomy.EDITORIAL_REASONS,
            "technical_issues": taxonomy.TECHNICAL_ISSUES, "categories": taxonomy.CATEGORIES,
            "strata": taxonomy.STRATA, "providers": taxonomy.PROVIDERS}


@router.get("/stats")
def stats(db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    s = load_ai_settings(db)
    out = dataset.counts(db)
    out["quota_bytes"] = int(s.dataset_quota_gb * 1024**3)
    out["candidates"] = db.scalar(select(func.count()).select_from(Candidate)) or 0
    return out


@router.get("/queue")
def queue_(limit: int = 30, db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    return [_example_dict(db, ex) for ex in dataset.review_queue(db, max(1, min(limit, 200)))]


@router.get("/examples")
def list_examples(state: str = "all", stratum: str | None = None, split: str | None = None, limit: int = 50,
                  offset: int = 0, db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    q = select(EvalExample)
    if state == "pending":
        q = q.where(EvalExample.reviewed_at.is_(None))
    elif state == "reviewed":
        q = q.where(EvalExample.reviewed_at.is_not(None))
    if stratum:
        q = q.where(EvalExample.stratum == stratum)
    if split:
        q = q.where(EvalExample.split == split)
    rows = db.scalars(q.order_by(EvalExample.created_at.desc()).offset(max(0, offset)).limit(max(1, min(limit, 200))))
    return [_example_dict(db, ex) for ex in rows]


@router.get("/examples/{eid}")
def get_example(eid: int, db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    return _example_dict(db, _get(db, eid), full=True)


@router.get("/examples/{eid}/media")
def example_media(eid: int, db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    try:
        p = ds_media.media_file(_get(db, eid))
    except ds_media.NotAvailable as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(p, media_type="video/mp4", headers={"Cache-Control": "private, max-age=300"})


class ReviewIn(BaseModel):
    rating: str
    category: str = ""
    editorial_reasons: list[str] = Field(default_factory=list, max_length=12)
    technical_issues: list[str] = Field(default_factory=list, max_length=8)
    description: str = Field("", max_length=2000)
    notes: str = Field("", max_length=4000)
    context_quality: int | None = Field(None, ge=0, le=10)
    event_start: float | None = Field(None, ge=0)
    event_peak: float | None = Field(None, ge=0)
    event_end: float | None = Field(None, ge=0)
    short_start: float | None = Field(None, ge=0)
    short_end: float | None = Field(None, ge=0)

    @model_validator(mode="after")
    def _check(self):
        if self.rating not in taxonomy.RATINGS:
            raise ValueError("invalid rating")
        if self.category and self.category not in taxonomy.CATEGORIES:
            raise ValueError("invalid category")
        bad = set(self.editorial_reasons) - set(taxonomy.EDITORIAL_REASONS)
        bad |= set(self.technical_issues) - set(taxonomy.TECHNICAL_ISSUES)
        if bad:
            raise ValueError(f"invalid reasons: {sorted(bad)}")
        ev = [v for v in (self.event_start, self.event_peak, self.event_end) if v is not None]
        if ev != sorted(ev):
            raise ValueError("event start <= peak <= end")
        if (self.short_start is None) != (self.short_end is None):
            raise ValueError("short start and end go together")
        if self.short_start is not None and not (3.0 <= self.short_end - self.short_start <= 180.0):
            raise ValueError("short duration must be between 3 and 180 s")
        return self


@router.put("/examples/{eid}/review")
def put_review(eid: int, body: ReviewIn, request: Request, db: Session = Depends(get_db),
               user: CurrentUser = Depends(require_admin)):
    ex = _get(db, eid)
    lo, hi = ex.media_start - 1, ex.media_end + 1
    for v in (body.event_start, body.event_peak, body.event_end, body.short_start, body.short_end):
        if v is not None and not lo <= v <= hi:
            raise HTTPException(422, "timestamps must lie inside the example media")
    rv = db.scalar(select(Review).where(Review.example_id == ex.id, Review.reviewer == user.email))
    if rv is None:
        rv = Review(example_id=ex.id, reviewer=user.email)
        db.add(rv)
    for k, v in body.model_dump().items():
        setattr(rv, k, v)
    rv.ideal_duration = (body.short_end - body.short_start) if body.short_start is not None else None
    rv.ai_snapshot = dict(ex.ai or {})
    rv.updated_at = utcnow()
    ex.reviewed_at = utcnow()
    audit(db, request, user, "review.save", f"example:{eid}", {"rating": body.rating})
    db.commit()
    return _example_dict(db, ex, full=True)


@router.delete("/examples/{eid}/review")
def delete_review(eid: int, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(require_admin)):
    ex = _get(db, eid)
    for rv in db.scalars(select(Review).where(Review.example_id == ex.id, Review.reviewer == user.email)):
        db.delete(rv)
    db.flush()
    if dataset.current_review(db, ex) is None:
        ex.reviewed_at = None
    audit(db, request, user, "review.delete", f"example:{eid}")
    db.commit()
    return {"ok": True}


@router.post("/clips/{cid}/example")
def example_for_clip(cid: int, request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(require_admin)):
    """Open (or create) the evaluation example of a clip - used by 'Avaliar' on the clip page."""
    clip = db.get(Clip, cid)
    if clip is None:
        raise HTTPException(404, "clip not found")
    cand = db.scalar(select(Candidate).where(Candidate.clip_id == cid))
    ex = dataset.create_example(db, stratum="manual_test" if clip.is_test else "accepted", candidate=cand, clip=clip,
                                priority=30.0, media_source="clip")
    if ex.media_state in ("failed", "expired"):  # retry from the stored clip source
        ex.media_state, ex.media_error = "pending", ""
        from .. import queue

        queue.enqueue(db, "dataset_media", {"example_id": ex.id, "prefer": "clip"},
                      idempotency_key=f"dataset_media:{ex.id}:retry:{int(utcnow().timestamp())}", priority=80)
    audit(db, request, user, "review.open_clip", f"clip:{cid}", {"example": ex.id})
    db.commit()
    return _example_dict(db, ex, full=True)


@router.post("/backfill")
def backfill(request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(require_admin)):
    """Create examples for clips produced before Phase 3 (their stored source keeps the media)."""
    made = 0
    for clip in db.scalars(select(Clip).where(Clip.status.in_(("ready", "approved", "rejected")))):
        if db.scalar(select(EvalExample.id).where(EvalExample.clip_id == clip.id)):
            continue
        if not clip.source_path:
            continue
        dataset.create_example(db, stratum="manual_test" if clip.is_test else "accepted", clip=clip, priority=45.0,
                               media_source="clip")
        made += 1
    audit(db, request, user, "review.backfill", "", {"created": made})
    db.commit()
    return {"created": made}


@router.get("/metrics")
def get_metrics(split: str | None = None, db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    return {"editorial": metrics.editorial(db, split), "note": "Métricas editoriais usam só avaliações humanas; "
            "cortes de teste manual ficam fora. Nota alta não é garantia de qualidade nem probabilidade de viralizar."}


@router.get("/operational")
def get_operational(hours: int = 24, db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    return metrics.operational(db, max(1, min(hours, 24 * 30)))


@router.get("/export")
def export(request: Request, format: str = "jsonl", split: str | None = None, reviewed_only: bool = True,
           db: Session = Depends(get_db), user: CurrentUser = Depends(require_admin)):
    if format not in ("jsonl", "csv"):
        raise HTTPException(422, "format must be jsonl or csv")
    rows = list(dataset.export_rows(db, split, reviewed_only))
    audit(db, request, user, "review.export", "", {"format": format, "rows": len(rows), "split": split})
    db.commit()
    stamp = utcnow().strftime("%Y%m%d-%H%M")
    if format == "jsonl":
        body = "".join(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in rows)
        return StreamingResponse(iter([body]), media_type="application/x-ndjson",
                                 headers={"Content-Disposition": f'attachment; filename="asc-dataset-{stamp}.jsonl"'})
    # CSV: one row per labelled example with the flat candidate features (training table)
    feats = sorted({k for r in rows for k in (r.get("features") or {})})
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["example_id", "split", "stratum", "session_id", "streamer", "ai_decision", "ai_score", "rating",
                "positive", "category", "reasons", *feats])
    for r in rows:
        lab = r.get("label") or {}
        f = r.get("features") or {}
        w.writerow([r["example_id"], r["split"], r["stratum"], r["session_id"], r["streamer"]["name"],
                    (r["ai"] or {}).get("decision"), (r["ai"] or {}).get("score"), lab.get("rating"),
                    lab.get("positive"), lab.get("category"), "|".join(lab.get("editorial_reasons") or []),
                    *[f.get(k) for k in feats]])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="asc-dataset-{stamp}.csv"'})


def _ranker_dict(m: RankerModel) -> dict:
    return {"id": m.id, "name": m.name, "kind": m.kind, "status": m.status, "metrics": m.metrics,
            "n_train": m.n_train, "n_val": m.n_val, "n_features": len(m.feature_names or []),
            "created_by": m.created_by, "created_at": _iso(m.created_at), "activated_at": _iso(m.activated_at)}


@router.get("/learning")
def learning(db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    from ..evaluation import feedback

    return {"status": feedback.status(db), "profile": feedback.profile(db)}


@router.post("/learning/backfill")
def learning_backfill(request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(require_admin)):
    """Import past approve/reject decisions (audit trail) as human labels."""
    from ..evaluation import feedback

    out = feedback.backfill_from_audit(db)
    feedback.request_learning(db)
    audit(db, request, user, "learning.backfill", "", out)
    db.commit()
    return out


@router.post("/learning/run")
def learning_run(request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(require_admin)):
    from ..evaluation import feedback

    feedback.request_learning(db)
    audit(db, request, user, "learning.request", "")
    db.commit()
    return {"queued": True}


@router.get("/rankers")
def list_rankers(db: Session = Depends(get_db), _: CurrentUser = Depends(current_user)):
    data = ranker.labelled(db)
    return {"models": [_ranker_dict(m) for m in db.scalars(select(RankerModel).order_by(RankerModel.id.desc()))],
            "labelled": {k: {"n": len(v), "pos": sum(y for _, y, _, _ in v)} for k, v in data.items()},
            "requirements": {"min_train": ranker.MIN_TRAIN, "min_per_class": ranker.MIN_PER_CLASS,
                             "min_val": ranker.MIN_VAL},
            "mode": load_ai_settings(db).ranker_mode}


@router.post("/rankers/train")
def train_ranker(request: Request, db: Session = Depends(get_db), user: CurrentUser = Depends(require_admin)):
    try:
        m = ranker.train(db, user.email)
    except ranker.NotEnoughData as exc:
        raise HTTPException(422, {"message": str(exc), "counts": exc.counts}) from exc
    audit(db, request, user, "ranker.train", f"ranker:{m.id}", {"metrics": m.metrics.get("ranker")})
    db.commit()
    return _ranker_dict(m)


class RankerStatusIn(BaseModel):
    status: str


@router.post("/rankers/{rid}/status")
def ranker_status(rid: int, body: RankerStatusIn, request: Request, db: Session = Depends(get_db),
                  user: CurrentUser = Depends(require_admin)):
    m = db.get(RankerModel, rid)
    if m is None:
        raise HTTPException(404, "ranker not found")
    try:
        ranker.set_status(db, m, body.status)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    audit(db, request, user, "ranker.status", f"ranker:{rid}", {"status": body.status})
    db.commit()
    return _ranker_dict(m)
