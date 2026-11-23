from __future__ import annotations

import hashlib
import json
import os
import signal
import sys
import time
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_asc_ctl_policy import CONF, ROOT, asc_ctl as ctl


CONF_201 = {**CONF, "SERVER": "201", "LAN_IP": "192.168.1.201"}
CID = "a" * 64
PEER = "b" * 64
IMAGE = "sha256:" + "c" * 64
LINUX = pytest.mark.skipif(sys.platform != "linux", reason="Linux dirfd, /proc and O_PATH")


def test_201_keeps_the_commands_from_installed_asc_ctl_120():
    installed = """backup-db build compose db-restore env-install env-show exec gpu-validate image-load
        image-rm image-save images init models set-tag status test-media-add tls-init tls-install-ca
        tunnel-token-install tunnel-token-remove""".split()
    assert ctl.VERSION == "1.4.0"
    assert set(installed) <= ctl.COMMANDS.keys()
    for kind, suffix in (("app", ""), ("vision", "-vision")):
        assert ctl.image_ref(kind, "0.8.0") == f"ai-stream-clipper{suffix}:0.8.0"


def test_wheel_lock_matches_runtime_versions():
    lock = ROOT / "infrastructure/admin/bench-pr31-audio.lock"
    pins = {}
    for line in lock.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        pin, digest = line.split(" --hash=sha256:")
        assert len(bytes.fromhex(digest)) == 32
        name, version = pin.split("==")
        pins[name] = version
    assert pins == ctl.PR31_AUDIO_VERSIONS


def test_command_rejects_other_hosts_and_arguments():
    with pytest.raises(ctl.Denied, match="restricted"):
        ctl.COMMANDS["bench-pr31"](CONF, [])
    with pytest.raises(ctl.Denied, match="restricted"):
        ctl.cmd_bench_pr31({**CONF_201, "LAN_IP": "127.0.0.1"}, [])
    for args in (["/tmp/settings.json"], ["--mount", "/:/host"], ["--check", "--check"]):
        with pytest.raises(ctl.Denied, match="no path arguments"):
            ctl.cmd_bench_pr31(CONF_201, args)


@pytest.mark.parametrize("value", [None, "75", True, 70, 74.99, 75.01])
def test_requires_explicit_numeric_score_75(value):
    with pytest.raises(ctl.Denied, match="min_score=75"):
        ctl._bench_score({"min_score": value, "whisper_model": "small"})


@pytest.mark.parametrize("value", [None, "tiny", "off", "Small", "/models/whisper"])
def test_requires_whisper_small(value):
    with pytest.raises(ctl.Denied, match="whisper_model=small"):
        ctl._bench_score({"min_score": 75, "whisper_model": value})


def test_manifest_normalizes_only_prepared_media():
    ctl._bench_score({"min_score": 75.0, "whisper_model": "small", "span_s": 40})
    manifest = {"videos": [{"video": "/home/asc/bench/pr31/media/positive.mp4"},
                           {"segments": [{"file": "media/negative.ts"}]}]}
    assert [p.as_posix() for p in ctl._bench_manifest(manifest)] == ["media/negative.ts", "media/positive.mp4"]
    assert manifest["videos"][0]["video"] == "media/positive.mp4"


@pytest.mark.parametrize("value", ["../clip.mp4", "/opt/asc/other.ts", "/home/asc/bench/pr310/clip.mp4",
                                   "media/../../clip.mp4", "out/clip.mp4", "models/clip.mp4", "media/a,ro.mp4",
                                   "media/a\x00.mp4", "media\\a.mp4", "https://example.test/a.mp4"])
def test_refuses_media_outside_the_input_tree(value):
    with pytest.raises(ctl.Denied):
        ctl._bench_manifest({"videos": [{"video": value}]})


