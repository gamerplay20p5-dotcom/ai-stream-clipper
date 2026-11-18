"""Vertical (1080x1920) short renderer built on FFmpeg filter graphs.

The renderer *executes* an :class:`~clipper.ai.schemas.EditPlan` (Phase 2). It never
generates new imagery: it reframes, zooms, fades, captions and normalises the original
footage.

Layouts
* ``gameplay`` - full horizontal frame (nothing cropped) over a blurred, darkened fill.
* ``zoom``     - centre 75 % of the frame, larger gameplay (may hide edge HUD).
* ``split``    - webcam crop on top, centre gameplay crop below (needs ``webcam_region``).
* ``action``   - full-height vertical crop that follows the action (plan crop keyframes).
* ``auto``     - ``split`` when a webcam region is known, otherwise ``gameplay``.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
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
    plan: dict | None = None  # EditPlan (clip-relative times)


@dataclass
class RenderResult:
    video: Path
    thumbnail: Path
    duration: float
    layout: str
    seconds: float
    audio: dict = field(default_factory=dict)
    applied: list[str] = field(default_factory=list)


def resolve_layout(layout: str, webcam_region: dict | None) -> str:
    if layout == "auto":
        return "split" if webcam_region else "gameplay"
    if layout == "split" and not webcam_region:
        return "gameplay"
    return layout


def _even(v: float) -> int:
    return max(2, int(v) // 2 * 2)


def piecewise(points: list[tuple[float, float]], var: str = "t") -> str:
    """FFmpeg expression interpolating ``(t, value)`` points linearly (clamped at the ends)."""
    pts = sorted(points)
    if not pts:
        return "0"
    if len(pts) == 1:
        return f"{pts[0][1]:.2f}"
    expr = f"{pts[-1][1]:.2f}"
    for (t0, v0), (t1, v1) in reversed(list(zip(pts[:-1], pts[1:]))):
        dt = max(1e-3, t1 - t0)
        seg = f"{v0:.2f}+({v1 - v0:.2f})*({var}-{t0:.3f})/{dt:.3f}"
        expr = f"if(lt({var},{t1:.3f}),{seg},{expr})"
    return f"if(lt({var},{pts[0][0]:.3f}),{pts[0][1]:.2f},{expr})"


def zoom_expr(zooms: list[dict], fps: int, ramp_in: float = 0.25, ramp_out: float = 0.35) -> str:
    """Zoom factor over time for zoompan (``on`` = output frame number)."""
    t = f"(on/{fps})"
    parts = []
    for z in zooms:
        t0, d, s = float(z["t"]), float(z["duration"]), float(z["scale"])
        env = f"max(0,min(1,min(({t}-{t0:.3f})/{ramp_in},({t0 + d:.3f}-{t})/{ramp_out})))"
        parts.append(f"({s - 1:.3f})*{env}")
    return "1+" + "+".join(parts) if parts else "1"


def build_filter(layout: str, src_w: int, src_h: int, webcam: dict | None, fps: int, has_subs: bool,
                 plan: dict | None = None, duration: float = 0.0) -> str:
    plan = plan or {}
    post: list[str] = []
    zooms = plan.get("zooms") or []
    if zooms:
        post.append(f"zoompan=z='{zoom_expr(zooms, fps)}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                    f":d=1:s={W}x{H}:fps={fps}")
    fi, fo = float(plan.get("fade_in") or 0), float(plan.get("fade_out") or 0)
    if fi > 0:
        post.append(f"fade=t=in:st=0:d={fi:.2f}")
    if fo > 0 and duration > fo + fi:
        post.append(f"fade=t=out:st={duration - fo:.2f}:d={fo:.2f}")
    if has_subs:
        post.append("ass=subs.ass")
    tail = ("[comp];[comp]" + ",".join(post) + "[v]") if post else "[v]"
    fpsf = f"fps={fps}"

    if layout == "split" and webcam:
        cx, cy = webcam["x"] * src_w, webcam["y"] * src_h
        cw, ch = max(16, webcam["w"] * src_w), max(16, webcam["h"] * src_h)
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
    if layout == "action":
        cw = _even(min(src_w, src_h * W / H))
        kfs = plan.get("crop_keyframes") or [{"t": 0.0, "cx": 0.5}]
        pts = [(float(k["t"]), min(max(0.0, float(k["cx"]) * src_w - cw / 2), src_w - cw)) for k in kfs]
        return (f"[0:v]{fpsf},crop={cw}:{_even(src_h)}:x='{piecewise(pts)}':y=0,"
                f"scale={W}:{H},setsar=1" + tail)
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
    if layout == "split":
        return 1020
    if layout == "action":
        return 420
    return 600


async def measure_loudness(source: Path, offset: float, duration: float, target: float) -> dict:
    """EBU R128 measurement of the clip's audio (first loudnorm pass)."""
    err = await media.run_stderr([media.FFMPEG, "-hide_banner", "-nostats", "-ss", f"{offset:.3f}", "-i", str(source),
                                  "-t", f"{duration:.3f}", "-vn", "-af",
                                  f"loudnorm=I={target}:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"],
                                 timeout=max(120, duration * 4))
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", err, re.S)
    if not m:
        return {}
    data = json.loads(m.group(0))
    return {k: float(v) for k, v in data.items() if k.startswith("input_") and v not in ("-inf", "inf")}


