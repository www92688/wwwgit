# LocalProvider 降级行为（D2：模型缺失不崩溃）+ DummyProvider 确定性
import numpy as np
import pytest

from ych.common.errors import ERR_AI_MODEL_MISSING, AppError
from ych.services.s2_ai.local_provider import LocalProvider
from ych.services.s2_ai.model_registry import ModelRegistry


def make_frame(w=64, h=48):
    return np.zeros((h, w, 3), dtype=np.uint8)


@pytest.fixture
def provider_no_models(tmp_path) -> LocalProvider:
    return LocalProvider(ModelRegistry(base_dir=tmp_path / "empty"),
                         templates_dir=None)


def test_detect_watermark_degrades_without_model(provider_no_models) -> None:
    # 模型缺失 + 无模板库 → 返回空列表而非崩溃（UI 引导手动框选）
    out = provider_no_models.detect_watermark([(0.0, make_frame())])
    assert out == [[]]


def test_detect_subtitle_raises_ai001(provider_no_models) -> None:
    with pytest.raises(AppError) as exc:
        provider_no_models.detect_subtitle([(0.0, make_frame())])
    assert exc.value.code == ERR_AI_MODEL_MISSING


def test_embed_frames_raises_ai001(provider_no_models) -> None:
    with pytest.raises(AppError):
        provider_no_models.embed_frames([(0.0, make_frame())])


def test_inpaint_small_region_telea_fallback(provider_no_models) -> None:
    # 小 patch（<32px）走 TELEA 快速通道：无需模型，mask 区域被填充
    frame = np.full((48, 64, 3), 100, dtype=np.uint8)
    mask = np.zeros((48, 64), dtype=np.uint8)
    mask[20:26, 30:38] = 255
    out = provider_no_models.inpaint(frame, mask)
    assert out.shape == frame.shape
    assert not np.array_equal(out[mask > 0], frame[mask > 0])


def test_inpaint_large_region_telea_fallback_without_model(
    provider_no_models,
) -> None:
    frame = np.full((96, 96, 3), 100, dtype=np.uint8)
    mask = np.zeros((96, 96), dtype=np.uint8)
    mask[10:60, 10:60] = 255
    out = provider_no_models.inpaint(frame, mask)   # LaMa 缺失 → TELEA
    assert out.shape == frame.shape