def test_docker_create_is_offline_bounded_and_has_only_fixed_mounts():
    stage = Path("/var/lib/asc-ctl/pr31/run-staged")
    argv = ctl._bench_create(stage, IMAGE, "993", "asc-bench-pr31-test", 3500)
    for flag, value in {"--cpus": "4", "--memory": "12g", "--memory-swap": "12g", "--pids-limit": "256",
                        "--network": "none", "--restart": "no", "--cap-drop": "ALL", "--pull": "never",
                        "--user": "10001:10001", "--group-add": "993", "--entrypoint": "/usr/bin/timeout"}.items():
        assert argv[argv.index(flag) + 1] == value
    assert argv[0] == "create"
    assert "--read-only" in argv and "--privileged" not in argv and "--security-opt" in argv
    assert "PYTHONPATH=/pr31/backend" in argv and "HF_HUB_OFFLINE=1" in argv
    assert "HF_HUB_CACHE=/pr31/audio/hub" in argv
    mounts = [dict(field.split("=", 1) if "=" in field else (field, True) for field in argv[i + 1].split(","))
              for i, arg in enumerate(argv) if arg == "--mount"]
    assert {m["target"]: m["source"] for m in mounts} == {
        "/pr31/backend": str(stage / "backend"), "/models": str(ctl.PR31_MODELS),
        "/inputs": str(stage / "inputs"), "/pr31/audio": str(ctl.PR31_AUDIO), "/result": str(stage / "result")}
    assert all(m["type"] == "bind" and m["bind-recursive"] == "disabled" for m in mounts)
    assert {m["target"] for m in mounts if m.get("readonly")} == {"/pr31/backend", "/models", "/inputs", "/pr31/audio"}
    assert argv[argv.index(IMAGE) + 1:argv.index(IMAGE) + 4] == ["--kill-after=5s", "3500s", "/opt/asc/bin/python3"]
    compile(ctl.PR31_ENTRY, "<container-entrypoint>", "exec")
    with pytest.raises(ctl.Denied, match="deadline"):
        ctl._bench_create(stage, IMAGE, "993", "test", 3600)


def test_check_has_no_gpu_device_or_writable_mount():
    argv = ctl._bench_create(Path("/safe/staging"), IMAGE, "993", "test", 280, check=True)
    assert "--device" not in argv and "--group-add" not in argv
    assert not any("target=/result" in arg for arg in argv)
    assert all(argv[i + 1].endswith(",readonly") for i, arg in enumerate(argv) if arg == "--mount")
    assert argv[-1] == "--check" and "none" == argv[argv.index("--network") + 1]


def test_docker_is_pinned_to_the_local_socket_with_a_clean_environment(monkeypatch):
    monkeypatch.setenv("DOCKER_HOST", "tcp://example.test:2375")
    monkeypatch.setenv("ASC_INTERNAL_SECRET", "test-only")

    def run(argv, **kwargs):
        assert argv == [ctl.DOCKER, "--config", str(ctl.PR31_DOCKER_CONFIG),
                        "--host", "unix:///var/run/docker.sock", "inspect", PEER]
        assert kwargs["env"] == {"PATH": ctl.SAFE_PATH, "HOME": "/root", "LC_ALL": "C"}
        assert kwargs["timeout"] == 8 and kwargs["check"]
        return SimpleNamespace(stdout="ok\n")

    monkeypatch.setattr(ctl.subprocess, "run", run)
    assert ctl._bench_docker("inspect", PEER) == "ok"


@pytest.mark.parametrize("volumes", [{}, {"/extra": {}}])
def test_only_the_running_asc_worker_image_is_accepted(monkeypatch, volumes):
    info = {k: True for k in ("MemoryLimit", "SwapLimit", "CpuCfsQuota", "PidsLimit")}
    info.update(OSType="linux", CgroupDriver="systemd", SecurityOptions=["name=seccomp,profile=builtin"])
    replies = iter([json.dumps(info), "", f"{PEER} {IMAGE} ai-stream-clipper-vision:0.8.0 running asc worker-vision",
                    json.dumps(volumes)])
    monkeypatch.setattr(ctl, "_bench_docker", lambda *args: next(replies))
    if volumes:
        with pytest.raises(ctl.Denied, match="implicit volumes"):
            ctl._bench_image()
    else:
        assert ctl._bench_image() == (IMAGE, PEER)


