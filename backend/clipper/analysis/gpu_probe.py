"""OpenVINO GPU probe: devices, properties, compile + inference of a small conv model.

    python -m clipper.analysis.gpu_probe --out /bench/gpu-probe.json

Exit code 0 only when the GPU device exists and FP16/FP32 inference ran.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)
    import numpy as np
    import openvino as ov
    from openvino import opset13 as ops

    core = ov.Core()
    res: dict = {"host": platform.node(), "openvino": ov.__version__, "devices": list(core.available_devices),
                 "runs": []}
    if "GPU" not in res["devices"]:
        res["error"] = "GPU device not visible to OpenVINO"
        print(json.dumps(res, indent=1))
        return 1
    for prop in ("FULL_DEVICE_NAME", "GPU_DEVICE_TOTAL_MEM_SIZE", "OPTIMIZATION_CAPABILITIES", "DEVICE_ARCHITECTURE"):
        try:
            v = core.get_property("GPU", prop)
            res[prop] = v if isinstance(v, (int, str)) else [str(x) for x in v]
        except Exception as exc:  # property names vary across driver/plugin versions
            res[prop] = f"n/a ({type(exc).__name__})"
    x = ops.parameter([1, 3, 224, 224], np.float32, name="x")
    w = ops.constant(np.random.rand(64, 3, 3, 3).astype(np.float32))
    y = ops.relu(ops.convolution(x, w, [1, 1], [1, 1], [1, 1], [1, 1]))
    model = ov.Model([y], [x], "probe")
    inp = np.random.rand(1, 3, 224, 224).astype(np.float32)
    for prec in ("f16", "f32"):
        t0 = time.time()
        compiled = core.compile_model(model, "GPU", {"INFERENCE_PRECISION_HINT": prec})
        tc = time.time() - t0
        req = compiled.create_infer_request()
        req.infer({0: inp})
        t0 = time.time()
        for _ in range(50):
            req.infer({0: inp})
        res["runs"].append({"precision": prec, "compile_s": round(tc, 3),
                            "infer_ms": round(1000 * (time.time() - t0) / 50, 3)})
    try:
        stats = core.get_property("GPU", "GPU_MEMORY_STATISTICS")
        res["memory_statistics"] = {k: int(v) for k, v in dict(stats).items()}
    except Exception:
        pass
    print(json.dumps(res, indent=1))
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(res, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
