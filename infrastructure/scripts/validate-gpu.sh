#!/usr/bin/env bash
# Validate Intel Arc + OpenVINO inside the vision image on 201/210 (no other container touched).
#
#   ./infrastructure/scripts/validate-gpu.sh                 # device probe + tiny model compile/infer
#   ./infrastructure/scripts/validate-gpu.sh --vlm qwen3-vl-2b-int4      # + VLM benchmark on the annotated videos
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
IMAGE="ai-stream-clipper-vision:${ASC_IMAGE_TAG:-0.2.0}"
GID="$(getent group render | cut -d: -f3)"
RUN=(docker run --rm --device /dev/dri/renderD128 --group-add "$GID" --security-opt no-new-privileges:true)

echo "== 1. devices =="
"${RUN[@]}" -e ASC_GPU_DEVICE=/dev/dri/renderD128 "$IMAGE" probe-gpu

echo "== 2. compile + infer a small model on GPU (FP16) =="
"${RUN[@]}" --entrypoint python3 "$IMAGE" - <<'PY'
import time, numpy as np, openvino as ov
from openvino import opset13 as ops
core = ov.Core()
x = ops.parameter([1, 3, 224, 224], np.float32, name="x")
w = ops.constant(np.random.rand(16, 3, 3, 3).astype(np.float32))
y = ops.relu(ops.convolution(x, w, [1, 1], [1, 1], [1, 1], [1, 1]))
model = ov.Model([y], [x], "probe")
for prec in ("f16", "f32"):
    t0 = time.time()
    c = core.compile_model(model, "GPU", {"INFERENCE_PRECISION_HINT": prec})
    tc = time.time() - t0
    req = c.create_infer_request()
    inp = np.random.rand(1, 3, 224, 224).astype(np.float32)
    req.infer({0: inp})
    t0 = time.time()
    for _ in range(50):
        req.infer({0: inp})
    print(f"{prec}: compile {tc:.2f}s, infer {1000*(time.time()-t0)/50:.2f} ms/iter on",
          core.get_property("GPU", "FULL_DEVICE_NAME"))
PY

if [ "${1:-}" = "--vlm" ]; then
  MODEL="${2:-qwen3-vl-2b-int4}"
  MODELS_DIR="${ASC_MODELS_DIR:-/opt/asc/models}"
  OUT="bench-$(hostname)-$MODEL.json"
  echo "== 3. VLM on GPU: $MODEL (results -> $ROOT/$OUT) =="
  "${RUN[@]}" -v "$MODELS_DIR:/models:ro" -v "$ROOT/tests/fixtures:/fixtures:ro" -v "$ROOT:/out" \
    -e ASC_MODELS_DIR=/models -e ASC_WORK_DIR=/tmp --entrypoint python3 "$IMAGE" \
    -m clipper.analysis.vlm_bench --model "$MODEL" --device GPU --annotations /fixtures/annotations.json \
    --repeat 2 --out "/out/$OUT"
fi
echo "== GPU validation finished =="
