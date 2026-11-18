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
