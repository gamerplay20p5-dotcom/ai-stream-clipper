"""Model manager: one heavy generative model resident at a time, device selection with
GPU -> CPU fallback, availability tracking and per-model statistics.

Arc A750 boards have ~8 GB each and do not share memory, so a worker never keeps two
generative models loaded: requesting another model unloads the current one first.
"""

from __future__ import annotations

import gc
import logging
from pathlib import Path
import threading
import time
from dataclasses import asdict, dataclass, field

from .registry import MODELS, is_installed, model_path

log = logging.getLogger(__name__)


class ModelUnavailable(RuntimeError):
    pass


@dataclass
class ModelStats:
    key: str
    device: str = ""
    state: str = "not_loaded"  # not_loaded | loading | ready | failed | missing
    load_seconds: float = 0.0
    calls: int = 0
    failures: int = 0
    last_latency_s: float = 0.0
    avg_latency_s: float = 0.0
    last_error: str = ""
    fell_back_to_cpu: bool = False

    def record(self, latency: float, ok: bool) -> None:
        if ok:
            self.calls += 1
            self.last_latency_s = round(latency, 3)
            self.avg_latency_s = round(self.avg_latency_s + (latency - self.avg_latency_s) / self.calls, 3)
        else:
            self.failures += 1


@dataclass
class _Loaded:
    key: str
    device: str
    pipe: object
    kind: str
    cpu_only: bool = False


def _ov_devices() -> list[str]:
    try:
        import openvino as ov

        return list(ov.Core().available_devices)
    except Exception:
        return []


def gpu_memory() -> dict:
    """VRAM figures reported by the OpenVINO GPU plugin (best effort)."""
    try:
        import openvino as ov

        core = ov.Core()
        if "GPU" not in core.available_devices:
            return {}
        out = {"total_bytes": int(core.get_property("GPU", "GPU_DEVICE_TOTAL_MEM_SIZE"))}
        try:
            stats = core.get_property("GPU", "GPU_MEMORY_STATISTICS")
            out["used_bytes"] = int(sum(int(v) for v in dict(stats).values()))
        except Exception:
            pass
        out["name"] = str(core.get_property("GPU", "FULL_DEVICE_NAME"))
        return out
    except Exception:
        return {}


@dataclass
class ModelManager:
    preferred_device: str = "GPU"
    allow_cpu_fallback: bool = True
    stats: dict[str, ModelStats] = field(default_factory=dict)
    _loaded: _Loaded | None = None
    _lock: threading.RLock = field(default_factory=threading.RLock)

    def stat(self, key: str) -> ModelStats:
        if key not in self.stats:
            self.stats[key] = ModelStats(key)
        return self.stats[key]

    def snapshot(self) -> dict:
        return {
            "loaded": self._loaded.key if self._loaded else None,
            "device": self._loaded.device if self._loaded else None,
            "models": {k: asdict(v) for k, v in self.stats.items()},
        }

    def unload(self) -> None:
        with self._lock:
            if self._loaded is not None:
                log.info("unloading model", extra={"model": self._loaded.key})
                self.stat(self._loaded.key).state = "not_loaded"
                self._loaded = None
                gc.collect()

    def _devices_to_try(self, *, cpu_only: bool = False) -> list[str]:
        devices = _ov_devices()
        if cpu_only:
            return ["CPU"] if "CPU" in devices else []
        order = []
        if self.preferred_device in devices:
            order.append(self.preferred_device)
        if self.allow_cpu_fallback and "CPU" in devices and "CPU" not in order:
            order.append("CPU")
        return order

    def acquire(self, key: str, *, cpu_only: bool = False):
        """Return ``(pipeline, device)`` for ``key``, loading it (and unloading any other
        model) if needed. Raises :class:`ModelUnavailable` with a human-readable reason."""
        spec = MODELS.get(key)
        if spec is None:
            raise ModelUnavailable(f"unknown model {key}")
        st = self.stat(key)
        with self._lock:
            loaded = self._loaded
            if loaded is not None and loaded.key == key:
                if (cpu_only and loaded.device == "CPU") or (not cpu_only and not loaded.cpu_only):
                    return loaded.pipe, loaded.device
            if not is_installed(key):
                st.state = "missing"
                st.last_error = f"model files not found in {model_path(key)}"
                raise ModelUnavailable(st.last_error)
            self.unload()
            errors = []
            for device in self._devices_to_try(cpu_only=cpu_only):
                st.state = "loading"
                t0 = time.monotonic()
                try:
                    pipe = self._build(spec.kind, str(model_path(key)), device)
                except Exception as exc:  # compile failure, OOM, unsupported op...
                    errors.append(f"{device}: {type(exc).__name__}: {str(exc)[:300]}")
                    log.warning("model load failed", extra={"model": key, "device": device, "err": str(exc)[:300]})
                    gc.collect()
                    continue
                st.load_seconds = round(time.monotonic() - t0, 2)
                st.device = device
                st.state = "ready"
                st.fell_back_to_cpu = device == "CPU" and self.preferred_device != "CPU"
                self._loaded = _Loaded(key, device, pipe, spec.kind, cpu_only and self.preferred_device != "CPU")
                log.info("model loaded", extra={"model": key, "device": device, "seconds": st.load_seconds})
                return pipe, device
            st.state = "failed"
            st.last_error = "; ".join(errors) or "no OpenVINO device available"
            raise ModelUnavailable(st.last_error)

    @staticmethod
    def _build(kind: str, path: str, device: str):
        import openvino_genai as ov_genai

        # compiled-kernel cache in the (writable) work dir: model dirs are mounted read-only
        from ..config import get_settings

        cache = get_settings().work_dir / "ov_cache" / Path(path).name
        cache.mkdir(parents=True, exist_ok=True)
        cfg = {"CACHE_DIR": str(cache)} if device == "GPU" else {}
        if kind == "vlm":
            return ov_genai.VLMPipeline(path, device, **cfg)
        if kind == "llm":
            return ov_genai.LLMPipeline(path, device, **cfg)
        raise ModelUnavailable(f"kind {kind} is not a generative model")


_manager: ModelManager | None = None


def get_manager(preferred_device: str = "GPU", allow_cpu_fallback: bool = True) -> ModelManager:
    global _manager
    if _manager is None:
        _manager = ModelManager(preferred_device, allow_cpu_fallback)
    return _manager
