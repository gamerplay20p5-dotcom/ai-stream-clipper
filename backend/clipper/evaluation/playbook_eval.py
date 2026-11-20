"""Offline evaluation of the editorial playbook against HUMAN decisions (approved vs rejected).

usage: python -m clipper.evaluation.playbook_eval data/eval/clips_labeled.json
Reports AUC (probability that a random approved clip scores above a random rejected one) for the current
score and for the playbook-adjusted score, plus per-criterion separation and the clips that moved most.
"""

from __future__ import annotations

import json
import random
import sys

from ..ai import playbook


def auc(pos: list[float], neg: list[float]) -> float | None:
    if not pos or not neg:
        return None
    w = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return w / (len(pos) * len(neg))


def bootstrap(pos, neg, n=1000, seed=7):
    rnd = random.Random(seed)
    vals = []
    for _ in range(n):
        p = [rnd.choice(pos) for _ in pos]
        q = [rnd.choice(neg) for _ in neg]
        vals.append(auc(p, q))
    vals.sort()
    return vals[int(0.025 * n)], vals[int(0.975 * n)]


def evaluate(rows: list[dict]) -> dict:
    lab = [r for r in rows if r["status"] in ("approved", "rejected")]
    out = {"n_approved": sum(r["status"] == "approved" for r in lab), "n_rejected": sum(r["status"] == "rejected" for r in lab)}
    scored = []
    for r in lab:
        c = playbook.assess(words=r["words"], start=r["start"], end=r["end"], peak=r["peak"], kinds=r["kinds"],
                            story=r.get("situation"), visual=r.get("visual"))
        scored.append((r, c, playbook.adjust(r["score"], c)))
    pos_old = [r["score"] for r, _, _ in scored if r["status"] == "approved"]
    neg_old = [r["score"] for r, _, _ in scored if r["status"] == "rejected"]
    pos_new = [s for r, _, s in scored if r["status"] == "approved"]
    neg_new = [s for r, _, s in scored if r["status"] == "rejected"]
    out["auc_old"], out["auc_new"] = auc(pos_old, neg_old), auc(pos_new, neg_new)
    out["ci_old"], out["ci_new"] = bootstrap(pos_old, neg_old), bootstrap(pos_new, neg_new)
    per = {}
    for k in playbook.WEIGHTS:
        p = [c.value(k) for r, c, _ in scored if r["status"] == "approved" and c.value(k) is not None]
        n = [c.value(k) for r, c, _ in scored if r["status"] == "rejected" and c.value(k) is not None]
        per[k] = {"auc": auc(p, n), "mean_approved": sum(p) / len(p) if p else None,
                  "mean_rejected": sum(n) / len(n) if n else None, "n": len(p) + len(n)}
    out["criteria"] = per
    out["offgame"] = {"approved": sum(c.offgame for r, c, _ in scored if r["status"] == "approved"),
                      "rejected": sum(c.offgame for r, c, _ in scored if r["status"] == "rejected")}
    # threshold view at the production minimum (55) and at the reviewers' practical bar (75)
    for thr in (70, 75, 80):
        out[f"kept_at_{thr}"] = {
            "old": (sum(s >= thr for s in pos_old), sum(s >= thr for s in neg_old)),
            "new": (sum(s >= thr for s in pos_new), sum(s >= thr for s in neg_new))}
    moved = sorted(scored, key=lambda x: x[2] - x[0]["score"])[:8]
    out["most_penalised"] = [(r["id"], r["status"], round(r["score"]), s, (r.get("title") or "")[:50]) for r, _, s in moved]
    return out


def main(path: str) -> None:
    rows = json.load(open(path, encoding="utf-8"))
    res = evaluate(rows)
    print(json.dumps(res, ensure_ascii=False, indent=1, default=lambda x: round(x, 3) if isinstance(x, float) else x))


if __name__ == "__main__":  # pragma: no cover
    main(sys.argv[1])
