"""End-to-end pipeline with real processes (API + worker), real ffmpeg and real
faster-whisper (tiny model):

  controlled test video -> monitor -> capture (segments) -> audio analysis + transcription
  -> scoring -> vision (OpenCV) -> render (1080x1920, captions) -> preview/download

Also restarts the worker and the API mid-way to verify jobs and state survive restarts.
Run with:  pytest -m e2e
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from clipper import media
from conftest import ADMIN, CSRF, FIXTURES, ROOT

pytestmark = pytest.mark.e2e
EVENT_T = json.loads((FIXTURES / "gameplay_event.json").read_text())["event_t"]


def _port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Stack:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.api_port = _port()
        self.seg_port = _port()
        self.api = f"http://127.0.0.1:{self.api_port}"
        self.env = {
            **os.environ,
            "PYTHONPATH": str(ROOT / "backend"),
            "ASC_ENV": "test",
            "ASC_LOG_JSON": "true",
            "ASC_LOG_LEVEL": "INFO",
            "ASC_DATABASE_URL": f"sqlite:///{(tmp / 'e2e.db').as_posix()}",
            "ASC_STORAGE_DIR": str(tmp / "storage"),
            "ASC_CAPTURE_DIR": str(tmp / "capture"),
            "ASC_WORK_DIR": str(tmp / "work"),
            "ASC_API_URL": self.api,
            "ASC_SEGMENT_SERVER_PORT": str(self.seg_port),
            "ASC_SEGMENT_SERVER_BIND": "127.0.0.1",
            "ASC_SEGMENT_SERVER_PUBLIC_URL": f"http://127.0.0.1:{self.seg_port}",
            "ASC_FILE_SOURCE_REALTIME": "false",
            "ASC_MAINTENANCE_INTERVAL_S": "1",
            "ASC_POLL_INTERVAL_S": "0.5",
            "ASC_HEARTBEAT_INTERVAL_S": "2",
            "ASC_WHISPER_MODEL": "tiny",
            "ASC_WHISPER_CPU_THREADS": "4",
            "ASC_WORKER_ID": "e2e-worker",
            "ASC_WORKER_HOST_LABEL": "206",
        }
        self.procs: dict[str, subprocess.Popen] = {}

    def start(self, name: str, *args: str) -> None:
        log = open(self.tmp / f"{name}.log", "ab")
        self.procs[name] = subprocess.Popen([sys.executable, "-m", "clipper", *args], env=self.env,
                                            stdout=log, stderr=subprocess.STDOUT, cwd=str(self.tmp))

    def stop(self, name: str) -> None:
        p = self.procs.pop(name, None)
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=20)
            except subprocess.TimeoutExpired:
                p.kill()

    def kill(self, name: str) -> None:
        p = self.procs.pop(name, None)
        if p:
            p.kill()
            p.wait()

    def wait_api(self, timeout: float = 60) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if httpx.get(self.api + "/readyz", timeout=2).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        raise AssertionError("API did not become ready:\n" + self.logs("api"))

    def logs(self, name: str, tail: int = 4000) -> str:
        f = self.tmp / f"{name}.log"
        return f.read_text(errors="replace")[-tail:] if f.exists() else ""


def _login(api: str) -> httpx.Client:
    c = httpx.Client(base_url=api, timeout=30, headers=CSRF)
    r = c.post("/api/auth/login", json={"email": ADMIN[0], "password": ADMIN[1]})
    assert r.status_code == 200, r.text
    return c


def _wait(fn, timeout: float, what: str, stack: Stack):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = fn()
        if last:
            return last
        time.sleep(1)
    raise AssertionError(f"timeout waiting for {what}; last={last}\n--- worker log ---\n{stack.logs('worker')}"
                         f"\n--- api log ---\n{stack.logs('api')}")


@pytest.fixture()
def stack(tmp_path):
    s = Stack(tmp_path)
    yield s
    for name in list(s.procs):
        s.stop(name)


def test_full_pipeline_and_restart_resilience(stack: Stack, tmp_path: Path):
    stack.start("api", "api", "--host", "127.0.0.1", "--port", str(stack.api_port))
    stack.wait_api()
    c = _login(stack.api)
    r = c.put("/api/settings", json={"language": "en", "whisper_model": "tiny", "vision_enabled": True,
                                     "vision_provider": "heuristic", "export_preset": "ultrafast"})
    assert r.status_code == 200, r.text

    stack.start("worker", "worker", "--roles", "monitor,capture,audio,scoring,vision,render")
    r = c.post("/api/streamers", json={"url": "file://gameplay_event.mp4", "display_name": "E2E Tester",
                                       "preferences": {"game": "GTA V"}})
    assert r.status_code == 201, r.text
    streamer_id = r.json()["id"]

    def ready_clip():
        items = c.get("/api/clips").json()["items"]
        failed = [x for x in items if x["status"] == "failed"]
        assert not failed, failed
        return next((x for x in items if x["status"] == "ready"), None)

    t0 = time.time()
    clip = _wait(ready_clip, 300, "a rendered clip", stack)
    pipeline_seconds = time.time() - t0

    # --- detection quality ---------------------------------------------------------
    assert EVENT_T - 1 <= clip["peak"] <= EVENT_T + 4, clip
    assert clip["start"] < EVENT_T - 5 < EVENT_T + 5 < clip["end"]
    assert clip["score"] >= 55 and clip["reasons"]
    assert "no way" in clip["transcript"].lower()
    assert clip["vision_state"] == "done" and clip["score_breakdown"]["visual"] is not None
    assert clip["title"] and clip["title_suggestions"]

    # --- session bookkeeping --------------------------------------------------------
    sessions = c.get("/api/sessions").json()
    sess = next(s for s in sessions if s["streamer_id"] == streamer_id)
    _wait(lambda: c.get(f"/api/sessions/{sess['id']}").json()["status"] == "ended", 60, "session end", stack)
    detail = c.get(f"/api/sessions/{sess['id']}").json()
    assert detail["segment_count"] >= 4
    assert abs(detail["covered_until"] - 40.9) < 2.5, detail  # timestamps add up to the source duration
    assert {"loudness_spike", "keyword"} <= {e["kind"] for e in detail["events"]}

    # --- preview + download ---------------------------------------------------------
    video = tmp_path / "dl.mp4"
    r = c.get(f"/api/clips/{clip['id']}/download")
    assert r.status_code == 200 and r.headers["content-type"] == "video/mp4"
    video.write_bytes(r.content)
    info = media.probe_sync(video)
    v, a = media.video_stream(info), media.audio_stream(info)
    assert (v["width"], v["height"]) == (1080, 1920) and v["codec_name"] == "h264"
    assert a is not None and a["codec_name"] == "aac"
    assert abs(media.duration_of(info) - clip["duration"]) < 1.0
    rng = c.get(f"/api/clips/{clip['id']}/video", headers={"Range": "bytes=0-1023"})
    assert rng.status_code == 206 and len(rng.content) == 1024  # seekable preview
    assert c.get(f"/api/clips/{clip['id']}/thumbnail").headers["content-type"] == "image/jpeg"

    # --- infrastructure view --------------------------------------------------------
    infra = c.get("/api/infrastructure").json()
    host206 = next(h for h in infra["hosts"] if h["label"] == "206")
    assert host206["workers"] and set(host206["workers"][0]["roles"]) >= {"capture", "audio", "render"}
    stats = c.get("/api/stats").json()
    assert stats["clips_total"] >= 1 and stats["hours_analyzed"] > 0

    # --- restart resilience: worker dies with a job queued, API restarts ----------------
    stack.kill("worker")
    r = c.patch(f"/api/clips/{clip['id']}", json={"title": "Edited title", "start": clip["start"] + 1,
                                                 "caption_style": "boxed"})
    assert r.status_code == 200, r.text
    r = c.post(f"/api/clips/{clip['id']}/rerender")
    assert r.status_code == 200 and r.json()["version"] == 2
    stack.stop("api")
    stack.start("api", "api", "--host", "127.0.0.1", "--port", str(stack.api_port))
    stack.wait_api()
    c = _login(stack.api)
    assert c.get(f"/api/clips/{clip['id']}").json()["status"] == "rendering"  # state persisted
    stack.start("worker", "worker", "--roles", "monitor,capture,audio,scoring,vision,render")

    def rerendered():
        x = c.get(f"/api/clips/{clip['id']}").json()
        return x if x["status"] == "ready" and x["version"] == 2 else None

    clip2 = _wait(rerendered, 180, "re-render after restarts", stack)
    assert clip2["title"] == "Edited title" and clip2["caption_style"] == "boxed"
    assert abs(clip2["duration"] - (clip["duration"] - 1)) < 0.05
    assert c.post(f"/api/clips/{clip['id']}/approve").json()["status"] == "approved"
    jobs = c.get("/api/jobs").json()
    assert not [j for j in jobs if j["status"] == "dead"], [j for j in jobs if j["status"] == "dead"]
    print(f"\nE2E pipeline: first clip ready after {pipeline_seconds:.1f}s for a 40.9s source")


def test_silent_visual_discovery_shadow_and_render(stack: Stack, tmp_path: Path):
    from shutil import copyfile

    from fixtures.make_visual_fixtures import write_scene

    media_dir = tmp_path / "fixtures"
    media_dir.mkdir()
    original = media_dir / "silent-off.mp4"
    write_scene(original, "rollover")
    copyfile(original, media_dir / "silent-shadow.mp4")
    copyfile(original, media_dir / "silent-on.mp4")
    stack.env["ASC_TEST_MEDIA_DIR"] = str(media_dir)
    stack.start("api", "api", "--host", "127.0.0.1", "--port", str(stack.api_port))
    stack.wait_api()
    c = _login(stack.api)
    stack.start("worker", "worker", "--roles", "monitor,capture,audio,scoring,vision,render")

    def complete_session(streamer_id):
        sessions = c.get("/api/sessions").json()
        session = next((s for s in sessions if s["streamer_id"] == streamer_id), None)
        if session is None or session["status"] != "ended":
            return None
        detail = c.get(f"/api/sessions/{session['id']}").json()
        return detail if detail["scored_until"] >= detail["covered_until"] - 0.1 else None

    for mode in ("off", "shadow", "on"):
        settings = c.put("/api/settings", json={"visual_discovery": mode, "multimodal_scoring": True,
                                                "grounded_metadata": True, "vision_provider": "heuristic",
                                                "gate_enabled": False, "editor_mode": "off",
                                                "dataset_enabled": False, "export_preset": "ultrafast"})
        assert settings.status_code == 200, settings.text
        r = c.post("/api/streamers", json={"url": f"file://silent-{mode}.mp4", "display_name": f"Criador {mode}",
                                         "preferences": {"game": "GTA V", "language": "pt"}})
        assert r.status_code == 201, r.text
        sid = r.json()["id"]
        started = time.monotonic()

        session = _wait(lambda: complete_session(sid), 75, f"silent source scored in {mode}", stack)
        items = c.get("/api/clips").json()["items"]
        if mode != "on":
            assert not items
            assert not [e for e in session["events"] if e["kind"] == "visual_motion"]
            if mode == "shadow":
                reports = c.get("/api/diagnostics").json()["streamers"]
                shadow = next(report["scoring"]["visual_shadow"] for report in reports
                              if (report.get("scoring") or {}).get("visual_shadow"))
                assert shadow["proposals"] == shadow["multimodal_candidates"] == 1
                assert shadow["baseline_candidates"] == 0 and shadow["recent"][0]["evidence"]
            continue
        events = [e for e in session["events"] if e["kind"] == "visual_motion"]
        assert len(events) == 1 and abs(events[0]["t"] - 13.25) <= 1

        def ready():
            clips = c.get("/api/clips").json()["items"]
            assert not [item for item in clips if item["status"] in ("failed", "rejected")], clips
            return next((item for item in clips if item["status"] == "ready"), None)

        clip = _wait(ready, 90, "a rendered silent candidate", stack)
        assert clip["start"] <= 10 < 16 <= clip["end"]
        assert clip["title"] == "Trecho da live em GTA V | Criador on"
        assert clip["transcript"] == ""
        video = c.get(f"/api/clips/{clip['id']}/download")
        assert video.status_code == 200
        result = tmp_path / "silent-short.mp4"
        result.write_bytes(video.content)
        info = media.probe_sync(result)
        v = media.video_stream(info)
        assert (v["width"], v["height"]) == (1080, 1920)
        print(f"\nSilent E2E pipeline: first clip ready after {time.monotonic() - started:.1f}s for 24s of video")

    write_scene(media_dir / "silent-distractors.mp4", "distractors")
    r = c.post("/api/streamers", json={"url": "file://silent-distractors.mp4", "display_name": "Controle negativo"})
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    session = _wait(lambda: complete_session(sid), 75, "distractor source scored", stack)
    assert not [e for e in session["events"] if e["kind"] == "visual_motion"]
    assert not [item for item in c.get("/api/clips").json()["items"] if item["streamer_id"] == sid]
