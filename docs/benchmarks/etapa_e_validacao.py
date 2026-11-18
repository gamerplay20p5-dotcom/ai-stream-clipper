"""Stage E validation on real clips: the worker's code path (Whisper words -> turns -> voice
embeddings) + the API labelling (clusters, hypothesis) + old vs new captions."""
import collections, json, subprocess, sys, time
from pathlib import Path

import numpy as np

sys.path.insert(0, "backend")
from clipper.ai import speaker as spk
from clipper.analysis.transcribe import FasterWhisperProvider
from clipper.render.subtitles import CaptionOptions, SubWord, build_ass

d = Path(sys.argv[1])
meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
emb = spk.get_embedder("data/models", threads=2)
wh = FasterWhisperProvider("small", "int8", 4, Path("data/models/whisper"))
rows, stt_t, spk_t, audio_s = [], 0.0, 0.0, 0.0
per_streamer = collections.defaultdict(list)  # streamer -> [(clip, turn)]
for m in meta:
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", m["path"], "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                         capture_output=True).stdout
    x = np.frombuffer(raw, dtype=np.float32).copy()
    audio_s += len(x) / 16000
    for i in range(0, len(x), 160000):  # 10 s segments like the capture worker
        seg = x[i:i + 160000]
        t0 = time.perf_counter(); tr = wh.transcribe(seg, "pt"); stt_t += time.perf_counter() - t0
        words = [[w.start, w.end, w.word, w.probability] for w in tr.words]
        t0 = time.perf_counter(); turns = spk.embed_turns(seg, words, emb); spk_t += time.perf_counter() - t0
        for t in turns:
            per_streamer[m["streamer"]].append((m["id"], i / 16000 + t["start"], i / 16000 + t["end"], t["text"], t["emb"], words))
        m.setdefault("words", []).extend([[i / 16000 + w[0], i / 16000 + w[1], w[2]] for w in words])

report = {"audio_s": round(audio_s, 1), "whisper_s": round(stt_t, 1), "speaker_s": round(spk_t, 2),
          "speaker_ms_per_10s": round(spk_t / audio_s * 10 * 1000), "streamers": {}}
labels_by_clip = {}
profiles = {}
for name, turns in per_streamer.items():
    clusters = []
    for t in turns:
        _l, _c, clusters = spk.assign(t[4][0], None, clusters)
        for c in clusters:
            if c["id"] == int(_l.split(":")[1]) if _l.startswith("other:") else False:
                c["seconds"] = c.get("seconds", 0) + t[2] - t[1]
    total = sum(c.get("seconds", 0) for c in clusters)
    top = max(clusters, key=lambda c: c.get("seconds", 0))
    profiles[name] = top["centroid"]
    # relabel with the hypothesis, as relabel_session does
    cl2, lab = [], collections.Counter()
    for t in turns:
        l, conf, cl2 = spk.assign(t[4][0], top["centroid"], cl2)
        lab[l if not l.startswith("other:") else "other"] += t[2] - t[1]
        labels_by_clip.setdefault(t[0], []).append((t[1], t[2], l))
    report["streamers"][name] = {"turns": len(turns), "clusters": len(clusters),
                                 "dominant_share": round(top.get("seconds", 0) / max(total, 1e-6), 2),
                                 "seconds_by_label": {k: round(v, 1) for k, v in lab.items()}}
names = list(profiles)
if len(names) >= 2:
    report["profile_cosine_between_streamers"] = round(spk.cosine(profiles[names[0]], profiles[names[1]]), 3)
    # cross-check: the other streamer's turns against this profile must not be called "streamer"
    a, b = names[0], names[1]
    wrong = sum(1 for t in per_streamer[b] if spk.cosine(t[4][0], profiles[a]) >= spk.STREAMER_SIM)
    report[f"turns_of_{b}_labelled_as_{a}"] = f"{wrong}/{len(per_streamer[b])}"
    wrong = sum(1 for t in per_streamer[a] if spk.cosine(t[4][0], profiles[b]) >= spk.STREAMER_SIM)
    report[f"turns_of_{a}_labelled_as_{b}"] = f"{wrong}/{len(per_streamer[a])}"

# captions: old (v0.5.4 behaviour = no speakers) vs new (speaker-aware), validated by libass
cap = {"old_events": 0, "new_events": 0, "new_other_events": 0, "ffmpeg_ok": 0, "clips": 0}
for m in meta:
    turns = labels_by_clip.get(m["id"], [])
    def who(t):
        for a, b, l in turns:
            if a - 0.05 <= t <= b + 0.05:
                return l
        return "unknown"
    old = [SubWord(w[0], w[1], w[2]) for w in m.get("words", [])]
    new = [SubWord(w[0], w[1], w[2], who((w[0] + w[1]) / 2)) for w in m.get("words", [])]
    dur = max([w.end for w in old] + [5.0])
    a_old = build_ass(old, duration=dur)
    a_new = build_ass(new, duration=dur, opts=CaptionOptions(watermark="Malaio Cortes", cta="Siga para mais cortes"))
    cap["old_events"] += a_old.count("\nDialogue: 0,")
    cap["new_events"] += a_new.count("\nDialogue: 0,")
    cap["new_other_events"] += a_new.count(",Other,")
    ass = d / f"new_{m['id']}.ass"
    ass.write_text(a_new, encoding="utf-8")
    png = d / f"new_{m['id']}.png"
    t_mid = new[len(new) // 2].start + 0.1 if new else 1.0
    r = subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", f"{t_mid:.2f}", "-copyts", "-i", m["path"],
                        "-vf", f"ass={ass.name}", "-frames:v", "1", png.name], cwd=d, capture_output=True)
    cap["ffmpeg_ok"] += int(r.returncode == 0)
    cap["clips"] += 1
report["captions"] = cap
print(json.dumps(report, ensure_ascii=False, indent=1))
