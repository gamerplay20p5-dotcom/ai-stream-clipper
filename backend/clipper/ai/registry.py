"""Catalogue of supported models. Paths are resolved under ``ASC_MODELS_DIR``.

``min_vram_gb`` is an *estimate used for routing* (weights + KV cache at our limits),
not a measured value. Real numbers must come from ``clipper.bench`` on each host.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import get_settings


@dataclass(frozen=True)
class ModelSpec:
    key: str
    kind: str  # vlm | llm | audio
    dirname: str
    hf_repo: str
    family: str
    approx_size_gb: float
    min_vram_gb: float
    max_frames: int = 6
    image_side: int = 448  # frames are resized so the longest side is at most this
    max_new_tokens: int = 384
    notes: str = ""


MODELS: dict[str, ModelSpec] = {
    # --- vision-language -------------------------------------------------------------------
    "qwen3-vl-2b-int4": ModelSpec(
        "qwen3-vl-2b-int4", "vlm", "qwen3-vl-2b-int4", "helenai/Qwen3-VL-2B-Instruct-int4", "qwen3-vl",
        1.8, 3.5, max_frames=8, max_new_tokens=600,
        notes="community OpenVINO export (helenai); tokenizer converted locally with openvino-tokenizers"),
    "qwen3-vl-4b-int4": ModelSpec(
        "qwen3-vl-4b-int4", "vlm", "qwen3-vl-4b-int4", "OpenVINO/Qwen3-VL-4B-Instruct-int4-ov", "qwen3-vl",
        3.1, 5.5, max_frames=8, max_new_tokens=600, notes="official OpenVINO export"),
    "internvl2-1b-int4": ModelSpec(
        "internvl2-1b-int4", "vlm", "internvl2-1b-int4", "OpenVINO/InternVL2-1B-int4-ov", "internvl2",
        0.8, 2.0, max_frames=4, notes="official OpenVINO export; lightest option / CPU fallback"),
    # --- text ----------------------------------------------------------------------------------
    "qwen3-0.6b-int4": ModelSpec(
        "qwen3-0.6b-int4", "llm", "qwen3-0.6b-int4", "OpenVINO/Qwen3-0.6B-int4-ov", "qwen3", 0.45, 1.0,
        max_new_tokens=256),
    "qwen3-1.7b-int4": ModelSpec(
        "qwen3-1.7b-int4", "llm", "qwen3-1.7b-int4", "OpenVINO/Qwen3-1.7B-int4-ov", "qwen3", 1.1, 2.0,
        max_new_tokens=256),
    # --- audio ---------------------------------------------------------------------------------
    "yamnet": ModelSpec("yamnet", "audio", "yamnet", "zeropointnine/yamnet-onnx", "yamnet", 0.02, 0.0),
}


def models_dir() -> Path:
    s = get_settings()
    return Path(s.models_dir)


def model_path(key: str) -> Path:
    return models_dir() / MODELS[key].dirname


def is_installed(key: str) -> bool:
    spec = MODELS.get(key)
    if spec is None:
        return False
    p = model_path(key)
    if spec.kind == "audio":
        return (p / "yamnet.onnx").exists()
    return (p / "openvino_tokenizer.xml").exists() and (p / "openvino_detokenizer.xml").exists() and any(
        p.glob("openvino_*model.xml"))


def installed(kind: str | None = None) -> list[str]:
    return [k for k, s in MODELS.items() if (kind is None or s.kind == kind) and is_installed(k)]
