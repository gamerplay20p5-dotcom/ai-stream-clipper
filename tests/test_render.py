import asyncio
import json

import pytest

from clipper import media
from clipper.render.renderer import RenderSpec, render
from clipper.render.subtitles import SubWord
from conftest import FIXTURES

TL = json.loads((FIXTURES / "timeline_event.json").read_text())


@pytest.mark.parametrize("layout,webcam", [
    ("gameplay", None),
    ("zoom", None),
    ("split", {"x": 0.7, "y": 0.05, "w": 0.25, "h": 0.3}),
])
def test_render_vertical_h264_aac(tmp_path, layout, webcam):
    words = [SubWord(w[0], w[1], w[2]) for w in TL["words"] if 18 <= w[0] < 30]
    spec = RenderSpec(source=FIXTURES / "gameplay_event.mp4", source_start=0.0, start=18.0, end=30.0, peak=24.0,
                      words=words, title="Teste de corte", layout=layout, webcam_region=webcam,
                      preset="ultrafast", crf=28, threads=4)
    res = asyncio.run(render(spec, tmp_path / layout))
    info = media.probe_sync(res.video)
    v, a = media.video_stream(info), media.audio_stream(info)
    assert (v["width"], v["height"]) == (1080, 1920)
    assert v["codec_name"] == "h264" and v["pix_fmt"] == "yuv420p"
    assert a is not None and a["codec_name"] == "aac"
    assert abs(media.duration_of(info) - 12.0) < 0.5
    assert res.thumbnail.stat().st_size > 1000
    assert res.layout == layout
    ass = (tmp_path / layout / "subs.ass").read_text(encoding="utf-8")
    assert "OH" in ass and "WAY" in ass
    # title overlay only where there is free space (split layout has the webcam on top)
    assert ("Dialogue: 1," in ass) == (layout != "split")


def test_structural_render_concatenates_segments_and_remaps_captions(tmp_path):
    """Phase 6B: segments (with a labelled teaser) -> one continuous vertical video whose captions
    follow the edited timeline; removed words disappear, nothing is reordered except the teaser."""
    words = [SubWord(w[0], w[1], w[2]) for w in TL["words"] if 10 <= w[0] < 32]
    segs = [{"a": 23.5, "b": 25.5, "role": "teaser"}, {"a": 12.0, "b": 15.0, "role": "intro"},
            {"a": 20.0, "b": 30.0, "role": "main"}]
    plan = {"segments": segs, "structure": "B", "zooms": [{"t": 12.0, "duration": 1.0, "scale": 1.1}],
            "caption_style": "bold"}
    spec = RenderSpec(source=FIXTURES / "gameplay_event.mp4", source_start=0.0, start=12.0, end=30.0, peak=24.0,
                      words=words, title="", layout="gameplay", preset="ultrafast", crf=28, threads=4, plan=plan)
    res = asyncio.run(render(spec, tmp_path / "s"))
    info = media.probe_sync(res.video)
    assert abs(media.duration_of(info) - 15.0) < 0.6  # 2 + 3 + 10 s
    assert media.audio_stream(info) is not None
    ass = (tmp_path / "s" / "subs.ass").read_text(encoding="utf-8")
    assert "Daqui a pouco" in ass
    assert any(x.startswith("structure:B") for x in res.applied)
    # a word spoken at 22-24 s (main part) appears at 5 + (t - 20) s in the output, never in the cut 15-20 s gap
    import re

    starts = [float(m.group(1)) * 60 + float(m.group(2)) for m in
              re.finditer(r"Dialogue: 0,0:(\d\d):(\d\d\.\d\d),", ass)]
    assert starts and max(starts) <= 15.0


def test_hardware_failure_retries_in_software(tmp_path, monkeypatch):
    original = media.run

    async def force_nvenc_failure(args, **kwargs):
        if "h264_nvenc" in args:
            raise media.MediaError("encoder unavailable")
        return await original(args, **kwargs)

    monkeypatch.setattr(media, "run", force_nvenc_failure)
    spec = RenderSpec(FIXTURES / "gameplay_event.mp4", 0, 21, 24, 22, [], caption_style="none",
                      preset="ultrafast", encoder="h264_nvenc")
    res = asyncio.run(render(spec, tmp_path))
    assert {"encoder_fallback:h264_nvenc", "encoder:libx264"} <= set(res.applied)
    video = media.video_stream(media.probe_sync(res.video))
    assert (video["codec_name"], video["pix_fmt"], video["width"], video["height"]) == ("h264", "yuv420p", 1080, 1920)
