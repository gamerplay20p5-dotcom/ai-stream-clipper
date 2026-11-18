"""Continuous learning from the owner's decisions (Phase 5).

* Approve / reject in the clip review page are HUMAN labels (the director's automatic
  rejections are not: they are predictions and never become labels).
* Explicit reviews (Avaliação page) always win over the coarse approve/reject label.
* Manual time edits of a clip are kept as the human "ideal Short" bounds.
* A low-priority ``learn`` job rebuilds the editorial preference profile (with sample sizes and
  confidence intervals, so patterns show early but are flagged when inconclusive) and retrains the
  ranker when there is enough data. New models start in shadow; promotion to active requires a
  validation win over the heuristic AND over the previous active model on the same validation set.
"""

from __future__ import annotations

import logging
import math
from collections import Counter, defaultdict
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import queue
from ..db import utcnow
from ..models import AuditLog, Candidate, Clip, EvalExample, LearningRun, RankerModel, Review, SituationRow
from . import dataset, ranker, taxonomy

log = logging.getLogger(__name__)

ACTION_RATING = {"approve": "good", "reject": "bad"}
MIN_NEW_LABELS = 3  # retrain/re-profile after this many new human labels
INSUFFICIENT = 10  # below this a group's rate is shown as "insufficient data"


def _example_for(db: Session, clip: Clip) -> EvalExample:
    cand = db.scalar(select(Candidate).where(Candidate.clip_id == clip.id))
    return dataset.create_example(db, stratum="manual_test" if clip.is_test else "accepted", candidate=cand,
                                  clip=clip, priority=10.0, media_source="clip")


def record_clip_action(db: Session, clip: Clip, reviewer: str, action: str) -> Review | None:
    """Approve/reject from the clip page -> human label (never overrides an explicit review)."""
    if action not in ACTION_RATING:
        return None
    ex = _example_for(db, clip)
    rv = db.scalar(select(Review).where(Review.example_id == ex.id, Review.reviewer == reviewer))
    if rv is not None and rv.source == "review":
        return rv  # a detailed review exists: keep it
    if rv is None:
        rv = Review(example_id=ex.id, reviewer=reviewer, source="clip_action")
        db.add(rv)
    rv.rating = ACTION_RATING[action]
    rv.ai_snapshot = dict(ex.ai or {})
    edit = (clip.provenance or {}).get("human_edit") or {}
    if edit.get("start") is not None and edit.get("end") is not None:
        rv.short_start, rv.short_end = float(edit["start"]), float(edit["end"])
        rv.ideal_duration = rv.short_end - rv.short_start
    rv.updated_at = utcnow()
    ex.reviewed_at = utcnow()
    request_learning(db)
    return rv


def record_time_edit(clip: Clip, start: float, end: float, user: str) -> None:
    """Manual start/end correction = the human's ideal bounds (used when the clip gets a label)."""
    prov = dict(clip.provenance or {})
    prov["human_edit"] = {"start": round(start, 2), "end": round(end, 2), "by": user,
                          "ai_start": clip.heuristic_start if clip.heuristic_start is not None else clip.start,
                          "ai_end": clip.heuristic_end if clip.heuristic_end is not None else clip.end}
    clip.provenance = prov


def backfill_from_audit(db: Session) -> dict:
    """Turn past approve/reject actions (audit trail = human) into labels; last action per clip wins."""
    last: dict[int, tuple] = {}
    for a in db.scalars(select(AuditLog).where(AuditLog.action.in_(("clip.approve", "clip.reject")))
                        .order_by(AuditLog.created_at)):
        try:
            cid = int(a.target.split(":")[1])
        except (IndexError, ValueError):
            continue
        last[cid] = (a.action.split(".")[1], a.user_email or "admin")
    made, missing = 0, 0
    for cid, (action, user) in last.items():
        clip = db.get(Clip, cid)
        if clip is None:
            missing += 1
            continue
        if record_clip_action(db, clip, user, action) is not None:
            made += 1
    return {"labels": made, "clips_deleted_before": missing}


