from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys

import pytest

from clipper import media
from clipper.bench.replay import Annotation, Case, _compare, _editorial_settings
from clipper.settings_store import AISettings
from clipper.workers.capture import _read_csv
from conftest import ROOT
from fixtures.make_visual_fixtures import write_scene


def test_comparison_matches_two_close_events_once_each():
    events = [Annotation(peak=10), Annotation(peak=13)]
    assert _compare(events, [12, 15, 18], 2) == {"tp": 2, "fp": 1, "fn": 0}
    assert _compare([], [12], 2) == {"tp": 0, "fp": 1, "fn": 0}


def test_manifest_requires_labels_and_ordered_contiguous_original_segments():
    with pytest.raises(ValueError, match="events"):
        Case(video="input.mp4")
    with pytest.raises(ValueError, match="contiguous"):
        Case(events=[], segments=[{"file": "a.ts", "start": 10, "duration": 10},
                                  {"file": "b.ts", "start": 21, "duration": 10}])


def test_settings_export_rejects_unknown_keys_and_missing_min_score(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"min_score": 70, "internal_secret": "private-value"}', encoding="utf-8")
    with pytest.raises(ValueError, match="editorial JSON") as err:
        _editorial_settings(path, None, None)
    assert "private-value" not in str(err.value)
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="must contain min_score"):
        _editorial_settings(path, None, None)


