"""Plan limits from ``commercial/catalog.json`` (the single source of truth shared with the commercial service).

Paid plans arrive inside signed snapshots, already resolved by the commercial service. This reader is only for
plans the HTS grants itself (courtesy / trial), so they get exactly the same limits as the paid plan.
Looked up at ``ASC_CATALOG_PATH``, ``/app/catalog.json`` (image) or ``commercial/catalog.json`` (repository).
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

PLANS = ("basic", "creator", "pro")


@lru_cache
def load() -> dict:
    here = Path(__file__).resolve()
    candidates = [os.environ.get("ASC_CATALOG_PATH", ""), "/app/catalog.json",
                  str(here.parents[2] / "commercial" / "catalog.json")]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return json.loads(Path(candidate).read_text(encoding="utf-8"))
    raise RuntimeError("catalog.json não encontrado (ASC_CATALOG_PATH)")


def limits_for(plan: str) -> dict:
    """Same shape as ``limitsFor`` in commercial/server/access.js."""
    p = load()["plans"][plan]
    return {"hours": p["hours"], "channels": p["channels"], "simultaneous": p["simultaneous"],
            "storage_gb": p["storageGb"], "export_4k": p["export4k"], "features": dict(p["features"])}