def request_learning(db: Session) -> None:
    """Enqueue the background learning job (idempotent per 10-minute bucket, lowest priority)."""
    bucket = int(utcnow().timestamp() // 600)
    queue.enqueue(db, "learn", {"reason": "new_labels"}, idempotency_key=f"learn:{bucket}", priority=300,
                  max_attempts=2, delay_s=60)


# ------------------------------------------------------------------ preference profile
def _wilson(pos: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    if n == 0:
        return None
    p = pos / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return round(max(0.0, c - h), 2), round(min(1.0, c + h), 2)


def _group(rows, key) -> list[dict]:
    g: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r in rows:
        for k in key(r) or ["(nenhum)"]:
            g[k][0] += r["pos"]
            g[k][1] += 1
    out = []
    for k, (pos, n) in g.items():
        out.append({"value": k, "n": n, "approved": pos, "rate": round(pos / n, 2) if n else None,
                    "ci95": _wilson(pos, n), "reliable": n >= INSUFFICIENT})
    return sorted(out, key=lambda x: (-x["n"], x["value"]))


def human_labels(db: Session) -> list[dict]:
    """Binary human labels with the observable context of each (no AI prediction is a label)."""
    rows = []
    latest: dict[int, Review] = {}
    for rv in db.scalars(select(Review).order_by(Review.updated_at)):
        latest[rv.example_id] = rv
    for ex_id, rv in latest.items():
        if rv.rating == "fair":
            continue
        ex = db.get(EvalExample, ex_id)
        if ex is None or ex.is_test:
            continue
        ai = ex.ai or {}
        clip = db.get(Clip, ex.clip_id) if ex.clip_id else None
        sit = db.get(SituationRow, clip.situation_id) if clip is not None and clip.situation_id else None
        dur = (ai.get("end") or 0) - (ai.get("start") or 0)
        rows.append({
            "pos": 1 if rv.rating in taxonomy.POSITIVE else 0, "source": rv.source, "rating": rv.rating,
            "streamer": ex.streamer_name or "?", "kinds": sorted(set(ai.get("kinds") or [])),
            "category": rv.category or "", "reasons": rv.editorial_reasons or [],
            "situation": sit.kind if sit is not None else "", "score": ai.get("score"), "duration": dur,
            "start_delta": (ai.get("start") - rv.short_start) if rv.short_start is not None and ai.get("start") is not None else None,
            "end_delta": (ai.get("end") - rv.short_end) if rv.short_end is not None and ai.get("end") is not None else None,
            "provider": ai.get("provider", ""), "session": ex.session_id,
        })
    return rows


def profile(db: Session) -> dict:
    rows = human_labels(db)
    n = len(rows)
    pos = sum(r["pos"] for r in rows)
    approved = [r for r in rows if r["pos"]]
    rejected = [r for r in rows if not r["pos"]]

    def med(vals):
        vals = sorted(v for v in vals if v is not None)
        return round(vals[len(vals) // 2], 1) if vals else None

    score_split = None
    sc_pos = [r["score"] for r in approved if r["score"] is not None]
    sc_neg = [r["score"] for r in rejected if r["score"] is not None]
    if sc_pos and sc_neg:
        wins = sum((p > q) + 0.5 * (p == q) for p in sc_pos for q in sc_neg)
        score_split = round(wins / (len(sc_pos) * len(sc_neg)), 2)  # AUC of the current score
    reasons = Counter(x for r in rejected for x in r["reasons"])
    starts = [r["start_delta"] for r in rows if r["start_delta"] is not None]
    recurring = []
    if starts and sum(1 for d in starts if d > 2.0) >= max(3, len(starts) // 2):
        recurring.append("os cortes costumam começar tarde: o contexto anterior precisa crescer")
    if reasons.get("started_late", 0) >= 3:
        recurring.append(f"{reasons['started_late']} rejeições por começar tarde")
    if reasons.get("missing_context", 0) >= 3:
        recurring.append(f"{reasons['missing_context']} rejeições por falta de contexto")
    if reasons.get("nothing_interesting", 0) >= 3:
        recurring.append(f"{reasons['nothing_interesting']} rejeições por nada interessante: o limiar ou os sinais estão frouxos")
    kinds_total = Counter(k for r in rows for k in r["kinds"])
    dominant = kinds_total.most_common(1)[0] if kinds_total else None
    diversity_note = None
    if dominant and n >= 8 and dominant[1] / n > 0.7:
        diversity_note = (f"{dominant[1]} de {n} exemplos rotulados têm o sinal “{dominant[0]}”: "
                          "o aprendizado compensa por classe para não favorecer só esse tipo")
    return {
        "labels": n, "approved": pos, "rejected": n - pos,
        "by_source": dict(Counter(r["source"] for r in rows)),
        "sufficient_for_training": n >= ranker.MIN_TRAIN,
        "note": ("dados insuficientes para conclusões confiáveis: as tendências abaixo são indícios"
                 if n < 30 else "amostra moderada: confira os intervalos de confiança"),
        "approval_rate": round(pos / n, 2) if n else None, "approval_ci95": _wilson(pos, n),
        "by_streamer": _group(rows, lambda r: [r["streamer"]]),
        "by_signal": _group(rows, lambda r: r["kinds"]),
        "by_situation": _group(rows, lambda r: [r["situation"]] if r["situation"] else []),
        "by_provider": _group(rows, lambda r: [r["provider"]] if r["provider"] else []),
        "duration_median": {"approved": med([r["duration"] for r in approved]),
                            "rejected": med([r["duration"] for r in rejected])},
        "current_score_auc": score_split,
        "start_delta_median": med(starts), "end_delta_median": med([r["end_delta"] for r in rows]),
        "rejection_reasons": dict(reasons), "recurring_problems": recurring, "diversity": diversity_note,
    }


# ------------------------------------------------------------------ the background job
def learn(db: Session) -> LearningRun:
    prof = profile(db)
    run = LearningRun(labels=prof["labels"], profile=prof, outcome="profile_only")
    prev_run = db.scalar(select(LearningRun).order_by(LearningRun.id.desc()))
    if prev_run is not None and prof["labels"] - prev_run.labels < MIN_NEW_LABELS and prev_run.outcome != "profile_only":
        run.detail = "poucos rótulos novos desde o último treino; perfil atualizado"
        db.add(run)
        return run
    try:
        model = ranker.train(db, created_by="learning-job")
    except ranker.NotEnoughData as exc:
        run.detail = f"{exc} (atual: {exc.counts})"
        db.add(run)
        return run
    run.ranker_id = model.id
    m = model.metrics
    active = db.scalar(select(RankerModel).where(RankerModel.status == "active"))
    va = (m.get("counts") or {})
    big_enough = va.get("val", 0) >= 20 and 5 <= va.get("val_pos", 0) <= va.get("val", 0) - 5
    better_than_heur = (m["ranker"]["auc"] or 0) >= (m["heuristic"]["auc"] or 0) + 0.03 and \
        (m["ranker"]["p_at_5"] or 0) >= (m["heuristic"]["p_at_5"] or 0)
    better_than_active = active is None or _auc_on_current_val(db, active) is None or \
        (m["ranker"]["auc"] or 0) >= (_auc_on_current_val(db, active) or 0) + 0.02
    if big_enough and better_than_heur and better_than_active:
        ranker.set_status(db, model, "active")
        run.outcome = "promoted"
        run.detail = f"ativado: AUC {m['ranker']['auc']} vs heurística {m['heuristic']['auc']} (val n={va.get('val')})"
    else:
        ranker.set_status(db, model, "shadow")
        run.outcome = "trained_shadow"
        why = []
        if not big_enough:
            why.append("validação pequena")
        if not better_than_heur:
            why.append("não superou a heurística com margem")
        if not better_than_active:
            why.append("não superou o modelo ativo")
        run.detail = "em sombra: " + ", ".join(why)
    db.add(run)
    return run


def _auc_on_current_val(db: Session, model: RankerModel) -> float | None:
    data = ranker.labelled(db).get("val", [])
    if not data:
        return None
    scores = [ranker.predict(model.params, model.feature_names, f) for f, _, _, _ in data]
    return ranker.auc(scores, [y for _, y, _, _ in data])


def status(db: Session) -> dict:
    last = db.scalar(select(LearningRun).order_by(LearningRun.id.desc()))
    active = db.scalar(select(RankerModel).where(RankerModel.status == "active"))
    shadow = db.scalar(select(RankerModel).where(RankerModel.status == "shadow"))
    total_reviews = db.scalar(select(func.count()).select_from(Review)) or 0
    since = utcnow() - timedelta(days=7)
    return {
        "human_labels": total_reviews,
        "labels_last_7d": db.scalar(select(func.count()).select_from(Review).where(Review.updated_at >= since)) or 0,
        "last_run": None if last is None else {"at": last.created_at.isoformat() + "Z", "outcome": last.outcome,
                                               "detail": last.detail, "labels": last.labels},
        "active_model": active.name if active else "heurística (nenhum classificador ativo)",
        "shadow_model": shadow.name if shadow else None,
    }