def test_gpu_gate_refuses_foreign_clients_before_and_during_replay(monkeypatch):
    monkeypatch.setattr(ctl, "_bench_gpu_clients", lambda nodes: {101: "pid:[34]"})
    assert ctl._bench_no_contention({128}, "pid:[34]") == 1
    for namespace in (None, "pid:[35]"):
        with pytest.raises(ctl.Denied, match="external client"):
            ctl._bench_no_contention({128}, namespace)


@pytest.mark.parametrize("nodes", [None, {128}], ids=["cpu-check", "gpu-replay"])
def test_watch_aborts_on_peer_degradation(monkeypatch, nodes):
    monkeypatch.setattr(ctl, "_bench_docker", lambda *args: "running 101 0")
    monkeypatch.setattr(ctl, "_bench_peers", lambda ids: {PEER: "running unhealthy 0"})
    with pytest.raises(ctl.Denied, match="peer container"):
        ctl._bench_watch(CID, {PEER: "running healthy 0"}, nodes, time.monotonic() + 30, {"samples": 0})


def test_watch_records_gpu_samples_and_exit_code(monkeypatch):
    monkeypatch.setattr(ctl, "_bench_docker", lambda *args: "exited 0 0")
    monkeypatch.setattr(ctl, "_bench_peers", lambda ids: {PEER: "running healthy 0"})
    monkeypatch.setattr(ctl, "_bench_capacity", lambda **kwargs: None)
    monkeypatch.setattr(ctl.shutil, "disk_usage", lambda path: SimpleNamespace(free=8 * ctl.GIB))
    monkeypatch.setattr(ctl, "_bench_no_contention", lambda nodes, namespace: 0)
    record = {"samples": 1, "peak_gpu_clients": 2}
    assert ctl._bench_watch(CID, {PEER: "running healthy 0"}, {128}, time.monotonic() + 30, record) == 0
    assert record == {"samples": 2, "peak_gpu_clients": 2}
    with pytest.raises(ctl.Denied, match="time limit"):
        ctl._bench_watch(CID, {}, {128}, time.monotonic() - 1, record)


@pytest.mark.parametrize(("check", "contention_at"), [(True, 1), (False, 1), (False, 2), (False, 3), (False, 4)],
                         ids=["cpu-check", "gpu-admission", "gpu-after-staging", "gpu-before-start", "gpu-runtime"])
