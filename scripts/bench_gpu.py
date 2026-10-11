# 四模型 CPU vs DML 推理基准（显式输入形状，warm 5 + 计时 20）
# 用途：GPU 分流策略（ModelRegistry._DML_MODELS）的实测依据；换机器/换模型后
# 重跑本脚本即可复核分流是否仍合理（水印 DML 慢 4x、lama DML 不兼容为既测结论）。
# 用法：python scripts/bench_gpu.py（需 onnxruntime-directml 环境看 DML 列）
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort

MODELS = Path("runtime/models")
CASES = {
    "watermark_det_640.onnx": [(1, 3, 640, 640)],
    "subtitle_det_ppocrv4_mobile.onnx": [(1, 3, 960, 960)],
    "clip_vitb32_image.onnx": [(1, 3, 224, 224)],
    "lama_fp32_512.onnx": [(1, 3, 512, 512), (1, 1, 512, 512)],
}

def bench(file: str, providers: list[str]) -> float:
    sess = ort.InferenceSession(str(MODELS / file), providers=providers)
    inputs = sess.get_inputs()
    shapes = CASES[file]
    assert len(inputs) == len(shapes), f"{file}: {len(inputs)} inputs"
    feed = {i.name: np.zeros(s, np.float32)
            for i, s in zip(inputs, shapes, strict=True)}
    for _ in range(5):
        sess.run(None, feed)
    t0 = time.perf_counter()
    for _ in range(20):
        sess.run(None, feed)
    return (time.perf_counter() - t0) / 20 * 1000

for f in CASES:
    cpu = bench(f, ["CPUExecutionProvider"])
    dml = bench(f, ["DmlExecutionProvider", "CPUExecutionProvider"])
    print(f"{f:<36} CPU {cpu:8.1f} ms | DML {dml:8.1f} ms | 加速 {cpu/dml:5.1f}x")
