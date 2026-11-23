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
from .subtitles import CaptionOptions, SubWord, build_ass

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
    captions: dict | None = None  # CaptionOptions fields (Phase 5 E)
    encoder: str = "libx264"
    hw_timeout: float = 90.0  # seconds before a hardware encode is abandoned for libx264


@dataclass
class RenderResult:
    video: Path
    thumbnail: Path
    duration: float
    layout: str
    seconds: float
    audio: dict = field(default_factory=dict)
    applied: list[str] = field(default_factory=list)


def _check_output(path: Path, duration: float) -> None:
    """A hardware encode that "succeeded" must still produce a playable H.264 1080x1920 file of the right length."""
    try:
        info = media.probe_sync(path)
        v = media.video_stream(info)
        got = media.duration_of(info) or 0.0
    except Exception as exc:  # noqa: BLE001 - any unreadable output means: re-encode in software
        raise media.MediaError(f"hardware output unreadable: {exc}") from exc
    if not v or v.get("codec_name") != "h264" or (v.get("width"), v.get("height")) != (W, H) or got < duration - 1.0:
        raise media.MediaError(f"hardware output invalid ({v.get('codec_name') if v else None}, {got:.1f}s)")


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
    badge = plan.get("badge_avatar")
    if badge:  # Phase 6C: OUR avatar in a circle, under the captions (name drawn by the ASS file)
        tail = _badge_tail(post, has_subs, plan)
    else:
        if has_subs:
            post.append("ass=subs.ass")
        tail = ("[comp];[comp]" + ",".join(post) + "[v]") if post else "[v]"
    fpsf = f"fps={fps}"
    band = _split_band(plan)

    if layout == "split" and webcam:
        cx, cy = webcam["x"] * src_w, webcam["y"] * src_h
        cw, ch = max(16, webcam["w"] * src_w), max(16, webcam["h"] * src_h)
        band_ar = W / band
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
        bottom_h = H - band
        gw = min(src_w, src_h * W / bottom_h)
        order = "[bot][top]" if plan.get("webcam_position") == "bottom" else "[top][bot]"
        return (
            f"[0:v]{fpsf},split=2[a][b];"
            f"[a]crop={_even(cw)}:{_even(ch)}:{int(cx)}:{int(cy)},scale={W}:{band}:force_original_aspect_ratio="
            f"increase,crop={W}:{band},setsar=1[top];"
            f"[b]crop={_even(gw)}:{_even(src_h)}:(iw-ow)/2:0,scale={W}:{bottom_h}:force_original_aspect_ratio=increase,"
            f"crop={W}:{bottom_h},setsar=1[bot];"
            f"{order}vstack=inputs=2" + tail
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


def caption_options(raw: dict | None) -> CaptionOptions:
    """Unknown keys are ignored (an older API or a newer worker never breaks the render)."""
    known = set(CaptionOptions.__dataclass_fields__) - {"extra"}
    return CaptionOptions(**{k: v for k, v in (raw or {}).items() if k in known})


BADGE_D = 112  # avatar diameter (px) of the identification badge
BADGE_POS = {"top_left": (60, 170), "top_right": (W - 60 - BADGE_D, 170),
             "bottom_left": (60, H - 420 - BADGE_D), "bottom_right": (W - 160 - BADGE_D, H - 420 - BADGE_D)}


def _split_band(plan: dict) -> int:
    """Webcam band height for the split layout (format profile: 20-60% of the frame)."""
    pct = plan.get("webcam_height_pct")
    if pct:
        return _even(H * max(20, min(60, float(pct))) / 100)
    return SPLIT_TOP


def _badge_tail(post: list[str], has_subs: bool, plan: dict) -> str:
    x, y = BADGE_POS.get(plan.get("badge_position") or "top_left", BADGE_POS["top_left"])
    d = BADGE_D
    circle = (f"[1:v]scale={d}:{d}:force_original_aspect_ratio=increase,crop={d}:{d},format=rgba,"
              f"geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='if(lte(hypot(X-{d / 2},Y-{d / 2}),{d / 2 - 1}),255,0)'[av]")
    head = f"[comp];[comp]{','.join(post)}[pre];" if post else "[comp];[comp]null[pre];"
    end = ",ass=subs.ass" if has_subs else ""
    return f"{head}{circle};[pre][av]overlay={x}:{y}:format=auto{end}[v]"


def caption_margin(layout: str, plan: dict | None = None) -> int:
    # distance from the bottom edge to the caption baseline
    plan = plan or {}
    if layout == "split":
        band = _split_band(plan)
        if plan.get("webcam_position") == "bottom":
            return band + 90  # captions at the bottom of the gameplay, above the webcam
        return max(420, H - band - 196) if plan.get("webcam_height_pct") else 1020
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


def _segments(plan: dict) -> list[dict]:
    segs = [s for s in (plan.get("segments") or []) if float(s["b"]) - float(s["a"]) > 0.05]
    return segs if (len(segs) > 1 or any(s.get("role") == "teaser" for s in segs)) else []


async def concat_segments(spec: RenderSpec, segs: list[dict], workdir: Path, has_audio: bool) -> tuple[Path, float]:
    """Pass 1 of a structural edit: the kept source segments, in plan order, as one intermediate
    file (near-lossless). 30 ms audio fades at every join avoid clicks; video joins are hard cuts."""
    lo = min(float(s["a"]) for s in segs)
    ss = max(0.0, lo - spec.source_start - 1.0)  # input seek a little before the first segment
    base = spec.source_start + ss
    parts, labels = [], []
    for i, s in enumerate(segs):
        a, b = float(s["a"]) - base, float(s["b"]) - base
        d = b - a
        parts.append(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS,fps={spec.fps}[v{i}]")
        labels.append(f"[v{i}]")
        if has_audio:
            parts.append(f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS,"
                         f"afade=t=in:st=0:d=0.03,afade=t=out:st={max(0.0, d - 0.03):.3f}:d=0.03[a{i}]")
            labels.append(f"[a{i}]")
    n = len(segs)
    graph = ";".join(parts) + ";" + "".join(labels) + f"concat=n={n}:v=1:a={1 if has_audio else 0}[v]" + \
        ("[a]" if has_audio else "")
    out = workdir / "structure.mkv"
    span = max(float(s["b"]) for s in segs) - base + 0.5
    args = [media.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{ss:.3f}", "-i",
            str(spec.source.resolve()), "-t", f"{span:.3f}", "-filter_complex", graph, "-map", "[v]"]
    if has_audio:
        args += ["-map", "[a]", "-c:a", "pcm_s16le"]
    args += ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", "-threads", str(spec.threads), out.name]
    await media.run(args, cwd=workdir, timeout=max(600, span * 20))
    return out, round(sum(float(s["b"]) - float(s["a"]) for s in segs), 3)


def remap_for_segments(spec: RenderSpec, plan: dict, segs: list[dict]) -> tuple[RenderSpec, dict, list]:
    """Move every session-time element (words, zooms, crop keyframes, faces, peak) to the edited
    timeline. Removed moments disappear; the teaser gets its on-screen label."""
    from ..ai.structure import TEASER_LABEL, map_time

    total = sum(float(s["b"]) - float(s["a"]) for s in segs)
    words = []
    pos, bounds = 0.0, []
    for s in segs:  # output-time interval of each segment
        d = float(s["b"]) - float(s["a"])
        bounds.append((pos, pos + d))
        pos += d
    for w in spec.words:
        t = map_time(segs, w.start)
        if t is not None:
            seg_end = next((b for a, b in bounds if a - 1e-6 <= t < b), t + 0.05)
            end = min(t + max(0.05, w.end - w.start), seg_end)  # never continue after a cut
            words.append(SubWord(t, max(end, t + 0.05), w.text, w.speaker))
    p = dict(plan)

    def rel(items: list[dict], key: str = "t") -> list[dict]:
        out = []
        for it in items or []:
            t = map_time(segs, spec.start + float(it[key]))
            if t is not None:
                out.append({**it, key: round(t, 3)})
        return out

    p["zooms"] = rel(plan.get("zooms") or [])
    p["crop_keyframes"] = rel(plan.get("crop_keyframes") or []) or ([{"t": 0.0, "cx": 0.5}]
                                                                    if plan.get("crop_keyframes") else [])
    p["face_track"] = rel(plan.get("face_track") or [])
    notes, pos = [], 0.0
    for s in segs:
        d = float(s["b"]) - float(s["a"])
        if s.get("role") == "teaser":
            notes.append((pos, pos + d, TEASER_LABEL))
        pos += d
    peak = map_time(segs, spec.peak)
    new = RenderSpec(**{**spec.__dict__, "source_start": 0.0, "start": 0.0, "end": total,
                        "peak": peak if peak is not None else total / 2, "words": [], "plan": p})
    return new, p, [words, notes]


async def render(spec: RenderSpec, workdir: Path) -> RenderResult:
    t0 = time.monotonic()
    workdir.mkdir(parents=True, exist_ok=True)
    plan = spec.plan or {}
    info = await media.probe(spec.source)
    notes: list = []
    mapped_words = None
    segs = _segments(plan)
    if segs:  # Phase 6B structural edit: pass 1 builds the edited sequence, pass 2 is the usual render
        has_a = media.audio_stream(info) is not None
        mezz, _total = await concat_segments(spec, segs, workdir, has_a)
        spec, plan, (mapped_words, notes) = remap_for_segments(spec, plan, segs)
        spec = RenderSpec(**{**spec.__dict__, "source": mezz})
        info = await media.probe(mezz)
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

    words = mapped_words if mapped_words is not None else [
        SubWord(w.start - spec.start, w.end - spec.start, w.text, w.speaker) for w in spec.words
        if spec.start <= w.start < spec.start + duration]
    title = plan.get("title", spec.title) if plan else spec.title
    title = title if (spec.show_title and layout != "split") else ""
    want_subs = caption_style != "none" or bool(title)
    if want_subs:
        ass = build_ass(words, style=caption_style, margin_v=caption_margin(layout, plan), title=title,
                        duration=duration, emphasis=set(plan.get("emphasis_words") or []),
                        opts=caption_options(spec.captions), notes=notes)
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
        "-ss", f"{offset:.3f}", "-i", str(spec.source.resolve()),
    ]
    if plan.get("badge_avatar"):
        args += ["-i", str(Path(plan["badge_avatar"]).resolve())]  # input 1: our avatar (still image)
    args += ["-t", f"{duration:.3f}", "-filter_complex", filt, "-map", "[v]"]
    if has_audio:
        args += ["-map", "0:a:0"]
        if audio_filters:
            args += ["-af", ",".join(audio_filters)]
        args += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
    software = ["-c:v", "libx264", "-preset", spec.preset, "-crf", str(spec.crf),
                "-pix_fmt", "yuv420p", "-threads", str(spec.threads)]
    hardware = {
        "h264_qsv": ["-c:v", "h264_qsv", "-global_quality", str(spec.crf), "-pix_fmt", "nv12"],
        "h264_nvenc": ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", str(spec.crf),
                       "-b:v", "0", "-pix_fmt", "nv12"],
    }
    output = ["-profile:v", "high", "-movflags", "+faststart", out.name]
    log.info("rendering clip", extra={"layout": layout, "duration": round(duration, 2), "applied": applied})
    timeout = max(600, duration * 40)
    encoder = spec.encoder
    if encoder in hardware:
        # A stuck hardware encoder is detected within a short budget instead of the full software timeout
        hw_timeout = min(timeout, max(spec.hw_timeout, duration * 3))
        try:
            await media.run(args + hardware[encoder] + output, cwd=workdir, timeout=hw_timeout)
            _check_output(workdir / out.name, duration)
        except media.MediaError as exc:
            log.warning("hardware encoder failed, retrying with libx264",
                        extra={"encoder": encoder, "err": str(exc)[:250]})
            applied.append(f"encoder_fallback:{encoder}")
            encoder = "libx264"
    else:
        encoder = "libx264"
    if encoder == "libx264":
        await media.run(args + software + output, cwd=workdir, timeout=timeout)
    applied.append(f"encoder:{encoder}")

    thumb = workdir / "thumb.jpg"
    t_thumb = min(max(0.0, spec.peak - spec.start), max(0.0, duration - 0.5))
    await media.run([media.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t_thumb:.2f}", "-i",
                     str(out.name), "-frames:v", "1", "-vf", "scale=540:-2", "-q:v", "3", "thumb.jpg"],
                    cwd=workdir, timeout=120)
    if segs:
        applied.append(f"structure:{plan.get('structure', '?')}:{len(segs)}seg")
    return RenderResult(out, thumb, duration, layout, time.monotonic() - t0, audio_info, applied)