def test_cpu_check_allows_busy_gpu_and_replay_refuses_it(monkeypatch, tmp_path, check, contention_at):
    calls, reports, gpu_samples = [], [], []

    def docker(*args, **kwargs):
        calls.append(args)
        return {"ps": PEER, "create": CID, "start": CID, "rm": CID,
                "inspect": "exited 0 0" if check else "running 101 0"}[args[0]]

    def gpu_clients(nodes):
        gpu_samples.append(nodes)
        return {102: "pid:[34]"} if len(gpu_samples) >= contention_at else {}

    monkeypatch.setattr(ctl, "PR31_STATE", tmp_path)
    monkeypatch.setattr(ctl, "_bench_host_address", lambda: None)
    monkeypatch.setattr(ctl, "_bench_audio", lambda deadline: None)
    monkeypatch.setattr(ctl, "_bench_capacity", lambda **kwargs: None)
    monkeypatch.setattr(ctl, "_bench_trusted_dir", lambda path: None)
    monkeypatch.setattr(ctl, "_bench_gpu", lambda conf: ("993", {128}))
    monkeypatch.setattr(ctl, "_bench_gpu_clients", gpu_clients)
    monkeypatch.setattr(ctl, "_bench_image", lambda: (IMAGE, PEER))
    monkeypatch.setattr(ctl, "_bench_docker", docker)
    monkeypatch.setattr(ctl, "_bench_open_dir", lambda *args, **kwargs: nullcontext(10))
    monkeypatch.setattr(ctl, "_bench_stage", lambda *args: None)
    monkeypatch.setattr(ctl, "_bench_peers", lambda ids: {PEER: "running healthy 0"})
    monkeypatch.setattr(ctl, "_bench_publish", lambda fd, name, data, uid, gid: reports.append((name, data)))
    monkeypatch.setattr(os, "chown", lambda *args: None, raising=False)
    monkeypatch.setattr(os, "fstatvfs", lambda fd: SimpleNamespace(f_bavail=ctl.GIB, f_frsize=8), raising=False)
    original_fstat = os.fstat
    monkeypatch.setattr(os, "fstat", lambda fd: SimpleNamespace(st_uid=1234, st_mode=0o700)
                        if fd == 10 else original_fstat(fd))
    original_readlink = os.readlink
    monkeypatch.setattr(os, "readlink", lambda path, *args, **kwargs: "pid:[35]" if path == "/proc/101/ns/pid"
                        else original_readlink(path, *args, **kwargs))
    monkeypatch.setattr(ctl.shutil, "disk_usage", lambda path: SimpleNamespace(free=8 * ctl.GIB))

    deadline = time.monotonic() + 30
    if check:
        assert ctl._bench_run(1234, 1234, CONF_201, True, deadline, signal) == 0
        assert [args[0] for args in calls] == ["ps", "create", "start", "inspect", "rm"]
        assert calls[1][-1] == "--check" and "--device" not in calls[1]
        assert calls[-1] == ("rm", "-f", "-v", CID)
        assert not reports and not gpu_samples
        return
    if contention_at <= 2:
        with pytest.raises(ctl.Denied, match="external client"):
            ctl._bench_run(1234, 1234, CONF_201, False, deadline, signal)
        assert calls == ([] if contention_at == 1 else [("ps", "--no-trunc", "-q")])
        assert not reports and len(gpu_samples) == contention_at
        return
    assert ctl._bench_run(1234, 1234, CONF_201, False, deadline, signal) == 1
    steps = ["ps", "create", "rm"] if contention_at == 3 else ["ps", "create", "start", "inspect", "rm"]
    assert [args[0] for args in calls] == steps and len(gpu_samples) == contention_at
    assert calls[-1] == ("rm", "-f", "-v", CID)
    [(name, status)] = reports
    assert name.endswith(".status.json")
    assert status["result"] == "aborted" and status["reason"] == "the shared GPU has an external client"


@LINUX
def test_privileged_input_rejects_symlinks_hardlinks_and_fifos(tmp_path):
    (tmp_path / "media.mp4").write_bytes(b"input")
    (tmp_path / "media-link.mp4").symlink_to(tmp_path / "media.mp4")
    os.link(tmp_path / "media.mp4", tmp_path / "media-hardlink.mp4")
    os.mkfifo(tmp_path / "media-fifo.mp4")
    (tmp_path / "directory").symlink_to(tmp_path, target_is_directory=True)
    with ctl._bench_open_dir(None, tmp_path.parts[1:]) as parent:
        with pytest.raises(OSError):
            with ctl._bench_open_dir(parent, ("directory",)):
                pytest.fail("followed a symlinked directory")
        for name in ("media-link.mp4", "media-hardlink.mp4", "media-fifo.mp4"):
            with pytest.raises(ctl.Denied, match="regular file"):
                with ctl._bench_regular(parent, name, os.getuid(), 100):
                    pytest.fail("opened an unapproved file")


