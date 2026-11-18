"""Vertical (1080x1920) short renderer built on FFmpeg filter graphs.

Layouts
* ``gameplay`` - full horizontal frame (nothing cropped) over a blurred, darkened fill.
* ``zoom``     - centre 75 % of the frame, larger gameplay (may hide edge HUD).
* ``split``    - webcam crop on top, centre gameplay crop below (needs ``webcam_region``).
* ``auto``     - ``split`` when a webcam region is known, otherwise ``gameplay``.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

from .. import media
from .subtitles import SubWord, build_ass

log = logging.getLogger(__name__)

W, H = 1080, 1920
SPLIT_TOP = 704  # webcam band height in split layout


@dataclass
class RenderSpec:
    source: Path
    source_start: float  # session offset at which ``source`` begins
    start: float  # clip start (session offset)
    end: float
    peak: float
    words: list[SubWord]  # session offsets
    title: str = ""
    caption_style: str = "bold"
    layout: str = "auto"
    webcam_region: dict | None = None  # normalised {x,y,w,h}
    show_title: bool = True
    preset: str = "veryfast"
    crf: int = 21
    fps: int = 30
    threads: int = 4


@dataclass
class RenderResult:
    video: Path
    thumbnail: Path
    duration: float
    layout: str
    seconds: float


def resolve_layout(layout: str, webcam_region: dict | None) -> str:
    if layout == "auto":
        return "split" if webcam_region else "gameplay"
    if layout == "split" and not webcam_region:
        return "gameplay"
    return layout


def _even(v: float) -> int:
    return max(2, int(v) // 2 * 2)


def build_filter(layout: str, src_w: int, src_h: int, webcam: dict | None, fps: int, has_subs: bool) -> str:
    tail = "[vs];[vs]ass=subs.ass[v]" if has_subs else "[v]"
    fpsf = f"fps={fps}"
    if layout == "split" and webcam:
        cx, cy = webcam["x"] * src_w, webcam["y"] * src_h
        cw, ch = max(16, webcam["w"] * src_w), max(16, webcam["h"] * src_h)
        # expand the webcam crop to the band aspect ratio (cover)
        band_ar = W / SPLIT_TOP
        if cw / ch < band_ar:
            nw = min(src_w, ch * band_ar)
            cx -= (nw - cw) / 2
            cw = nw
            ch = min(ch, cw / band_ar)
        else:
            nh = min(src_h, cw / band_ar)
            cy -= (nh - ch) / 2
            ch = nh
            cw = min(cw, ch * band_ar)
        cx = min(max(0, cx), src_w - cw)
        cy = min(max(0, cy), src_h - ch)
        bottom_h = H - SPLIT_TOP
        gw = min(src_w, src_h * W / bottom_h)
        return (
            f"[0:v]{fpsf},split=2[a][b];"
            f"[a]crop={_even(cw)}:{_even(ch)}:{int(cx)}:{int(cy)},scale={W}:{SPLIT_TOP}:force_original_aspect_ratio="
            f"increase,crop={W}:{SPLIT_TOP},setsar=1[top];"
            f"[b]crop={_even(gw)}:{_even(src_h)}:(iw-ow)/2:0,scale={W}:{bottom_h}:force_original_aspect_ratio=increase,"
            f"crop={W}:{bottom_h},setsar=1[bot];"
            f"[top][bot]vstack=inputs=2" + tail
        )
    if layout == "zoom":
        fg = f"[b]crop=iw*0.75:ih:(iw-ow)/2:0,scale={W}:-2,setsar=1[fg];"
    else:
        fg = f"[b]scale={W}:-2,setsar=1[fg];"
    return (
        f"[0:v]{fpsf},split=2[a][b];"
        f"[a]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=8:2,"
        f"eq=brightness=-0.12:saturation=1.2,scale={W}:{H},setsar=1[bg];"
        + fg
        + "[bg][fg]overlay=(W-w)/2:(H-h)/2-90" + tail
    )


def caption_margin(layout: str) -> int:
    # distance from the bottom edge to the caption baseline
    return 1020 if layout == "split" else 600


async def render(spec: RenderSpec, workdir: Path) -> RenderResult:
    t0 = time.monotonic()
    workdir.mkdir(parents=True, exist_ok=True)
    info = await media.probe(spec.source)
    vs = media.video_stream(info)
    if vs is None:
        raise media.MediaError("source has no video stream")
    src_w, src_h = int(vs["width"]), int(vs["height"])
    has_audio = media.audio_stream(info) is not None
    layout = resolve_layout(spec.layout, spec.webcam_region)

    offset = max(0.0, spec.start - spec.source_start)
    duration = max(1.0, spec.end - spec.start)
    src_dur = media.duration_of(info)
    if src_dur and offset + duration > src_dur + 0.5:
        duration = max(1.0, src_dur - offset)

    words = [SubWord(w.start - spec.start, w.end - spec.start, w.text) for w in spec.words
             if spec.start <= w.start < spec.start + duration]
    want_subs = spec.caption_style != "none" or (spec.show_title and spec.title)
    title = spec.title if (spec.show_title and layout != "split") else ""
    if want_subs:
        ass = build_ass(words, style=spec.caption_style, margin_v=caption_margin(layout), title=title,
                        duration=duration)
        (workdir / "subs.ass").write_text(ass, encoding="utf-8")

    out = workdir / "clip.mp4"
    filt = build_filter(layout, src_w, src_h, spec.webcam_region, spec.fps, bool(want_subs))
    args = [
        media.FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{offset:.3f}", "-i", str(spec.source.resolve()), "-t", f"{duration:.3f}",
        "-filter_complex", filt, "-map", "[v]",
    ]
    if has_audio:
        args += ["-map", "0:a:0", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
    args += [
        "-c:v", "libx264", "-preset", spec.preset, "-crf", str(spec.crf), "-profile:v", "high",
        "-pix_fmt", "yuv420p", "-threads", str(spec.threads), "-movflags", "+faststart",
        str(out.name),
    ]
    log.info("rendering clip", extra={"layout": layout, "duration": round(duration, 2), "offset": round(offset, 2)})
    await media.run(args, cwd=workdir, timeout=max(600, duration * 30))

    thumb = workdir / "thumb.jpg"
    t_thumb = min(max(0.0, spec.peak - spec.start), max(0.0, duration - 0.5))
    await media.run([media.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t_thumb:.2f}", "-i",
                     str(out.name), "-frames:v", "1", "-vf", "scale=540:-2", "-q:v", "3", "thumb.jpg"],
                    cwd=workdir, timeout=120)
    return RenderResult(out, thumb, duration, layout, time.monotonic() - t0)
