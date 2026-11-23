"""Silent gameplay-like scenes built from the project's own original images (commercial/public/media), for testing the
multimodal pipeline with something a VLM can interpret (a red car on a neon avenue), until authorized real gameplay is
available. No third-party footage. No audio track (silent events).

    python tests/fixtures/make_realistic_fixtures.py <out dir>

Scenes (24 s, 1280x720, 30 fps): rollover (the car rolls over 1.25 times at ~12.5 s and rests on its side in a dust cloud), collision (the car swerves into a
parked car at ~12.6 s and stops tilted), driving (negative control: same drive, no event). Writes annotations.json.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
MEDIA = ROOT / "commercial" / "public" / "media"
W, H, FPS, DURATION = 1280, 720, 30, 24.0
EVENT = {"rollover": 12.5, "collision": 12.6}


def _load(name: str) -> np.ndarray:
    img = cv2.imread(str(MEDIA / name), cv2.IMREAD_COLOR)
    if img is None:  # OpenCV builds without WebP: decode through Pillow
        from PIL import Image
        img = cv2.cvtColor(np.array(Image.open(MEDIA / name).convert("RGB")), cv2.COLOR_RGB2BGR)
    return cv2.resize(img, (W, H), interpolation=cv2.INTER_CUBIC)


def _car(scene: np.ndarray, box: tuple[int, int, int, int]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Cut the car (sprite + soft alpha) and return the background with the car replaced by the neighbouring road
    (the road on both sides, shifted over the car and cross-faded; plain inpainting left a dark ghost car)."""
    x0, y0, x1, y1 = box
    sprite = scene[y0:y1, x0:x1].copy()
    mask = np.zeros(scene.shape[:2], np.uint8)
    cv2.ellipse(mask, ((x0 + x1) // 2, (y0 + y1) // 2), ((x1 - x0) // 2, (y1 - y0) // 2), 0, 0, 360, 255, -1)
    w = x1 - x0
    left = np.roll(scene, w, axis=1).astype(np.float32)  # road just left of the car, moved over it
    right = np.roll(scene, -w, axis=1).astype(np.float32)
    ramp = np.clip((np.arange(W, dtype=np.float32) - x0) / max(1, w), 0, 1)[None, :, None]
    fill = left * (1 - ramp) + right * ramp
    soft = cv2.GaussianBlur(cv2.dilate(mask, np.ones((31, 31), np.uint8)).astype(np.float32) / 255.0, (0, 0), 9)[..., None]
    background = (scene * (1 - soft) + fill * soft).astype(np.uint8)
    background = cv2.GaussianBlur(background, (0, 0), 1.2)
    alpha = mask[y0:y1, x0:x1].astype(np.float32) / 255.0
    alpha = cv2.GaussianBlur(alpha, (0, 0), 4)
    return background, sprite, alpha


def _smoke(frame: np.ndarray, center: tuple[float, float], age: float, rng: np.random.Generator) -> None:
    """Grey dust/smoke cloud growing for a few seconds after the crash."""
    if age <= 0:
        return
    layer = np.zeros(frame.shape[:2], np.float32)
    for k in range(14):
        r = int(30 + 90 * min(1.0, age / 2.5) * (0.6 + 0.4 * ((k * 37) % 10) / 10))
        dx = ((k * 53) % 21 - 10) * 14 * min(1.0, age)
        dy = -abs(((k * 29) % 13 - 6)) * 10 * min(1.0, age)
        cv2.circle(layer, (int(center[0] + dx), int(center[1] + dy)), r, 1.0, -1)
    layer = cv2.GaussianBlur(layer, (0, 0), 25) * (0.75 * max(0.0, 1 - age / 9))
    frame[:] = (frame * (1 - layer[..., None]) + 175 * layer[..., None]).astype(np.uint8)


def _paste(frame: np.ndarray, sprite: np.ndarray, alpha: np.ndarray, center: tuple[float, float], angle: float, scale: float) -> None:
    h, w = sprite.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, scale)
    m[0, 2] += center[0] - w / 2
    m[1, 2] += center[1] - h / 2
    warped = cv2.warpAffine(sprite, m, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    a = cv2.warpAffine(alpha, m, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)[..., None]
    frame[:] = (frame * (1 - a) + warped * a).astype(np.uint8)


def write_scene(path: Path, scene: str) -> None:
    base = _load("moment-chase.webp")
    box = (418, 236, 862, 556)  # the red car in the 1280x720 frame
    background, car, alpha = _car(base, box)
    other_bg = _load("moment-palms.webp")
    _, parked, parked_alpha = _car(other_bg, (470, 360, 790, 590))
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    rng = np.random.default_rng(7)
    home = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
    for i in range(int(DURATION * FPS)):
        t = i / FPS
        # driving: gentle camera bob and road flicker, identical in every scene
        bob = 3 * math.sin(t * 2.1)
        frame = np.roll(background, int(bob), axis=0).copy()
        frame = cv2.convertScaleAbs(frame, alpha=1.0 + 0.04 * math.sin(t * 7.3), beta=0)
        x, y, angle, scale = home[0] + 10 * math.sin(t * 0.9), home[1] + bob, 0.0, 1.0
        if scene == "rollover" and t >= EVENT["rollover"] - 0.6:
            # Seen from the chase camera, a rollover is a roll around the car's long axis: an in-plane rotation of
            # the rear view. 1.25 turns in 1.8 s while sliding to the right edge, then it rests on its side.
            dt = t - (EVENT["rollover"] - 0.6)
            spin = min(1.0, dt / 1.8)
            ease = 1 - (1 - spin) ** 2
            angle = -450 * ease
            x += 330 * ease
            y += -70 * math.sin(math.pi * spin) + 70 * ease
            scale = 1.0 - 0.18 * ease
        if scene == "collision":
            px, py = home[0] - 300, home[1] + 30
            _paste(frame, parked, parked_alpha, (px, py), 0.0, 0.8)
            if t >= EVENT["collision"] - 1.2:
                dt = t - (EVENT["collision"] - 1.2)
                if dt < 1.2:  # swerve into the parked car
                    x += (px + 170 - x) * (dt / 1.2) ** 1.6
                    angle = -14 * (dt / 1.2)
                else:  # impact: stops tilted against it, a short shake
                    x, angle = px + 170, -14 - 9 * min(1.0, (dt - 1.2) / 0.3)
                    if dt - 1.2 < 0.5:
                        frame = np.roll(frame, int(rng.integers(-8, 9)), axis=1)
        _paste(frame, car, alpha, (x, y), angle, scale)
        if scene == "rollover":
            _smoke(frame, (x, y + 40), t - EVENT["rollover"], rng)
        noise = rng.normal(0, 3, frame.shape).astype(np.int16)
        writer.write(np.clip(frame.astype(np.int16) + noise, 0, 255).astype(np.uint8))
    writer.release()


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "realistic-fixtures")
    out.mkdir(parents=True, exist_ok=True)
    videos = []
    for scene in ("rollover", "collision", "driving"):
        path = out / f"realistic-{scene}.mp4"
        write_scene(path, scene)
        videos.append({"video": path.name, "duration": DURATION, "language": "pt",
                       "events": [{"t": EVENT[scene], "kind": scene}] if scene in EVENT else []})
        print("escrito", path)
    (out / "annotations.json").write_text(json.dumps({"videos": videos}, indent=1), encoding="utf-8")