def linear_gain(measured: dict, target: float, ceiling_tp: float = -1.0) -> float | None:
    """Gain (dB) bringing integrated loudness towards ``target`` without pushing the true
    peak above ``ceiling_tp``: a pure linear gain, so the recording is never distorted or
    dynamically compressed."""
    if "input_i" not in measured or measured["input_i"] < -60:
        return None
    gain = target - measured["input_i"]
    if "input_tp" in measured:
        gain = min(gain, ceiling_tp - measured["input_tp"])
    return round(max(-20.0, min(20.0, gain)), 2)


async def render(spec: RenderSpec, workdir: Path) -> RenderResult:
    t0 = time.monotonic()
    workdir.mkdir(parents=True, exist_ok=True)
    plan = spec.plan or {}
    info = await media.probe(spec.source)
    vs = media.video_stream(info)
    if vs is None:
        raise media.MediaError("source has no video stream")
    src_w, src_h = int(vs["width"]), int(vs["height"])
    has_audio = media.audio_stream(info) is not None
    layout = resolve_layout(plan.get("layout") or spec.layout, plan.get("webcam_region") or spec.webcam_region)
    webcam = plan.get("webcam_region") or spec.webcam_region
    caption_style = plan.get("caption_style") or spec.caption_style
    applied = [f"layout:{layout}"]

    offset = max(0.0, spec.start - spec.source_start)
    duration = max(1.0, spec.end - spec.start)
    src_dur = media.duration_of(info)
    if src_dur and offset + duration > src_dur + 0.5:
        duration = max(1.0, src_dur - offset)

    words = [SubWord(w.start - spec.start, w.end - spec.start, w.text) for w in spec.words
             if spec.start <= w.start < spec.start + duration]
    title = plan.get("title", spec.title) if plan else spec.title
    title = title if (spec.show_title and layout != "split") else ""
    want_subs = caption_style != "none" or bool(title)
    if want_subs:
        ass = build_ass(words, style=caption_style, margin_v=caption_margin(layout), title=title,
                        duration=duration, emphasis=set(plan.get("emphasis_words") or []))
        (workdir / "subs.ass").write_text(ass, encoding="utf-8")
        applied.append(f"captions:{caption_style}")
    if plan.get("zooms"):
        applied.append(f"zoom:{len(plan['zooms'])}")

    audio_filters: list[str] = []
    audio_info: dict = {}
    if has_audio and plan.get("normalize_audio"):
        target = float(plan.get("target_lufs", -14.0))
        try:
            measured = await measure_loudness(spec.source.resolve(), offset, duration, target)
        except media.MediaError as exc:
            measured = {}
            log.warning("loudness measurement failed", extra={"err": str(exc)[:200]})
        gain = linear_gain(measured, target)
        audio_info = {"measured_lufs": measured.get("input_i"), "measured_tp": measured.get("input_tp"),
                      "gain_db": gain, "target_lufs": target}
        if gain is not None and abs(gain) >= 0.1:
            audio_filters.append(f"volume={gain}dB")
            applied.append(f"loudness:{gain:+.1f}dB")
    fi, fo = float(plan.get("fade_in") or 0), float(plan.get("fade_out") or 0)
    if has_audio and fi > 0:
        audio_filters.append(f"afade=t=in:st=0:d={fi:.2f}")
    if has_audio and fo > 0 and duration > fo + fi:
        audio_filters.append(f"afade=t=out:st={duration - fo:.2f}:d={fo:.2f}")
    if fi or fo:
        applied.append("fades")

    out = workdir / "clip.mp4"
    filt = build_filter(layout, src_w, src_h, webcam, spec.fps, bool(want_subs), plan, duration)
    args = [
        media.FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{offset:.3f}", "-i", str(spec.source.resolve()), "-t", f"{duration:.3f}",
        "-filter_complex", filt, "-map", "[v]",
    ]
    if has_audio:
        args += ["-map", "0:a:0"]
        if audio_filters:
            args += ["-af", ",".join(audio_filters)]
        args += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
    args += [
        "-c:v", "libx264", "-preset", spec.preset, "-crf", str(spec.crf), "-profile:v", "high",
        "-pix_fmt", "yuv420p", "-threads", str(spec.threads), "-movflags", "+faststart",
        str(out.name),
    ]
    log.info("rendering clip", extra={"layout": layout, "duration": round(duration, 2), "applied": applied})
    await media.run(args, cwd=workdir, timeout=max(600, duration * 40))

    thumb = workdir / "thumb.jpg"
    t_thumb = min(max(0.0, spec.peak - spec.start), max(0.0, duration - 0.5))
    await media.run([media.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t_thumb:.2f}", "-i",
                     str(out.name), "-frames:v", "1", "-vf", "scale=540:-2", "-q:v", "3", "thumb.jpg"],
                    cwd=workdir, timeout=120)
    return RenderResult(out, thumb, duration, layout, time.monotonic() - t0, audio_info, applied)
