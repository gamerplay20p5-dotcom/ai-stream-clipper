"""6B validation on the same 12 real clips: original vs chosen structure, objective metrics only."""
import json, os, statistics, subprocess, sys, time
from pathlib import Path

import numpy as np

sys.path.insert(0, "backend")
from clipper.ai import structure as st
from clipper.ai.acoustic import get_classifier
from clipper.analysis.transcribe import FasterWhisperProvider

d = Path(sys.argv[1])
meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
exec(open(os.path.join(os.environ["TEMP"], "asc_api.py"), encoding="utf-8").read())
wh = FasterWhisperProvider("small", "int8", 4, Path("data/models/whisper"))
yam = get_classifier("data/models")
rows, plan_ms = [], []
for m in meta:
    info = c.get(f"/api/clips/{m['id']}").json()
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", m["path"], "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                         capture_output=True).stdout
    x = np.frombuffer(raw, dtype=np.float32).copy()
    dur = len(x) / 16000
    words = [(w.start, w.end, w.word) for w in wh.transcribe(x, "pt").words]
    reac = []
    if yam is not None:
        y = yam.classify(x)
        hop = y["hop"]
        for name, vals in y["cats"].items():
            if any(k in name.lower() for k in ("laugh", "scream", "shout", "yell", "cheer", "gasp")):
                reac += [(i * hop, i * hop + hop) for i, v in enumerate(vals) if v >= 0.5]
    reac.sort()
    peak = max(0.0, min(dur - 0.5, float(info["peak"]) - float(info["start"])))  # clip-relative (rendered = original)
    mo = st.Moment(start=0.0, end=dur, peak=peak, words=words, reactions=reac, story=None, media=[(0.0, dur)],
                   max_len=90.0, min_gap=0.9)
    t0 = time.perf_counter()
    key, why = st.choose(mo, kinds=info.get("kinds") or [])
    plan = st.build(key, mo)
    plan_ms.append((time.perf_counter() - t0) * 1000)
    o = st.metrics(st.build("O", mo), mo)
    n = st.metrics(plan, mo) if plan.applicable else {}
    rows.append({"id": m["id"], "streamer": m["streamer"], "structure": key, "why": why, "applicable": plan.applicable,
                 "orig": o, "new": n, "meaningful": plan.applicable and st.meaningful(o, n), "reactions": len(reac)})
    print(m["id"], key, "| orig", o["duration"], "s, pico", o["time_to_peak"], "s, sem fala", o["dead_ratio"],
          "| novo", n.get("duration"), "s, pico", n.get("time_to_peak"), "s, sem fala", n.get("dead_ratio"),
          "| reações ok", n.get("reactions_kept"), "| palavras", n.get("words_kept"), "/", n.get("words_in_span"))
ok = [r for r in rows if r["applicable"]]
mean = lambda k, side: round(statistics.mean(r[side][k] for r in ok), 2)
print(json.dumps({
    "clips": len(rows), "applicable": len(ok), "meaningful": sum(r["meaningful"] for r in rows),
    "structures": {k: sum(1 for r in rows if r["structure"] == k) for k in "ABCDE"},
    "duration_mean": [mean("duration", "orig"), mean("duration", "new")],
    "time_to_peak_mean": [mean("time_to_peak", "orig"), mean("time_to_peak", "new")],
    "dead_ratio_mean": [mean("dead_ratio", "orig"), mean("dead_ratio", "new")],
    "reactions_kept": sum(r["new"].get("reactions_kept", False) for r in ok),
    "chronological": sum(r["new"].get("chronological", False) for r in ok),
    "original_words_dropped_pct": round(100 * sum(r["new"]["words_dropped_vs_original"] for r in ok) / max(1, sum(r["new"]["words_original"] for r in ok)), 1),
    "min_time_to_peak": min(r["new"]["time_to_peak"] for r in ok),
    "words_kept_pct": round(100 * sum(r["new"]["words_kept"] for r in ok) / max(1, sum(r["new"]["words_in_span"] for r in ok)), 1),
    "planner_ms_mean": round(statistics.mean(plan_ms), 2)}, ensure_ascii=False, indent=1))
(d / "structure_report.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
