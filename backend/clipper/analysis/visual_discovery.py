"""Propose segment-relative action windows from local motion on a low-rate grayscale stream."""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from statistics import median

import cv2
import numpy as np


@dataclass
class _Motion:
    center: np.ndarray
    velocity: np.ndarray
    angle: float


def _analyze_pair(before: np.ndarray, after: np.ndarray, changed: np.ndarray, activity: float) -> _Motion | None:
    # Widespread changes include hard cuts, flashes and textured camera pans.
    coverage = cv2.resize(changed.astype(np.float32), (6, 4), interpolation=cv2.INTER_AREA)
    if np.count_nonzero(coverage > 0.12) > 10 or activity > 0.32:
        return None
    mask = cv2.dilate(changed, np.ones((5, 5), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    if count < 2:
        return None
    label = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, w, h, area = stats[label]
    height, width = before.shape
    if area < before.size * 0.009 or w < 8 or h < 8 or w > 0.7 * width or h > 0.8 * height:
        return None
    region = np.uint8(labels == label)
    old = cv2.goodFeaturesToTrack(before, 64, 0.015, 3, mask=region)
    if old is None or len(old) < 4:
        return None
    new, status, error = cv2.calcOpticalFlowPyrLK(before, after, old, None, winSize=(15, 15), maxLevel=2)
    if new is None:
        return None
    valid = (status.ravel() == 1) & (error.ravel() < 28)
    old, new = old[valid, 0], new[valid, 0]
    shifts = new - old
    moving = (np.linalg.norm(shifts, axis=1) > 1.4) & (np.linalg.norm(shifts, axis=1) < 0.23 * width)
    if np.count_nonzero(moving) < 4:
        return None
    old, new = old[moving], new[moving]
    transform, inliers = cv2.estimateAffinePartial2D(old, new, ransacReprojThreshold=2)
    if transform is None or np.count_nonzero(inliers) < max(4, 0.55 * len(old)):
        return None
    support = inliers.ravel().astype(bool)
    points = old[support]
    angle = math.degrees(math.atan2(transform[1, 0], transform[0, 0]))
    if np.linalg.norm(np.ptp(points, axis=0)) < 12:
        angle = 0
    return _Motion(np.array([x + w / 2, y + h / 2]), np.median((new - old)[support], axis=0), angle)


def _signal(last: _Motion, now: _Motion) -> tuple[str, float] | None:
    # Components are the union of both positions in a frame pair, so their
    # midpoints should advance close to the neighbouring mean displacement.
    expected = (last.velocity + now.velocity) / 2
    if np.linalg.norm(now.center - last.center - expected) > 7:
        return None
    if abs(now.angle) >= 6 and now.angle * last.angle > 15:
        return "rotation", min(0.80, 0.62 + abs(now.angle) / 130)
    change = np.linalg.norm(now.velocity - last.velocity)
    prev_speed = np.linalg.norm(last.velocity)
    turning = np.dot(now.velocity, last.velocity) < 0.65 * prev_speed * np.linalg.norm(now.velocity)
    slowing = np.linalg.norm(now.velocity) < 0.6 * prev_speed
    if change > 5 and (turning or slowing):
        return "trajectory_change", min(0.82, 0.60 + change / 85)
    dy = now.velocity[1]
    if dy > 3.5 and last.velocity[1] > 2 and dy > abs(now.velocity[0]) * 1.25:
        return "vertical_displacement", min(0.76, 0.56 + dy / 80)
    return None


def discover_visual(path: Path, duration: float, webcam_region: dict | None = None) -> list[dict]:
    """Return at most four localized motion hypotheses, sorted by time.

    Time follows decoded frame order at nominal FPS, so TS timestamp resets do not
    propagate to clip offsets. Only sampled frames are retrieved and processed.
    """
    if not math.isfinite(duration) or duration <= 0:
        return []
    # Keep the decoder from taking over the audio worker when several segments run together.
    if hasattr(cv2, "CAP_PROP_N_THREADS"):
        cap = cv2.VideoCapture(str(path), cv2.CAP_FFMPEG, [cv2.CAP_PROP_N_THREADS, 1])
    else:
        cap = cv2.VideoCapture(str(path))
    proposals = []
    try:
        fps = cap.get(cv2.CAP_PROP_FPS)
        if not math.isfinite(fps) or not 1 <= fps <= 240:
            fps = 30.0
        previous = last = older = None
        history: deque[float] = deque(maxlen=11)
        next_sample = 0.0
        active = None
        for index in range(math.ceil(duration * fps)):
            if not cap.grab():
                break
            t = index / fps
            if t + 1e-7 < next_sample:
                continue
            next_sample = (math.floor(t * 4 + 1e-7) + 1) / 4
            ok, frame = cap.retrieve()
            if not ok or frame is None:
                previous = last = older = None
                history.clear()
                continue
            small = cv2.resize(frame, (192, 108), interpolation=cv2.INTER_AREA)
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            if webcam_region:
                x, y, w, h = [float(webcam_region[k]) for k in "xywh"]
                gray[max(0, int(y * 108)):max(0, math.ceil((y + h) * 108)),
                     max(0, int(x * 192)):max(0, math.ceil((x + w) * 192))] = 0
            # Strip fixed peripheral HUDs before motion analysis.
            gray = gray[9:-10, 10:-10]
            if previous is None:
                previous = gray
                continue
            changed = (cv2.absdiff(previous, gray) > 18).astype(np.uint8)
            activity = float(changed.mean())
            now = _analyze_pair(previous, gray, changed, activity)
            signal = _signal(last, now) if last is not None and now is not None else None
            # A sudden stop needs two preceding coherent displacements and a
            # quiet frame. Losing the object to a cut or flash is insufficient.
            if now is None and last is not None and older is not None:
                speed = np.linalg.norm(last.velocity)
                coherent = np.dot(older.velocity, last.velocity) > 0.5 * speed**2
                expected = (older.velocity + last.velocity) / 2
                same_track = np.linalg.norm(last.center - older.center - expected) <= 7
                if speed > 8 and coherent and same_track and activity < 0.003:
                    signal = "trajectory_change", min(0.74, 0.58 + speed / 100)
            previous, older, last = gray, last, now
            history.append(activity)
            if signal is None:
                continue
            name, confidence = signal
            if active is None or t - active["end"] > 0.75:
                # A near-constant animation can have many local trajectory changes.
                # Require a short rise over the preceding activity to start a proposal.
                recent = list(history)
                prior, onset = recent[:-4], recent[-4:]
                if len(prior) < 3 or max(onset) > 0.32 or max(onset) < max(0.012, 1.5 * median(prior) + 0.005):
                    continue
                active = dict(start=max(0.0, t - 0.5), peak=t, end=t, confidence=confidence, signal=name)
                proposals.append(active)
            elif confidence > active["confidence"]:
                active.update(peak=t, confidence=confidence, signal=name)
            active["end"] = t
    finally:
        cap.release()
    # A fall should continue across more than one sampled pair. A brief
    # trajectory reversal can be meaningful even when its impulse is short.
    sustained = [row for row in proposals if row["signal"] != "vertical_displacement"
                 or row["end"] - row["start"] >= 0.65]
    ranked = sorted(sustained, key=lambda row: row["confidence"], reverse=True)[:4]
    for row in ranked:
        row["end"] = min(duration, row["end"] + 0.25)
        for key in ("start", "peak", "end", "confidence"):
            row[key] = round(float(row[key]), 4)
    return sorted(ranked, key=lambda row: row["peak"])
