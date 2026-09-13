# local_provider D2 降级回归：模型推理运行期失败不得让任务失败
# （此前 detect_watermark/detect_subtitle/embed_frames 的 sess.run 无兜底）
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import numpy as np

from ych.services.s2_ai.local_provider import LocalProvider


class _BrokenSession:
    """get_inputs 正常、run 必炸的假会话（模拟契约不符/OOM）。"""

    def get_inputs(self) -> list[Any]:
        return [SimpleNamespace(name="input")]

    def run(self, *_a: object, **_k: object) -> list[Any]:
        raise RuntimeError("模型契约不符（模拟）")


class _BrokenRegistry:
    def session(self, _name: str) -> _BrokenSession:
        return _BrokenSession()


def _frames(n: int = 2) -> list[tuple[float, np.ndarray]]:
    rng = np.random.default_rng(7)
    return [(i * 0.5, rng.integers(0, 255, (48, 64, 3), dtype=np.uint8))
            for i in range(n)]


def test_detect_watermark_degrades_on_runtime_error() -> None:
    provider = LocalProvider(_BrokenRegistry())   # type: ignore[arg-type]
    result = provider.detect_watermark(_frames())
    assert len(result) == 2                       # 不抛异常，逐帧结果齐全
    assert all(isinstance(d, list) for d in result)


def test_detect_subtitle_degrades_to_classical() -> None:
    provider = LocalProvider(_BrokenRegistry())   # type: ignore[arg-type]
    result = provider.detect_subtitle(_frames())
    assert len(result) == 2                       # 经典文字带检测路径兜底


def test_embed_frames_degrades_to_classical_shape() -> None:
    provider = LocalProvider(_BrokenRegistry())   # type: ignore[arg-type]
    mat = provider.embed_frames(_frames(3))
    assert mat.shape == (3, 512)                  # 经典特征维度口径一致
    norms = np.linalg.norm(mat, axis=1)
    assert np.all((norms == 0) | (np.abs(norms - 1.0) < 1e-5))
