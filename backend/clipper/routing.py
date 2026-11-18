"""AI worker selection for vision jobs (availability, VRAM, latency).

The queue stays pull-based; this module decides whether *this* worker should take a given
job right now:
1. a job asking for model M only goes to workers that serve M with enough VRAM;
2. if no online worker serves M, any vision worker takes it (heuristic fallback) so the
   pipeline never stalls;
3. among idle capable workers, the lowest measured latency wins; a slower worker only
   takes the job once it has waited longer than ``patience_s``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from .ai.registry import MODELS
from .models import Job, Worker

DEFAULT_LATENCY = {"GPU": 15.0, "CPU": 60.0}


def served_models(w: Worker) -> set[str]:
    return set((w.capabilities or {}).get("ai_models") or [])


def latency(w: Worker, model: str) -> float:
    ai = (w.metrics or {}).get("ai") or {}
    measured = ((ai.get("models") or {}).get(model) or {}).get("avg_latency_s")
    if measured:
        return float(measured)
    return DEFAULT_LATENCY["GPU" if (w.capabilities or {}).get("gpu") else "CPU"]


def fits(w: Worker, model: str) -> bool:
    caps = w.capabilities or {}
    spec = MODELS.get(model)
    if spec is None:
        return False
    if caps.get("gpu"):
        vram = float(caps.get("vram_total_gb") or 0)
        return vram == 0 or vram >= spec.min_vram_gb or bool(caps.get("cpu_fallback"))
    return True  # CPU inference: RAM is checked by the worker's container limit


def is_idle(w: Worker) -> bool:
    caps = w.capabilities or {}
    return int((w.metrics or {}).get("running_jobs", 0)) < int(caps.get("vision_concurrency", 1))


def vision_acceptor(worker: Worker, online: list[Worker], now: datetime,
                    patience_s: float = 20.0) -> Callable[[Job], bool]:
    vision_workers = [w for w in online if "vision" in (w.roles or [])]

    def accept(job: Job) -> bool:
        if job.type != "vision_analyze":
            return True
        model = (job.payload or {}).get("model")
        if not model:
            return True
        holders = [w for w in vision_workers if model in served_models(w) and fits(w, model)]
        if not holders:
            return True  # nobody can run the model -> heuristic fallback anywhere
        if worker.id not in {w.id for w in holders}:
            return False
        idle = [w for w in holders if is_idle(w) or w.id == worker.id]
        best = min(idle, key=lambda w: latency(w, model))
        if best.id == worker.id:
            return True
        waited = (now - job.created_at).total_seconds()
        return latency(worker, model) <= 1.5 * latency(best, model) or waited > patience_s

    return accept
