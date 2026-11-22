"""Structural editor in the pipeline (Phase 6, stage B): variants, comparison, promotion.

* The clip's MAIN video is what the panel shows and what gets published.
* A VARIANT is an alternative edit of the same happening, rendered next to it (never replacing
  it, never overwriting the stored source, never failing the clip when its render fails).
* ``editor_mode``: ``off`` | ``shadow`` (default: main = original, variant = restructured, for
  comparison) | ``on`` (main = restructured, variant = original).
* A person compares (optionally blind) and may promote a variant; every comparison is stored as
  evidence (``edit_comparisons``) - quality is never claimed from the model's own score.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import queue
from .ai import structure as st
from .db import utcnow
from .models import AudioFeature, Clip, EditComparison, Job, Segment, TranscriptWord

log = logging.getLogger(__name__)

REACTION_KEYS = ("laugh", "scream", "shout", "yell", "cheer", "gasp")
VARIANT_PRIORITY = 80  # after live analysis (60) and main renders (70)


# ------------------------------------------------------------------ inputs
def media_spans(db: Session, clip: Clip) -> list[tuple[float, float]]:
    spans = [(float(a), float(a) + float(d)) for a, d in db.execute(
        select(Segment.start, Segment.duration).where(Segment.session_id == clip.session_id,
                                                      Segment.available.is_(True))).all()]
    if clip.source_path and clip.source_duration:
        spans.append((clip.source_start, clip.source_start + clip.source_duration))
    return spans


def reactions(db: Session, session_id: int, a: float, b: float, thr: float = 0.5) -> list[tuple[float, float]]:
    """Voice reactions (laughter, screams...) from the YAMNet categories stored per segment."""
    out: list[tuple[float, float]] = []
    for start, extra in db.execute(select(AudioFeature.start, AudioFeature.extra).where(
            AudioFeature.session_id == session_id, AudioFeature.start < b, AudioFeature.start + 10.5 > a)).all():
        y = (extra or {}).get("yamnet") or {}
        hop = float(y.get("hop") or 0.48)
        for name, vals in (y.get("cats") or {}).items():
            if not any(k in name.lower() for k in REACTION_KEYS):
                continue
            for i, v in enumerate(vals or []):
                if v is not None and float(v) >= thr:
                    t = float(start) + i * hop
                    if a <= t <= b:
                        out.append((t, t + hop))
    out.sort()
    merged: list[tuple[float, float]] = []
    for x, y in out:
        if merged and x <= merged[-1][1] + 0.6:
            merged[-1] = (merged[-1][0], max(merged[-1][1], y))
        else:
            merged.append((x, y))
    return merged


def moment(db: Session, clip: Clip, settings) -> st.Moment:
    from .pipeline_story import story_context

    try:
        story = story_context(db, clip).get("situation")
    except Exception:
        story = None
    lo = clip.start - (settings.max_story_seconds + 10)
    words = [(r[0], r[1], r[2]) for r in db.execute(
        select(TranscriptWord.start, TranscriptWord.end, TranscriptWord.word).where(
            TranscriptWord.session_id == clip.session_id, TranscriptWord.start >= lo,
            TranscriptWord.start < clip.end + 15).order_by(TranscriptWord.start)).all()]
    return st.Moment(start=clip.start, end=clip.end, peak=clip.peak, words=words,
                     reactions=reactions(db, clip.session_id, lo, clip.end + 15), story=story,
                     media=media_spans(db, clip), max_len=max(settings.max_clip_seconds, settings.max_story_seconds),
                     min_gap=settings.editor_min_gap)


def propose(db: Session, clip: Clip, settings, key: str = "auto", teaser: bool | None = None) -> dict:
    """The structure for this clip with objective metrics for it AND for the original."""
    m = moment(db, clip, settings)
    reason = "escolhida manualmente"
    if key == "auto":
        key, reason = st.choose(m, settings.editor_structure_prefs, clip.kinds or [])
    plan = st.build(key, m, teaser=settings.editor_teaser if teaser is None else teaser)
    orig = st.build("O", m)
    return {"structure": key, "label": st.LABELS[key], "reason": reason, "plan": plan.as_dict(),
            "applicable": plan.applicable, "why_not": plan.reason,
            "metrics": st.metrics(plan, m) if plan.applicable else {}, "original_metrics": st.metrics(orig, m),
            "meaningful": plan.applicable and st.meaningful(st.metrics(orig, m), st.metrics(plan, m))}


# ------------------------------------------------------------------ variants
def _variants(clip: Clip) -> dict:
    return dict((clip.provenance or {}).get("variants") or {})


def _save_variants(clip: Clip, v: dict) -> None:
    prov = dict(clip.provenance or {})
    prov["variants"] = v
    clip.provenance = prov


def _budget_ok(db: Session, settings) -> bool:
    since = utcnow() - timedelta(hours=1)
    n = db.query(Job).filter(Job.type == "render", Job.created_at >= since,
                             Job.idempotency_key.like("render:%:edit%")).count()
    return n < settings.editor_variants_per_hour


def enqueue_variant(db: Session, clip: Clip, name: str) -> None:
    """Queue a variant render bound to THIS plan revision (``render_id``): any other delivery is stale."""
    import uuid

    from . import entitlements
    from .models import Streamer

    # Alternative edits are a premium feature for customer channels; HTS channels keep them
    if not entitlements.has_feature(db, db.get(Streamer, clip.streamer_id), "variants", clip):
        return

    v = _variants(clip)
    rec = dict(v[name])
    rec["round"] = int(rec.get("round", 0)) + 1
    rid = uuid.uuid4().hex[:12]
    rec.update({"state": "queued", "error": "", "queued_at": utcnow().isoformat(), "render_id": rid})
    v[name] = rec
    _save_variants(clip, v)
    queue.enqueue(db, "render", {"clip_id": clip.id, "version": clip.version, "variant": name, "render_id": rid},
                  idempotency_key=f"render:{clip.id}:{name}:{rid}", priority=VARIANT_PRIORITY, max_attempts=2)


def plan_variant(db: Session, clip: Clip, settings, key: str = "auto", teaser: bool | None = None,
                 *, manual: bool = False) -> dict:
    """Build the restructured variant and queue its render (automatic: only when meaningful and
    within the hourly budget; manual: whenever applicable)."""
    prop = propose(db, clip, settings, key, teaser)
    if not prop["applicable"]:
        return {**prop, "queued": False}
    if not manual and (not prop["meaningful"] or not _budget_ok(db, settings)):
        return {**prop, "queued": False}
    v = _variants(clip)
    v["edit"] = {"structure": prop["structure"], "label": prop["label"], "reason": prop["reason"],
                 "plan": prop["plan"], "metrics": prop["metrics"], "original_metrics": prop["original_metrics"],
                 "round": int((v.get("edit") or {}).get("round", 0)), "manual": manual}
    _save_variants(clip, v)
    enqueue_variant(db, clip, "edit")
    return {**prop, "queued": True}


def variant_spec(db: Session, clip: Clip, name: str, base: dict) -> dict:
    """Render spec of a variant: the main spec with the variant's segments and span."""
    from .pipeline import _source_location, caption_words

    rec = _variants(clip).get(name)
    if not rec:
        raise LookupError("variant not found")
    segs = rec["plan"]["segments"]
    a, b = min(s["a"] for s in segs), max(s["b"] for s in segs)
    loc = _source_location(db, clip, a, b)
    plan = dict(base.get("edit_plan") or {})
    plan.update({"segments": segs, "structure": rec["structure"]})
    words = [[w[0], w[1], w[2], w[3]] for w in caption_words(db, clip, a - 0.5, b)]
    out = {**base, **loc, "start": a, "end": b, "edit_plan": plan, "words": words, "variant": name,
           "store_source": False}
    if rec.get("format_params"):  # Phase 6C preview: same montage, the profile's look
        from .format_lab import apply_format

        out = apply_format(out, rec, clip.id)
    return out


