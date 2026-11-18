"""Generate the controlled end-to-end test video ``gameplay_event.mp4``.

Timeline (approximate):
    0  - ~38 s  calm narration (TTS) over a synthetic "gameplay" scene + low game noise
    ~38 - 40 s  near silence
    ~40 - 44 s  sudden loud bright scream + "Oh my god! No way! No no no!" + visual explosion
    ~44 - end   calm narration again (resolution)

Requires an ffmpeg build with ``libflite`` (e.g. gyan.dev full build). The generated file
is committed to the repository so tests do not need flite.

    python tests/fixtures/make_fixture.py tests/fixtures/gameplay_event.mp4
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

CALM_1 = ("Alright chat, welcome back to the stream. Today we are going to try the casino heist again. "
          "Last time the police caught us right at the exit, so this time we have a better plan. "
          "I bought a faster car and parked it near the tunnel. Let me check the map real quick. "
          "Okay, the guards rotate every two minutes. We wait for the signal and then we go in quietly.")
SHOUT = "Oh my god! No way! No no no! What the hell!"
CALM_2 = ("Okay, okay. That was insane. The whole building just exploded behind me and I somehow survived. "
          "Chat, did you see that? I can not believe we made it out alive. Let me park the car and count the money.")


def run(args: list[str], cwd: Path | None = None) -> None:
    p = subprocess.run(args, capture_output=True, cwd=str(cwd) if cwd else None)
    if p.returncode != 0:
        raise RuntimeError(f"{' '.join(args)}\n{p.stderr.decode(errors='replace')[-1500:]}")


def duration(p: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", str(p)],
                         check=True, capture_output=True).stdout
    return float(json.loads(out)["format"]["duration"])


def tts(text: str, out: Path, gain: float) -> None:
    txt = out.with_suffix(".txt")
    txt.write_text(text, encoding="utf-8")
    # relative path: a drive letter colon would break the lavfi option string on Windows
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"flite=textfile={txt.name}:voice=slt",
         "-af", f"aresample=48000,volume={gain}", "-ac", "1", out.name], cwd=out.parent)


def main(dst: Path) -> None:
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        tts(CALM_1, t / "calm1.wav", 0.5)
        tts(SHOUT, t / "shout_voice.wav", 2.8)
        tts(CALM_2, t / "calm2.wav", 0.5)
        # scream: bright vibrato tone + noise burst, 1.6 s, loud
        run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=1400:sample_rate=48000:duration=1.6",
             "-f", "lavfi", "-i", "anoisesrc=color=white:sample_rate=48000:duration=1.6:amplitude=0.35",
             "-filter_complex", "[0]vibrato=f=7:d=0.6,volume=0.9[s];[s][1]amix=inputs=2:normalize=0,"
             "afade=t=in:d=0.05,afade=t=out:st=1.3:d=0.3", "-ac", "1", str(t / "scream.wav")])
        run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", "2.2", str(t / "silence.wav")])
        parts = ["calm1.wav", "silence.wav", "scream.wav", "shout_voice.wav", "calm2.wav"]
        (t / "list.txt").write_text("".join(f"file '{(t / p).as_posix()}'\n" for p in parts))
        run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(t / "list.txt"), "-c:a", "pcm_s16le",
             str(t / "voice.wav")])
        total = duration(t / "voice.wav") + 1.0
        event_t = duration(t / "calm1.wav") + 2.2
        # low continuous game ambience
        run(["ffmpeg", "-y", "-i", str(t / "voice.wav"), "-f", "lavfi", "-i",
             f"anoisesrc=color=brown:sample_rate=48000:amplitude=0.02:duration={total:.2f}",
             "-filter_complex", "[0][1]amix=inputs=2:normalize=0:duration=longest", "-ac", "2",
             str(t / "audio.wav")])
        # video: moving test pattern; explosion (bright orange flashes + noise) at the event
        ev0, ev1 = event_t, event_t + 3.5
        vf = (f"testsrc2=size=1280x720:rate=30:duration={total:.2f},"
              f"drawbox=x='mod(t*180,1280)':y=520:w=160:h=90:color=red@0.9:t=fill,"
              f"drawbox=x=0:y=0:w=1280:h=720:color=orange@0.85:t=fill:enable='between(t,{ev0:.2f},{ev1:.2f})',"
              f"noise=alls=60:allf=t:enable='between(t,{ev0:.2f},{ev1:.2f})',"
              f"drawtext=text='GTA RP TEST':x=40:y=40:fontsize=42:fontcolor=white:box=1:boxcolor=black@0.5")
        run(["ffmpeg", "-y", "-f", "lavfi", "-i", vf, "-i", str(t / "audio.wav"), "-shortest",
             "-c:v", "libx264", "-preset", "medium", "-crf", "38", "-maxrate", "900k", "-bufsize", "1800k", "-g", "60", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "128k", str(dst)])
        meta = {"event_t": round(event_t, 2), "duration": round(duration(dst), 2)}
        dst.with_suffix(".json").write_text(json.dumps(meta, indent=2))
        print(meta)


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "tests/fixtures/gameplay_event.mp4"))
