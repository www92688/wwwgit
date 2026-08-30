# ModelRegistry / TemplateMatcher / LocalProvider 降级路径测试（对照 9.6 + D2）
from __future__ import annotations

import numpy as np
import pytest

from ych.common.errors import ERR_AI_MODEL_LOAD_FAILED, ERR_AI_MODEL_MISSING, AppError
from ych.services.s2_ai.model_registry import ModelRegistry, intra_op_threads
from ych.services.s2_ai.template_matcher import TemplateMatcher


def make_frame(w=64, h=48, color=(30, 30, 30)):
    frame = np.zeros((h, w, 3), dtype=np.uint8)
    frame[:, :] = color
    return frame


# ---------- ModelRegistry ----------
def test_missing_model_raises_ai001(tmp_path) -> None:
    reg = ModelRegistry(base_dir=tmp_path)
    with pytest.raises(AppError) as exc:
        reg.session("watermark")
    assert exc.value.code == ERR_AI_MODEL_MISSING


def test_corrupt_model_raises_ai002(tmp_path) -> None:
    (tmp_path / "watermark_yolov8n_640.onnx").write_bytes(b"not-onnx")
    reg = ModelRegistry(base_dir=tmp_path)
    with pytest.raises(AppError) as exc:
        reg.session("watermark")
    assert exc.value.code == ERR_AI_MODEL_LOAD_FAILED


def test_intra_op_threads_floor_is_two() -> None:
    assert intra_op_threads() >= 2


# ---------- TemplateMatcher ----------
def _write_template(tmp_path):
    import cv2

    tdir = tmp_path / "templates"
    tdir.mkdir()
    rng = np.random.default_rng(123)
    # 带纹理模板（纯色零方差会使归一化相关未定义）
    tmpl = rng.integers(0, 255, (8, 16, 3), dtype=np.uint8)
    cv2.imwrite(str(tdir / "douyin_logo.png"), tmpl)
    return tdir


def test_template_matcher_hits_known_pattern(tmp_path) -> None:

    tdir = _write_template(tmp_path)
    rng = np.random.default_rng(42)
    frame = rng.integers(0, 60, (48, 64, 3), dtype=np.uint8).copy()
    # 右下角贴入与模板同源的纹理块（缩放 1.0 命中，位置已知）
    trng = np.random.default_rng(123)
    frame[38:46, 44:60] = trng.integers(0, 255, (8, 16, 3), dtype=np.uint8)
    matcher = TemplateMatcher(tdir)
    hits = matcher.match(frame)
    assert len(hits) == 1
    bbox = hits[0].bbox
    assert abs(bbox.x * 64 - 44) <= 2 and abs(bbox.y * 48 - 38) <= 2


def test_template_matcher_no_templates_returns_empty(tmp_path) -> None:
    matcher = TemplateMatcher(tmp_path / "no_dir")
    assert matcher.available() is False
    assert matcher.match(make_frame()) == []


def test_template_matcher_no_hit_below_threshold(tmp_path) -> None:
    tdir = _write_template(tmp_path)
    rng = np.random.default_rng(7)
    frame = rng.integers(0, 60, (48, 64, 3), dtype=np.uint8)   # 无目标纹理
    matcher = TemplateMatcher(tdir)
    assert matcher.match(frame) == []