def store_variant(db: Session, clip: Clip, name: str, video_rel: str, thumb_rel: str, size: int,
                  sha: str = "", seconds: float = 0.0, render_id: str = "") -> tuple[bool, list[str]]:
    """Record a finished variant render. Returns (stored, files to delete). A delivery whose
    ``render_id`` is not the one expected for this variant (older plan, out-of-order finish,
    dropped variant) is stale: its own files are returned for deletion, nothing changes."""
    v = _variants(clip)
    rec = dict(v.get(name) or {})
    if not rec or (render_id and rec.get("render_id") and render_id != rec["render_id"]):
        return False, [p for p in (video_rel, thumb_rel) if p]
    old = [p for p in (rec.get("video_path"), rec.get("thumb_path")) if p and p not in (video_rel, thumb_rel)]
    rec.update({"state": "done", "video_path": video_rel, "thumb_path": thumb_rel, "size": size, "sha256": sha,
                "render_seconds": round(float(seconds), 2), "done_at": utcnow().isoformat()})
    v[name] = rec
    _save_variants(clip, v)
    return True, old


def referenced_files(db: Session, clip: Clip) -> set[str]:
    """Files that must never be deleted or overwritten: the main video, every variant, and every
    media snapshot of a publication that is still pending or in progress."""
    from .models import Publication

    refs = {p for p in (clip.video_path, clip.thumb_path) if p}
    for rec in _variants(clip).values():
        refs.update(p for p in (rec.get("video_path"), rec.get("thumb_path")) if p)
    refs.update(p for (p,) in db.execute(select(Publication.video_path).where(
        Publication.clip_id == clip.id, Publication.video_path != "",
        Publication.status.in_(("draft", "proposed", "scheduled", "uploading", "processing", "uncertain")))).all() if p)
    from .sharing import protected_files

    refs.update(protected_files(db, clip))  # media frozen in valid shares (multi-user)
    return refs


