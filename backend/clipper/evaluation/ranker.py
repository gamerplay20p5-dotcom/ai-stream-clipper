"""Lightweight learned ranker over observable candidate features (logistic regression, numpy).

* Labels come only from human reviews (excellent/good = 1, bad/should_not_exist = 0; fair and
  manual test clips are excluded).
* Train on the ``train`` split, validate on ``val`` (split by session, never by moment).
* Always compared with the heuristic score on the SAME validation examples. A model is saved
  as ``candidate``; an admin may put it in ``shadow`` (scores shown, no decisions) or
  ``active`` only when it beats the heuristic on validation. Previous versions stay restorable.
"""

from __future__ import annotations

import math

import numpy as np
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..db import utcnow
from ..models import Candidate, RankerModel
from . import taxonomy
from .metrics import reviewed_pairs

MIN_TRAIN, MIN_PER_CLASS, MIN_VAL = 30, 8, 10
EXCLUDE = {"score", "raw_total"}  # the heuristic output itself is the baseline, not an input


class NotEnoughData(ValueError):
    def __init__(self, msg: str, counts: dict):
        super().__init__(msg)
        self.counts = counts


def _features_from_snapshot(ai: dict) -> dict:
    """Observable dims stored with the example (clips older than the candidates table)."""
    b = ai.get("breakdown") or {}
    f = {f"dim_{k}": float(v) for k, v in b.items() if isinstance(v, (int, float)) and k != "visual"}
    if not f:
        return {}
    kinds = ai.get("kinds") or []
    for k in ("laughter", "scream", "keyword", "speech_burst", "loudness_spike", "silence_break", "energy_burst",
              "bright_burst"):
        f[f"n_{k}"] = float(kinds.count(k) if isinstance(kinds, list) else 0)
    if ai.get("start") is not None and ai.get("end") is not None:
        f["duration_s"] = float(ai["end"] - ai["start"])
    return f


def feature_names(rows: list[dict]) -> list[str]:
    names: set[str] = set()
    for f in rows:
        for k, v in f.items():
            if k not in EXCLUDE and (isinstance(v, (int, float)) or v is None):
                names.add(k)
    return sorted(names)


def vectorize(f: dict, names: list[str]) -> np.ndarray:
    out = []
    for n in names:
        v = f.get(n)
        out.append(0.0 if v is None or (isinstance(v, float) and math.isnan(v)) else float(v))
    return np.array(out, dtype=float)


def labelled(db: Session) -> dict[str, list[tuple[dict, int, float | None, int]]]:
    """split -> [(features, label, heuristic_score, session_id)]"""
    out: dict[str, list] = {"train": [], "val": []}
    for ex, rv in reviewed_pairs(db):
        if rv.rating == "fair":
            continue
        cand = db.get(Candidate, ex.candidate_id) if ex.candidate_id else None
        feats = cand.features if cand is not None and cand.features else _features_from_snapshot(ex.ai or {})
        if not feats:
            continue
        score = cand.score if cand is not None else (ex.ai or {}).get("candidate_score", (ex.ai or {}).get("score"))
        y = 1 if rv.rating in taxonomy.POSITIVE else 0
        out.setdefault(ex.split, []).append((feats, y, score, ex.session_id or 0))
    return out


def _fit(X: np.ndarray, y: np.ndarray, l2: float = 1.0, iters: int = 3000, lr: float = 0.1) -> np.ndarray:
    n, d = X.shape
    w = np.zeros(d + 1)
    pos = max(1, int(y.sum()))
    neg = max(1, n - pos)
    sw = np.where(y == 1, n / (2 * pos), n / (2 * neg))  # class balance
    Xb = np.hstack([X, np.ones((n, 1))])
    for _ in range(iters):
        p = 1 / (1 + np.exp(-np.clip(Xb @ w, -30, 30)))
        g = Xb.T @ (sw * (p - y)) / n
        g[:-1] += l2 * w[:-1] / n
        w -= lr * g
    return w


def auc(scores: list[float], labels: list[int]) -> float | None:
    pos = [s for s, y in zip(scores, labels) if y == 1]
    neg = [s for s, y in zip(scores, labels) if y == 0]
    if not pos or not neg:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return round(wins / (len(pos) * len(neg)), 3)


