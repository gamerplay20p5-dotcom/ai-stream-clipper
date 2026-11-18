"""Download models into ``ASC_MODELS_DIR`` (run once per host; idempotent).

    python -m clipper.ai.download yamnet qwen3-vl-2b-int4 qwen3-1.7b-int4

Community exports without an OpenVINO tokenizer (Qwen3-VL-2B) get one converted locally
with ``openvino-tokenizers`` (requires ``transformers``; no PyTorch needed).
"""

from __future__ import annotations

import argparse
import subprocess
import sys

from ..config import get_settings
from .registry import MODELS, is_installed, model_path


def download(key: str) -> None:
    from huggingface_hub import snapshot_download

    spec = MODELS[key]
    dst = model_path(key)
    if is_installed(key):
        print(f"{key}: already installed at {dst}")
        return
    dst.mkdir(parents=True, exist_ok=True)
    print(f"{key}: downloading {spec.hf_repo} (~{spec.approx_size_gb} GB) -> {dst}")
    snapshot_download(spec.hf_repo, local_dir=str(dst))
    if spec.kind in ("vlm", "llm") and not (dst / "openvino_tokenizer.xml").exists():
        print(f"{key}: converting tokenizer to OpenVINO format")
        # same as the `convert_tokenizer` console script, run with this interpreter
        code = ("import sys; from openvino_tokenizers.cli_tools.convert_tokenizer import convert_hf_tokenizer; "
                "sys.argv=['convert_tokenizer', sys.argv[1], '--with-detokenizer', '-o', sys.argv[1]]; "
                "convert_hf_tokenizer()")
        subprocess.run([sys.executable, "-c", code, str(dst)], check=True)
    print(f"{key}: {'ok' if is_installed(key) else 'INCOMPLETE'}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("models", nargs="*", default=["yamnet", "qwen3-vl-2b-int4", "qwen3-1.7b-int4"])
    a = ap.parse_args(argv)
    print(f"models dir: {get_settings().models_dir}")
    for key in a.models:
        if key.startswith("whisper-"):  # e.g. whisper-small: pre-fill the HF cache used by faster-whisper
            from faster_whisper.utils import download_model

            print(f"{key}: {download_model(key.split('-', 1)[1])}")
            continue
        if key not in MODELS:
            sys.exit(f"unknown model {key}; choose from {sorted(MODELS)}")
        download(key)


if __name__ == "__main__":
    main()