def variant_failed(db: Session, clip_id: int, name: str, error: str, render_id: str = "") -> bool:
    """Mark the variant failed ONLY if the failed job is the render currently expected for it:
    a late failure of an older revision never touches a newer one. Returns True when applied."""
    clip = db.get(Clip, clip_id)
    if clip is None:
        return False
    v = _variants(clip)
    rec = v.get(name)
    if not rec or rec.get("state") == "done":
        return False
    if rec.get("render_id") and render_id != rec["render_id"]:
        log.info("stale variant failure ignored", extra={"clip_id": clip_id, "variant": name,
                                                        "failed": render_id, "expected": rec.get("render_id")})
        return False
    v[name] = {**rec, "state": "failed", "error": error[:500]}
    _save_variants(clip, v)
    return True


def promote(db: Session, clip: Clip, name: str, user: str) -> dict:
    """Make a finished variant the main video (no new render); the old main becomes a variant."""
    from .models import Publication

    v = _variants(clip)
    rec = v.get(name)
    if not rec or rec.get("state") != "done" or not rec.get("video_path") or not rec.get("sha256"):
        raise ValueError("a variante ainda não foi renderizada")
    if clip.status in ("rendering", "analyzing", "candidate", "waiting"):
        raise ValueError("o corte está em processamento")
    busy = db.query(Publication).filter(Publication.clip_id == clip.id,
                                        Publication.status.in_(("uploading", "processing"))).count()
    if busy:
        raise ValueError("há uma publicação deste corte sendo enviada agora: espere terminar")
    pending = db.query(Publication).filter(Publication.clip_id == clip.id,
                                           Publication.status.in_(("draft", "proposed", "scheduled"))).count()
    old_plan = dict(clip.edit_plan or {})
    # the previous main keeps ALL its media metadata (it can be promoted back without a render)
    old_main = {"structure": old_plan.get("structure") or "O", "label": st.LABELS.get(old_plan.get("structure") or "O"),
                "plan": {"segments": old_plan.get("segments") or [{"a": clip.start, "b": clip.end, "role": "main"}]},
                "metrics": rec.get("original_metrics") if name == "edit" else rec.get("metrics_main", {}),
                "state": "done", "video_path": clip.video_path, "thumb_path": clip.thumb_path, "size": clip.file_size,
                "sha256": clip.sha256, "render_seconds": clip.render_seconds, "start": clip.start, "end": clip.end,
                "version": clip.version, "render_id": "promoted"}
    segs = rec["plan"]["segments"]
    new_plan = dict(old_plan)
    new_plan.update({"segments": segs, "structure": rec.get("structure"), "structure_reason": rec.get("reason", "")})
    if rec.get("format_params"):  # a promoted format preview keeps its look in future re-renders
        new_plan["format_params"] = rec["format_params"]
        new_plan["format_profile_id"] = rec.get("format_profile_id")
    clip.edit_plan = new_plan
    clip.start, clip.end = float(rec.get("start", min(s["a"] for s in segs))), float(rec.get("end", max(s["b"] for s in segs)))
    clip.video_path, clip.thumb_path = rec["video_path"], rec.get("thumb_path", "")
    clip.file_size, clip.sha256 = int(rec.get("size", 0)), rec["sha256"]
    clip.render_seconds = float(rec.get("render_seconds", 0.0))
    # new version: any main render still queued for the previous version is now stale
    clip.version += 1
    prov = dict(clip.provenance or {})
    prov["render"] = {"expected": None, "version": clip.version, "promoted": name}
    clip.provenance = prov
    other = "original" if name in ("edit", "format") else "edit"
    v = _variants(clip)
    v.pop(name, None)
    v[other] = old_main
    _save_variants(clip, v)
    log.info("variant promoted", extra={"clip_id": clip.id, "variant": name, "by": user})
    return {"main": rec.get("structure"), "variant": other, "pending_publications": pending,
            "note": (f"{pending} publicação(ões) já agendada(s) continua(m) com o vídeo aprovado no agendamento"
                     if pending else "")}