def p_at_k(scores, labels, sessions, k: int = 5) -> float | None:
    by: dict[int, list] = {}
    for s, y, sid in zip(scores, labels, sessions):
        by.setdefault(sid, []).append((s, y))
    vals = [sum(y for _, y in sorted(v, key=lambda x: -x[0])[:k]) / min(k, len(v)) for v in by.values() if len(v) >= 2]
    return round(float(np.mean(vals)), 3) if vals else None


def predict(params: dict, names: list[str], features: dict) -> float:
    x = (vectorize(features, names) - np.array(params["mean"])) / np.array(params["std"])
    z = float(np.dot(np.array(params["w"][:-1]), x) + params["w"][-1])
    return round(1 / (1 + math.exp(-max(-30, min(30, z)))), 4)


def train(db: Session, created_by: str, l2: float = 1.0) -> RankerModel:
    data = labelled(db)
    tr, va = data.get("train", []), data.get("val", [])
    counts = {"train": len(tr), "train_pos": sum(y for _, y, _, _ in tr), "val": len(va),
              "val_pos": sum(y for _, y, _, _ in va)}
    if (len(tr) < MIN_TRAIN or counts["train_pos"] < MIN_PER_CLASS or len(tr) - counts["train_pos"] < MIN_PER_CLASS
            or len(va) < MIN_VAL or counts["val_pos"] == 0 or counts["val_pos"] == len(va)):
        raise NotEnoughData(
            f"avaliações insuficientes para treinar com confiança: treino precisa de ≥{MIN_TRAIN} exemplos "
            f"(≥{MIN_PER_CLASS} positivos e ≥{MIN_PER_CLASS} negativos) e validação de ≥{MIN_VAL} com as duas classes",
            counts)
    names = feature_names([f for f, _, _, _ in tr])
    Xtr = np.vstack([vectorize(f, names) for f, _, _, _ in tr])
    mean, std = Xtr.mean(axis=0), Xtr.std(axis=0)
    std[std < 1e-9] = 1.0
    ytr = np.array([y for _, y, _, _ in tr], dtype=float)
    w = _fit((Xtr - mean) / std, ytr, l2=l2)
    params = {"w": w.tolist(), "mean": mean.tolist(), "std": std.tolist(), "l2": l2}
    vs = [predict(params, names, f) for f, _, _, _ in va]
    vy = [y for _, y, _, _ in va]
    vh = [h if h is not None else 0.0 for _, _, h, _ in va]
    vsid = [s for _, _, _, s in va]
    metrics = {
        "ranker": {"auc": auc(vs, vy), "p_at_5": p_at_k(vs, vy, vsid)},
        "heuristic": {"auc": auc(vh, vy), "p_at_5": p_at_k(vh, vy, vsid)},
        "counts": counts,
        "top_weights": sorted(zip(names, (w[:-1]).tolist()), key=lambda x: -abs(x[1]))[:12],
    }
    r, h = metrics["ranker"], metrics["heuristic"]
    metrics["beats_heuristic"] = bool(r["auc"] is not None and h["auc"] is not None and r["auc"] > h["auc"]
                                      and (r["p_at_5"] or 0) >= (h["p_at_5"] or 0))
    n = (db.scalar(select(RankerModel.id).order_by(RankerModel.id.desc())) or 0) + 1
    model = RankerModel(name=f"ranker-v{n}", kind="logistic", status="candidate", feature_names=names, params=params,
                        metrics=metrics, n_train=len(tr), n_val=len(va), created_by=created_by)
    db.add(model)
    db.flush()
    return model


def set_status(db: Session, model: RankerModel, status: str) -> None:
    """Promote/demote. Only one shadow and one active at a time; 'active' requires validation."""
    if status not in ("candidate", "shadow", "active", "archived"):
        raise ValueError("invalid status")
    if status == "active" and not (model.metrics or {}).get("beats_heuristic"):
        raise ValueError("este modelo não superou a heurística na validação; não pode ser ativado")
    if status in ("shadow", "active"):
        db.execute(update(RankerModel).where(RankerModel.status == status, RankerModel.id != model.id)
                   .values(status="archived"))
    model.status = status
    if status == "active":
        model.activated_at = utcnow()


def scoring_model(db: Session) -> RankerModel | None:
    """The model that scores new candidates: active first, else shadow."""
    return (db.scalar(select(RankerModel).where(RankerModel.status == "active"))
            or db.scalar(select(RankerModel).where(RankerModel.status == "shadow")))
