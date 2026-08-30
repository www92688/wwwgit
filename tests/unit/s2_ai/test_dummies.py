# DummyProvider 确定性验证（M2/M3 全部单测的统一替身，详设 9.6）
import numpy as np

from tests.unit.s2_ai.dummies import DummyProvider
from ych.services.s2_ai.provider import Detection


def make_frame(seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 255, (48, 64, 3), dtype=np.uint8)


def test_dummy_watermark_deterministic_center_bbox() -> None:
    dp = DummyProvider()
    frames = [(i * 0.5, make_frame(i)) for i in range(3)]
    dets = dp.detect_watermark(frames)
    assert len(dets) == 3 and all(len(d) == 1 for d in dets)
    assert all(isinstance(d[0], Detection) for d in dets)
    assert dets[0][0].bbox == dets[1][0].bbox   # 同一预设 BBox


def test_dummy_subtitle_toggle() -> None:
    on = DummyProvider()
    off = DummyProvider(subtitle_enabled=False)
    frames = [(0.0, make_frame())]
    assert len(on.detect_subtitle(frames)[0]) == 1
    assert off.detect_subtitle(frames)[0] == []


def test_dummy_inpaint_fills_fixed_color_and_counts() -> None:
    dp = DummyProvider()
    frame = np.full((48, 64, 3), 10, dtype=np.uint8)
    mask = np.zeros((48, 64), dtype=np.uint8)
    mask[5:15, 5:25] = 255
    out = dp.inpaint(frame, mask)
    assert dp.inpaint_calls == 1
    assert (out[10, 10] == np.array(dp.fill_color)).all()
    assert (out[40, 40] == 10).all()   # mask 外不变


def test_dummy_embed_hash_vectors_l2_normalized() -> None:
    dp = DummyProvider()
    v = dp.embed_frames([(0.0, make_frame(7))])
    assert v.shape == (1, 512)
    assert abs(float(np.linalg.norm(v[0])) - 1.0) < 1e-5
    v2 = dp.embed_frames([(0.0, make_frame(7))])
    assert np.allclose(v, v2)          # 可复现

