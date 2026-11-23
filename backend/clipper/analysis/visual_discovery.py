"""Propose segment-relative action windows from local motion on a low-rate grayscale stream."""
from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from statistics import median

import cv2
import numpy as np


SAMPLE_HZ = 4
SIZE = (192, 108)


class DiscoveryTimeout(RuntimeError):
    """The segment could not be scanned within its time budget (the decoder was stopped)."""


def _samples(path: Path, duration: float, deadline: float | None) -> Iterator[tuple[float, np.ndarray | None]]:
    """Grayscale 192x108 samples at 4 Hz from the start of the file: ``(t, frame)``. OpenCV, one decoder thread.

    Measured alternative: ffmpeg sampling with the loop filter skipped decodes ~20% faster in isolation but changes
    pixels enough to lose detections (deblocking off) and was slower end to end, so it is not used."""
    if hasattr(cv2, "CAP_PROP_N_THREADS"):
        cap = cv2.VideoCapture(str(path), cv2.CAP_FFMPEG, [cv2.CAP_PROP_N_THREADS, 1])
    else:
        cap = cv2.VideoCapture(str(path))
    try:
        fps = cap.get(cv2.CAP_PROP_FPS)
        if not math.isfinite(fps) or not 1 <= fps <= 240:
            fps = 30.0
        next_sample = 0.0
        for index in range(math.ceil(duration * fps)):
            if deadline is not None and time.monotonic() > deadline:
                raise DiscoveryTimeout("visual scan exceeded its budget")
            if not cap.grab():
                break
            t = index / fps
            if t + 1e-7 < next_sample:
                continue
            next_sample = (math.floor(t * SAMPLE_HZ + 1e-7) + 1) / SAMPLE_HZ
            ok, frame = cap.retrieve()
            if not ok or frame is None:
                yield t, None
                continue
            small = cv2.resize(frame, SIZE, interpolation=cv2.INTER_AREA)
            yield t, cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    finally:
        cap.release()


# Largest moving box still treated as a localized object (fractions of the analysed frame)
MAX_BOX_W = 0.85
MAX_BOX_H = 0.95


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
    # The player's own vehicle in third person is large and central: only a near full-frame box is ruled out here;
    # widespread changes (cuts, flashes, pans) are already rejected by the coverage and activity checks above.
    if area < before.size * 0.009 or w < 8 or h < 8 or w > MAX_BOX_W * width or h > MAX_BOX_H * height:
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



def _compact_change(changed: np.ndarray, activity: float) -> tuple[float, np.ndarray] | None:
    """Largest changed region when the change is localized: (area fraction, box [x0, y0, x1, y1]) or None.

    Used when tracking fails: a fast rotation or impact of a large object (the player's vehicle) breaks optical flow,
    yet the change stays compact. Widespread changes (cuts, flashes, pans) are rejected like in _analyze_pair."""
    coverage = cv2.resize(changed.astype(np.float32), (6, 4), interpolation=cv2.INTER_AREA)
    if np.count_nonzero(coverage > 0.12) > 10 or activity > 0.32:
        return None
    count, _labels, stats, _ = cv2.connectedComponentsWithStats(cv2.dilate(changed, np.ones((5, 5), np.uint8)))
    if count < 2:
        return None
    label = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, w, h, area = stats[label]
    fraction = area / changed.size
    height, width = changed.shape
    if fraction < 0.03 or w > MAX_BOX_W * width or h > MAX_BOX_H * height:
        return None
    return fraction, np.array([x, y, x + w, y + h], dtype=float)


def _local_onset(changed: np.ndarray, recent: deque, box: np.ndarray) -> bool:
    """The region was quiet before and changes now: continuously animated elements (sweeps, scrolling UI, looping
    effects) keep changing in the same place and never pass."""
    if len(recent) < 3:
        return False
    x0, y0, x1, y1 = (int(v) for v in box)
    before = [float(m[y0:y1, x0:x1].mean()) for m in list(recent)[:-1]]
    now = float(changed[y0:y1, x0:x1].mean())
    # The quietest earlier sample: the one right before may already belong to the event (a rollover starts early)
    return now >= max(0.12, 3 * min(before) + 0.04)


def _same_region(a: np.ndarray, b: np.ndarray) -> bool:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return union > 0 and inter / union >= 0.25


def discover_visual(path: Path, duration: float, webcam_region: dict | None = None, *,
                    timeout_s: float | None = None) -> list[dict]:
    """Return at most four localized motion hypotheses, sorted by time.

    Time follows the sample order (4 per second from the start of the segment), so TS timestamp resets do not
    propagate to clip offsets. Raises DiscoveryTimeout when ``timeout_s`` elapses (the decoder is stopped).
    """
    if not math.isfinite(duration) or duration <= 0:
        return []
    deadline = time.monotonic() + timeout_s if timeout_s else None
    proposals = []
    previous = last = older = None
    last_compact = None
    recent_masks: deque[np.ndarray] = deque(maxlen=3)
    history: deque[float] = deque(maxlen=11)
    active = None
    for t, gray in _samples(path, duration, deadline):
        if gray is None:  # unreadable frame: restart the analysis state
            previous = last = older = last_compact = None
            history.clear()
            recent_masks.clear()
            continue
        if webcam_region:
            x, y, w, h = [float(webcam_region[k]) for k in "xywh"]
            gray[max(0, int(y * SIZE[1])):max(0, math.ceil((y + h) * SIZE[1])),
                 max(0, int(x * SIZE[0])):max(0, math.ceil((x + w) * SIZE[0]))] = 0
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
        # Tracking lost on a compact region that keeps changing in the same place for two samples: an abrupt
        # localized change (fast rotation or impact of a large object, such as the player's own vehicle). Lower
        # confidence than tracked signals; the onset rule below still requires a rise over the recent activity, and
        # the VLM must confirm it before anything is published.
        compact = _compact_change(changed, activity) if now is None else None
        if (signal is None and compact is not None and last_compact is not None
                and _same_region(compact[1], last_compact[1]) and _local_onset(changed, recent_masks, last_compact[1])):
            signal = "abrupt_change", min(0.66, 0.58 + 0.4 * max(compact[0], last_compact[0]))
        last_compact = compact
        recent_masks.append(changed)
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
