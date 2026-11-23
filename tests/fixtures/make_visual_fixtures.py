"""Generate deterministic video-only motion scenes using FFmpeg and NumPy."""

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

import numpy as np


def write_scene(path: Path, scene: str, *, duration: int = 24) -> None:
    width, height, fps = 320, 180, 20
    rng = np.random.default_rng(42)
    background = rng.integers(35, 90, size=(height // 3, width // 4), dtype=np.uint8)
    background = background.repeat(3, axis=0).repeat(4, axis=1)
    vehicle = rng.integers(115, 247, size=(12, 18), dtype=np.uint8).repeat(3, axis=0).repeat(3, axis=1)
    yy, xx = np.indices((56, 68), dtype=np.float32)
    xx -= 34
    yy -= 28
    command = [
        "ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pixel_format", "gray", "-video_size",
        f"{width}x{height}", "-framerate", str(fps), "-i", "-", "-an", "-c:v", "libx264", "-preset",
        "ultrafast", "-threads", "2", "-g", str(fps * 2), "-pix_fmt", "yuv420p", str(path),
    ]
    with subprocess.Popen(command, stdin=subprocess.PIPE) as ff:
        assert ff.stdin is not None
        for i in range(duration * fps):
            t = i / fps
            frame = background.copy()
            dt = max(0.0, min(2.0, t - 12.0))
            if scene in ("fall", "impact", "rollover"):
                x = 90.0 if scene == "fall" else 73.0 + 38 * min(dt, 1.1)
                y = 41.0 + 43 * dt if scene == "fall" else 104.0
                if scene == "impact":
                    x -= 45 * max(0, min(dt - 1.1, 0.55))
                angle = math.radians(55 * dt) if scene == "rollover" else 0.0
                c, s = math.cos(angle), math.sin(angle)
                u = (c * xx - s * yy + 27).astype(int)
                v = (s * xx + c * yy + 18).astype(int)
                keep = (u >= 0) & (u < vehicle.shape[1]) & (v >= 0) & (v < vehicle.shape[0])
                crop = frame[int(y) - 28:int(y) + 28, int(x) - 34:int(x) + 34]
                crop[keep] = vehicle[v[keep], u[keep]]
            elif scene == "distractors":
                if 5 <= t < 8:
                    frame = np.roll(frame, int((t - 5) * 90), axis=1)
                if 11 <= t < 11.4:
                    frame.fill(220)  # common game flash
                if 15 <= t:
                    frame = frame[::-1].copy()  # hard cut into otherwise static footage
                frame[1:11, 8:90] = int(t * 211) % 256  # peripheral HUD
            ff.stdin.write(frame.tobytes())
        ff.stdin.close()
        if ff.wait():
            raise RuntimeError(f"FFmpeg failed to generate {path}")


def generate(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    events = {"fall": 13.0, "impact": 13.1, "rollover": 13.25}
    videos = []
    for scene in (*events, "distractors"):
        path = root / f"silent_{scene}.mp4"
        write_scene(path, scene)
        videos.append({"video": path.name, "duration": 24.0, "language": "pt" if scene == "fall" else "en",
                       "events": [{"label": scene, "peak": events[scene], "must_include": [10.0, 16.0]}]
                       if scene in events else []})
    (root / "annotations.json").write_text(json.dumps({"videos": videos}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    generate(Path(sys.argv[1]))
