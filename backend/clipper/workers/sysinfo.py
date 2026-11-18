"""Host metrics and capability probing for worker heartbeats."""

from __future__ import annotations

import os
import platform
import socket
from pathlib import Path

import psutil


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def gpu_info(device: str) -> dict:
    """Intel GPU (xe/i915) probing. ``gpu`` is true only when the render node is present
    AND OpenVINO reports a GPU device (if OpenVINO is installed in this image)."""
    info: dict = {"gpu": False}
    if not device or not os.path.exists(device):
        return info
    info["gpu_device"] = device
    try:
        import openvino as ov

        core = ov.Core()
        devices = list(core.available_devices)
        info["openvino_devices"] = devices
        if "GPU" in devices:
            info["gpu"] = True
            try:
                info["gpu_name"] = core.get_property("GPU", "FULL_DEVICE_NAME")
            except Exception:
                pass
    except ImportError:
        info["openvino_devices"] = []
        info["gpu"] = False
        info["note"] = "render node present but OpenVINO not installed in this image"
    return info


def gpu_metrics() -> dict:
    """Best-effort Intel GPU frequency from sysfs (xe and i915 layouts)."""
    out: dict = {}
    for card in sorted(Path("/sys/class/drm").glob("card[0-9]")):
        for cur, mx in (("device/tile0/gt0/freq0/act_freq", "device/tile0/gt0/freq0/max_freq"),
                        ("gt_act_freq_mhz", "gt_max_freq_mhz")):
            a = _read(str(card / cur))
            m = _read(str(card / mx))
            if a and m:
                out = {"gpu_freq_mhz": int(a), "gpu_max_freq_mhz": int(m),
                       "gpu_busy_pct": round(100.0 * int(a) / max(1, int(m)), 1)}
                return out
    return out


def capabilities(gpu_device: str, extra: dict | None = None) -> dict:
    vm = psutil.virtual_memory()
    caps = {
        "cpu_count": psutil.cpu_count(logical=True),
        "mem_total": vm.total,
        "platform": platform.platform(),
        "hostname": socket.gethostname(),
    }
    caps.update(gpu_info(gpu_device))
    caps.update(extra or {})
    return caps


def metrics(work_dir: Path, running_jobs: int) -> dict:
    vm = psutil.virtual_memory()
    try:
        du = psutil.disk_usage(str(work_dir))
        disk = {"disk_used": du.used, "disk_total": du.total, "disk_pct": du.percent}
    except OSError:
        disk = {}
    try:
        load = os.getloadavg()
    except (AttributeError, OSError):
        load = (0.0, 0.0, 0.0)
    return {
        "cpu_pct": psutil.cpu_percent(interval=None),
        "load1": round(load[0], 2),
        "mem_used": vm.total - vm.available,
        "mem_total": vm.total,
        "mem_pct": vm.percent,
        "proc_rss": psutil.Process().memory_info().rss,
        "running_jobs": running_jobs,
        **disk,
        **gpu_metrics(),
    }
