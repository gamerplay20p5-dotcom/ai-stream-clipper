"""Generate the Phase 2 benchmark videos with KNOWN events (ground truth in annotations.json).

* chase_crash.mp4      - calm talk -> police chase builds up (motion + engine + urgent speech)
                         -> crash (flash + impact sound) -> loud verbal reaction -> resolution.
                         The interesting context starts well before the loudness peak.
* loud_game_noise.mp4  - the streamer keeps talking calmly while the GAME produces loud
                         explosions and gunfire. Not clip-worthy: classic loudness false positive.

Synthetic data (flite TTS + generated sound/visuals): validates mechanics and timestamps,
NOT perception of real emotion. Requires an ffmpeg build with libflite.

    python tests/fixtures/make_fixtures_v2.py tests/fixtures
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


def run(args: list[str], cwd: Path | None = None) -> None:
    p = subprocess.run(args, capture_output=True, cwd=str(cwd) if cwd else None)
    if p.returncode != 0:
        raise RuntimeError(" ".join(args) + "\n" + p.stderr.decode(errors="replace")[-1500:])


def duration(p: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", str(p)],
                         check=True, capture_output=True).stdout
    return float(json.loads(out)["format"]["duration"])


def tts(text: str, out: Path, gain: float) -> float:
    txt = out.with_suffix(".txt")
    txt.write_text(text, encoding="utf-8")
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"flite=textfile={txt.name}:voice=slt",
         "-af", f"aresample=48000,volume={gain}", "-ac", "1", out.name], cwd=out.parent)
    return duration(out)


def silence(out: Path, secs: float) -> None:
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", f"{secs:.2f}", str(out)])


def explosion(out: Path, secs: float = 1.8, amp: float = 0.9) -> None:
    # low, broadband burst with a fast attack and exponential decay
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"anoisesrc=color=brown:sample_rate=48000:duration={secs}:amplitude=1",
         "-f", "lavfi", "-i", f"anoisesrc=color=pink:sample_rate=48000:duration={secs}:amplitude=1",
         "-filter_complex", f"[0][1]amix=inputs=2:normalize=0,lowpass=f=1800,volume={amp},"
         f"afade=t=in:d=0.01,afade=t=out:st=0.15:d={secs - 0.15:.2f}:curve=exp", "-ac", "1", str(out)])


def gunfire(out: Path, shots: int = 8, gap: float = 0.12, amp: float = 0.8) -> None:
    parts = []
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anoisesrc=color=white:sample_rate=48000:duration=0.06:amplitude=1",
             "-af", f"highpass=f=120,volume={amp},afade=t=out:st=0.005:d=0.055:curve=exp", "-ac", "1",
             str(t / "shot.wav")])
        silence(t / "gap.wav", gap)
        for _ in range(shots):
            parts += ["shot.wav", "gap.wav"]
        (t / "l.txt").write_text("".join(f"file '{(t / p).as_posix()}'\n" for p in parts))
        run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(t / "l.txt"), "-c:a", "pcm_s16le", str(out)])


def concat(parts: list[Path], out: Path) -> None:
    lst = out.with_suffix(".list.txt")
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts))
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-ar", "48000", "-ac", "1",
         "-c:a", "pcm_s16le", str(out)])


def mix_bed(voice: Path, bed: Path, out: Path) -> None:
    run(["ffmpeg", "-y", "-i", str(voice), "-i", str(bed), "-filter_complex",
         "[0][1]amix=inputs=2:normalize=0:duration=longest", "-ac", "2", str(out)])


def encode(video_filter: str, audio: Path, dst: Path) -> None:
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", video_filter, "-i", str(audio), "-shortest",
         "-c:v", "libx264", "-preset", "medium", "-crf", "38", "-maxrate", "900k", "-bufsize", "1800k", "-g", "60",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", str(dst)])


def chase_crash(out_dir: Path) -> dict:
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        d_calm = tts("Okay chat, we are just driving around the city. Nothing special today, "
                     "I am going to the garage to change the color of the car. Then we can buy some food "
                     "and maybe do a small delivery job for the mechanic. The weather is nice today.",
                     t / "calm.wav", 0.5)
        silence(t / "s1.wav", 1.0)
        d_chase = tts("Wait, the police are right behind us. Go go go. Take the left turn. Faster, faster, "
                      "they are still following us. There is a helicopter now. Turn right at the gas station. "
                      "Cut through the parking lot, the bridge is blocked. Keep going, keep going, we can lose them "
                      "in the tunnel. They are so close. Down the hill, watch the traffic.", t / "chase.wav", 0.7)
        silence(t / "s2.wav", 0.6)
        explosion(t / "crash.wav", 1.6, 0.9)
        d_react = tts("Oh no! No no no! I crashed the car!", t / "react.wav", 2.8)
        d_res = tts("Okay, okay. We survived. The cops lost us behind the wall. "
                    "Let me call a tow truck and fix this car.", t / "res.wav", 0.5)
        concat([t / "calm.wav", t / "s1.wav", t / "chase.wav", t / "s2.wav", t / "crash.wav", t / "react.wav",
                t / "res.wav"], t / "voice.wav")
        total = duration(t / "voice.wav") + 1.0
        chase_t = d_calm + 1.0
        crash_t = chase_t + d_chase + 0.6
        react_end = crash_t + 1.6 + d_react
        # engine hum rising during the chase (game ambience)
        run(["ffmpeg", "-y", "-f", "lavfi", "-i",
             f"aevalsrc='0.05*sin(2*PI*(60+25*max(0,t-{chase_t:.2f}))*t)*between(t,{chase_t:.2f},{crash_t:.2f})'"
             f":s=48000:d={total:.2f}", "-ac", "1", str(t / "engine.wav")])
        mix_bed(t / "voice.wav", t / "engine.wav", t / "audio.wav")
        speed = f"if(lt(t,{chase_t:.2f}),60,60+90*(t-{chase_t:.2f}))"
        vf = (f"testsrc2=size=1280x720:rate=30:duration={total:.2f},"
              f"drawbox=x='mod(t*{speed},1180)':y=430:w=110:h=70:color=blue@0.95:t=fill:"
              f"enable='gte(t,{chase_t:.2f})',"
              f"drawbox=x='mod(t*{speed}+60,1180)':y=520:w=110:h=70:color=red@0.95:t=fill,"
              f"drawbox=x=0:y=0:w=1280:h=720:color=white@0.9:t=fill:enable='between(t,{crash_t:.2f},{crash_t + 0.5:.2f})',"
              f"drawbox=x=0:y=0:w=1280:h=720:color=darkred@0.5:t=fill:enable='between(t,{crash_t + 0.5:.2f},{react_end:.2f})',"
              f"noise=alls=50:allf=t:enable='between(t,{crash_t:.2f},{crash_t + 1.5:.2f})',"
              f"drawtext=text='GTA RP TEST - CHASE':x=40:y=40:fontsize=36:fontcolor=white:box=1:boxcolor=black@0.5")
        dst = out_dir / "chase_crash.mp4"
        encode(vf, t / "audio.wav", dst)
    return {"video": dst.name, "duration": round(duration(dst), 2), "language": "en", "events": [{
        "label": "perseguição termina em batida e o streamer reage",
        "clip_worthy": True,
        "context_start": round(chase_t, 2),  # the chase - needed to understand the crash
        "peak": round(crash_t, 2),
        "reaction_end": round(react_end, 2),
        "must_include": [round(chase_t + 2.0, 2), round(react_end, 2)],
    }]}


def loud_game_noise(out_dir: Path) -> dict:
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        lines = ["Alright, let me check the inventory. I have two pistols and some ammo.",
                 "I think we should go to the store and buy food before the mission.",
                 "The mission starts at the docks, we need to be careful with the boss.",
                 "Okay let me save the game and read the chat for a moment.",
                 "Thanks for the follow everyone, the next mission is in five minutes.",
                 "I need to change my clothes and get a better car for the docks.",
                 "Someone in the chat asked about the settings, I will show them later.",
                 "The boss pays well for this job, so we should not waste time.",
                 "Let me drink some water and then we continue the stream."]
        voice_parts = []
        for i, line in enumerate(lines):
            tts(line, t / f"l{i}.wav", 0.5)
            silence(t / f"g{i}.wav", 1.2)
            voice_parts += [t / f"l{i}.wav", t / f"g{i}.wav"]
        concat(voice_parts, t / "voice.wav")
        total = duration(t / "voice.wav") + 1.0
        # game sounds at known times, the streamer does not react
        explosion(t / "boom.wav", 2.6, 3.0)  # ~+18 dB over the voice, like a real in-game blast
        gunfire(t / "guns.wav", 16, 0.07, 3.0)
        e1, g1, e2 = 12.0, 24.0, 33.0
        run(["ffmpeg", "-y", "-i", str(t / "voice.wav"), "-i", str(t / "boom.wav"), "-i", str(t / "guns.wav"),
             "-i", str(t / "boom.wav"), "-filter_complex",
             f"[1]adelay={int(e1 * 1000)}[a];[2]adelay={int(g1 * 1000)}[b];[3]adelay={int(e2 * 1000)}[c];"
             "[0][a][b][c]amix=inputs=4:normalize=0:duration=first", "-ac", "2", str(t / "audio.wav")])
        vf = (f"testsrc2=size=1280x720:rate=30:duration={total:.2f},"
              f"drawbox=x='mod(t*120,1180)':y=520:w=110:h=70:color=red@0.95:t=fill,"
              f"drawbox=x=0:y=0:w=1280:h=720:color=orange@0.7:t=fill:enable='between(t,{e1},{e1 + 0.6})+between(t,{e2},{e2 + 0.6})',"
              f"drawtext=text='GTA RP TEST - LOBBY':x=40:y=40:fontsize=36:fontcolor=white:box=1:boxcolor=black@0.5")
        dst = out_dir / "loud_game_noise.mp4"
        encode(vf, t / "audio.wav", dst)
    return {"video": dst.name, "duration": round(duration(dst), 2), "language": "en", "events": [],
            "distractors": [{"label": "explosão do jogo", "t": e1}, {"label": "tiros do jogo", "t": g1},
                            {"label": "explosão do jogo", "t": e2}]}


def main(out_dir: Path) -> None:
    ann_path = out_dir / "annotations.json"
    ann = {"version": 1, "note": "synthetic ground truth; times in seconds", "videos": []}
    first = json.loads((out_dir / "gameplay_event.json").read_text())
    ann["videos"].append({"video": "gameplay_event.mp4", "duration": first["duration"], "language": "en", "events": [{
        "label": "silêncio, grito e reação verbal", "clip_worthy": True, "context_start": first["event_t"] - 8,
        "peak": first["event_t"] + 1.8, "reaction_end": first["event_t"] + 7.0,
        "must_include": [first["event_t"] - 1.0, first["event_t"] + 6.0]}]})
    ann["videos"].append(chase_crash(out_dir))
    ann["videos"].append(loud_game_noise(out_dir))
    ann_path.write_text(json.dumps(ann, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(ann, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "tests/fixtures"))