@LINUX
def test_staging_pins_source_bytes_and_publishes_exclusively(monkeypatch, tmp_path):
    package = tmp_path / "src/backend/clipper"
    package.mkdir(parents=True)
    code = b"# exact reviewed bytes\n"
    (package / "__init__.py").write_bytes(code)
    digest = hashlib.sha256(b"__init__.py\x00" + hashlib.sha256(code).digest()).hexdigest()
    monkeypatch.setattr(ctl, "PR31_PACKAGE_SHA", digest)
    (tmp_path / "editorial.json").write_text('{"min_score": 75, "whisper_model": "small"}', encoding="utf-8")
    (tmp_path / "manifest.json").write_text('{"videos": [{"video": "media/case.mp4"}]}', encoding="utf-8")
    (tmp_path / "bench/media").mkdir(parents=True)
    media = tmp_path / "bench/media/case.mp4"
    media.write_bytes(b"local clip")
    stage = tmp_path / "stage"
    stage.mkdir()
    (tmp_path / "out").mkdir()
    (tmp_path / "out/result.json").symlink_to(media)
    with ctl._bench_open_dir(None, tmp_path.parts[1:]) as parent:
        ctl._bench_stage(parent, stage, os.getuid(), time.monotonic() + 5)
        assert (stage / "backend/clipper/__init__.py").read_bytes() == code
        assert (stage / "inputs/media/case.mp4").read_bytes() == b"local clip"
        (package / "__init__.py").write_bytes(b"changed")
        with pytest.raises(ctl.Denied, match="reviewed PR"):
            ctl._bench_package(parent, stage / "modified", os.getuid(), time.monotonic() + 5)
        with ctl._bench_open_dir(parent, ("out",)) as out_fd:
            with pytest.raises(FileExistsError):
                ctl._bench_publish(out_fd, "result.json", {"unsafe": True}, os.getuid(), os.getgid())
            ctl._bench_publish(out_fd, "new.json", {"score": 75}, os.getuid(), os.getgid())
    assert media.read_bytes() == b"local clip"
    assert json.loads((tmp_path / "out/new.json").read_text()) == {"score": 75}


@LINUX
def test_audio_bundle_enforces_whisper_digest_and_permissions(monkeypatch, tmp_path):
    bundle = tmp_path / "audio"
    (bundle / "site").mkdir(parents=True)
    repo = bundle / "hub/models--Systran--faster-whisper-small"
    (repo / "refs").mkdir(parents=True)
    (repo / "refs/main").write_text(ctl.PR31_AUDIO_REVISION, encoding="ascii")
    snapshot = repo / "snapshots" / ctl.PR31_AUDIO_REVISION
    snapshot.mkdir(parents=True)
    payload = b"small model bytes"
    model = snapshot / "model.bin"
    model.write_bytes(payload)
    bundle.chmod(0o755)
    for path in bundle.rglob("*"):
        path.chmod(0o755 if path.is_dir() else 0o644)
    monkeypatch.setattr(ctl, "PR31_AUDIO", bundle)
    monkeypatch.setattr(ctl, "PR31_AUDIO_FILES", {"model.bin": hashlib.sha256(payload).hexdigest()})
    monkeypatch.setattr(ctl, "_bench_trusted_dir", lambda path: None)

    real_stat = Path.lstat

    def simulated_owner(path):
        st = real_stat(path)
        if path.is_relative_to(bundle):
            return SimpleNamespace(st_uid=0, st_mode=st.st_mode)
        return st

    monkeypatch.setattr(Path, "lstat", simulated_owner)
    deadline = time.monotonic() + 10
    ctl._bench_audio(deadline)
    model.write_bytes(b"different bytes")
    with pytest.raises(ctl.Denied, match="unexpected offline Whisper small file"):
        ctl._bench_audio(deadline)
    model.chmod(0o666)
    with pytest.raises(ctl.Denied, match="without group or other write access"):
        ctl._bench_audio(deadline)
    model.unlink()
    model.symlink_to(tmp_path / "another-model.bin")
    with pytest.raises(ctl.Denied, match="only real directories and regular files"):
        ctl._bench_audio(deadline)