@pytest.mark.parametrize("unavailable", [False, True], ids=["gate", "fallback"])
def test_replay_uses_original_segments_and_the_production_gate(tmp_path, unavailable):
    pytest.importorskip("cv2")
    positive = tmp_path / "rollover.mp4"
    control = tmp_path / "control.mp4"
    write_scene(positive, "rollover")
    write_scene(control, "distractors")
    media.run_sync([media.FFMPEG, "-v", "error", "-i", positive.name, "-c", "copy", "-f", "segment",
                    "-segment_time", "10", "-segment_list", "segments.csv", "part_%03d.ts"], cwd=tmp_path)
    segments = [{"file": file, "start": 120 + start, "duration": end - start}
                for file, start, end in _read_csv(tmp_path / "segments.csv")]
    hashes = [hashlib.sha256((tmp_path / s["file"]).read_bytes()).hexdigest() for s in segments]
    manifest = tmp_path / "annotations.json"
    manifest.write_text(json.dumps({"videos": [
        {"segments": segments, "events": [{"t": 133.25, "must_include": [130, 136]}]},
        {"video": control.name, "language": "pt", "events": []},
    ]}), encoding="utf-8")
    exported = AISettings(min_score=70, gate_event_min=5.8, gate_narrative_min=5.3, visual_investigate_min=61,
                          language="en", vision_model="qwen3-vl-4b-int4", weights={"visual": 1.4})
    export_path = tmp_path / "editorial.json"
    export_path.write_text(exported.model_dump_json(), encoding="utf-8")

    # The CLI is a separate process because it deliberately replaces the application settings.
    # Substitute only inference. Workers, SQLite, source handling, scoring and Quality Gate are real.
    setup = """
import os
from clipper import pipeline
from clipper.ai import quality_gate
from clipper.ai.manager import ModelManager, ModelUnavailable
from clipper.ai.schemas import VisualAnalysis
from clipper.ai.vlm import VLMAnalyzer
from clipper.workers import handlers
from clipper.bench.replay import main

if os.environ.get("TEST_REPLAY_FALLBACK") == "no":
    scored_modes = set()
    timeline = pipeline.timeline
    evaluate = quality_gate.evaluate
    def check_timeline(*args, **kwargs):
        result = timeline(*args, **kwargs)
        cfg = result["settings"]
        assert cfg["min_score"] == 70 and cfg["visual_investigate_min"] == 61
        assert cfg["weights"]["visual"] == 1.4
        scored_modes.add(cfg["visual_discovery"])
        return result
    def check_gate(**kwargs):
        assert kwargs["settings"].event_min == 5.8 and kwargs["settings"].narrative_min == 5.3
        return evaluate(**kwargs)
    pipeline.timeline = check_timeline
    quality_gate.evaluate = check_gate

ModelManager.acquire = lambda *args, **kwargs: (None, "CPU")
def analyze(self, frames, peak_t, span, window_abs, *args, **kwargs):
    if os.environ.get("TEST_REPLAY_FALLBACK") == "yes":
        raise ModelUnavailable("inference unavailable in regression test")
    return VisualAnalysis(provider="test", device="CPU", model=self.key, window_abs=window_abs,
        frame_times=[f.t for f in frames], important=9, unexpected=9, action=9, confidence=0.9,
        arc={"setup_start": peak_t - 3, "climax": peak_t, "resolution_end": peak_t + 3, "complete": True})

VLMAnalyzer.analyze = analyze
handlers._confirm_hypothesis = lambda manager, model, src, src_start, src_dur, hypothesis, cpu_only: {
    "occurred": True, "kind": "rollover", "reason": "inference double", "hypothesis": hypothesis}
code = main()
if os.environ.get("TEST_REPLAY_FALLBACK") == "no":
    assert scored_modes == {"off", "on"}
raise SystemExit(code)
"""
    decoy_db = tmp_path / "other.db"
    decoy_storage = tmp_path / "other-storage"
    env = {**os.environ, "PYTHONPATH": str(ROOT / "backend"), "ASC_ENV": "production",
           "ASC_DATABASE_URL": f"sqlite:///{decoy_db.as_posix()}", "ASC_STORAGE_DIR": str(decoy_storage),
           "TEST_REPLAY_FALLBACK": "yes" if unavailable else "no"}
    output = tmp_path / "comparison.json"
    config = [] if unavailable else ["--settings", str(export_path)]
    run = subprocess.run([sys.executable, "-c", setup, str(manifest), "--models-dir", str(tmp_path),
                          "--device", "CPU", "--out", str(output), *config], env=env, capture_output=True, timeout=90)
    assert run.returncode == (2 if unavailable else 0), run.stderr.decode(errors="replace")
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["settings"]["gate_enabled"] and report["gate_version"]
    assert not decoy_db.exists() and not decoy_storage.exists()
    source, negative = report["videos"]
    assert [s["sha256"] for s in source["segments"]] == hashes
    assert [hashlib.sha256((tmp_path / s["file"]).read_bytes()).hexdigest() for s in segments] == hashes
    assert report["summary"]["off"]["candidates"] == {"tp": 0, "fp": 0, "fn": 1}
    assert report["summary"]["on"]["discovery"]["tp"] == 1
    assert negative["negative_control"] and negative["modes"]["on"]["metrics"]["gate_ready"]["fp"] == 0
    clip = source["modes"]["on"]["clips"][0]
    assert 130 < clip["peak"] < 136 and clip["covers_required_interval"]
    assert clip["gate"]["outcome"]
    if unavailable:
        assert report["settings_source"] == "defaults"
        assert clip["vlm_fallback"] and source["modes"]["on"]["errors"]
    else:
        assert report["settings_source"] == "export"
        changes = report["settings_differences"]
        assert {k: v["to"] for k, v in changes.items()} == {
            "editor_mode": "off", "narrative_model": "template", "dataset_enabled": False,
            "visual_discovery": ["off", "on"],
        }
        assert report["settings"] == {
            k: changes[k]["to"] if k in changes else value
            for k, value in exported.model_dump(exclude={"visual_discovery"}).items()
        }
        assert source["settings_differences"] == {}
        assert negative["settings_differences"]["language"]["to"] == "pt"
        assert clip["vlm_model"] == report["model"]
        assert clip["targeted_confirmed_in_clip"]
        assert report["summary"]["on"]["gate_ready"]["tp"] == 1