def compare(db: Session, clip: Clip, left: str, right: str, winner: str, blind: bool, user: str,
            note: str = "") -> EditComparison:
    names = {"main": (clip.edit_plan or {}).get("structure") or "O"}
    for k, rec in _variants(clip).items():
        names[k] = rec.get("structure") or k
    if left not in names or right not in names or left == right:
        raise ValueError("versões inválidas")
    row = EditComparison(clip_id=clip.id, left=left, right=right, left_structure=names[left],
                         right_structure=names[right], winner=winner, blind=blind, reviewer=user, note=note[:500])
    db.add(row)
    db.flush()
    return row


def overview(db: Session, clip: Clip) -> dict:
    out = {"main": {"structure": (clip.edit_plan or {}).get("structure") or "O",
                    "label": st.LABELS.get((clip.edit_plan or {}).get("structure") or "O"),
                    "segments": (clip.edit_plan or {}).get("segments") or [{"a": clip.start, "b": clip.end,
                                                                            "role": "main"}],
                    "video": bool(clip.video_path), "version": clip.version}, "variants": {}}
    for k, rec in _variants(clip).items():
        out["variants"][k] = {kk: rec.get(kk) for kk in ("structure", "label", "reason", "metrics", "original_metrics",
                                                         "state", "error", "manual")}
        out["variants"][k]["segments"] = (rec.get("plan") or {}).get("segments") or []
        out["variants"][k]["video"] = rec.get("state") == "done" and bool(rec.get("video_path"))
    out["comparisons"] = [{"id": r.id, "left": r.left, "right": r.right, "left_structure": r.left_structure,
                           "right_structure": r.right_structure, "winner": r.winner, "blind": r.blind,
                           "reviewer": r.reviewer, "at": r.created_at.isoformat() if r.created_at else None}
                          for r in db.scalars(select(EditComparison).where(EditComparison.clip_id == clip.id)
                                              .order_by(EditComparison.id.desc()).limit(20))]
    return out


def results(db: Session) -> dict:
    """Blind human comparisons by structure: the only evidence that a structure is better."""
    agg: dict[str, dict] = {}
    for r in db.scalars(select(EditComparison)):
        for side, struct in (("left", r.left_structure), ("right", r.right_structure)):
            a = agg.setdefault(struct, {"shown": 0, "won": 0, "lost": 0, "equal": 0, "blind": 0})
            a["shown"] += 1
            a["blind"] += int(r.blind)
            if r.winner == "equal":
                a["equal"] += 1
            elif r.winner == side:
                a["won"] += 1
            else:
                a["lost"] += 1
    return {"by_structure": agg, "total": db.query(EditComparison).count(),
            "note": "Só comparações cegas contam como evidência; amostras pequenas são indícios."}
