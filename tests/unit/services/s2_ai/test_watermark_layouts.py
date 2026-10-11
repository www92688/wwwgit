# detect_watermark 两种输出布局等价性：自训模型 (1,N,≥5) 行布局（已带 NMS）
# 与标准 ultralytics 导出 (1,C≤10,anchors) 通道优先预 NMS（公开水印模型）。
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from ych.services.s2_ai.local_provider import LocalProvider


class _FakeSession:
    def __init__(self, output: np.ndarray) -> None:
        self._output = output

    def get_inputs(self) -> list[Any]:
        return [SimpleNamespace(name="images")]

    def run(self, *_a: object, **_k: object) -> list[np.ndarray]:
        return [self._output]


class _FakeRegistry:
    def __init__(self, output: np.ndarray) -> None:
        self._output = output

    def session(self, _name: str) -> _FakeSession:
        return _FakeSession(self._output)


def _frame() -> np.ndarray:
    return np.full((48, 64, 3), 128, dtype=np.uint8)


# letterbox(640) 后同一检测框：cx=320, cy=320, w=100, h=100, conf=0.9
_ROWS = np.array([[[320, 320, 100, 100, 0.9, 0.0],
                   [10, 10, 5, 5, 0.1, 0.0]]], dtype=np.float32)      # (1,2,6)


def _transposed() -> np.ndarray:
    out = np.zeros((1, 5, 8400), dtype=np.float32)                    # (1,5,8400)
    out[0, :, 4200] = [320, 320, 100, 100, 0.9]
    return out


def test_detect_watermark_rows_layout() -> None:
    provider = LocalProvider(_FakeRegistry(_ROWS), templates_dir=None)  # type: ignore[arg-type]
    (dets,) = provider.detect_watermark([(0.0, _frame())])
    assert len(dets) == 1
    assert dets[0].confidence == pytest.approx(0.9)


def test_detect_watermark_transposed_layout_matches_rows() -> None:
    rows = LocalProvider(_FakeRegistry(_ROWS), templates_dir=None)      # type: ignore[arg-type]
    trans = LocalProvider(_FakeRegistry(_transposed()), templates_dir=None)  # type: ignore[arg-type]
    (a,) = rows.detect_watermark([(0.0, _frame())])
    (b,) = trans.detect_watermark([(0.0, _frame())])
    assert len(b) == 1
    assert b[0].confidence == a[0].confidence
    assert np.allclose(
        (b[0].bbox.x, b[0].bbox.y, b[0].bbox.w, b[0].bbox.h),
        (a[0].bbox.x, a[0].bbox.y, a[0].bbox.w, a[0].bbox.h),
    )


def test_detect_watermark_transposed_low_conf_filtered() -> None:
    out = np.zeros((1, 5, 8400), dtype=np.float32)
    out[0, :, 4200] = [320, 320, 100, 100, 0.2]   # 低于 0.45 阈值
    provider = LocalProvider(_FakeRegistry(out), templates_dir=None)    # type: ignore[arg-type]
    (dets,) = provider.detect_watermark([(0.0, _frame())])
    assert dets == []
