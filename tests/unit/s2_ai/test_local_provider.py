# LocalProvider 降级行为（D2：模型缺失不崩溃）+ DummyProvider 确定性
import numpy as np
import pytest

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


def test_detect_subtitle_classical_fallback(provider_no_models) -> None:
    # 模型缺失 → 经典底部文字带检测兜底：底部高对比横条应被检出
    frame = make_frame()
    frame[40:46, 8:56] = 255          # 底部白条（模拟硬编码字幕）
    out = provider_no_models.detect_subtitle([(0.0, frame)])
    assert len(out) == 1
    assert out[0], "经典字幕检测应产出候选框"
    det = out[0][0]
    assert det.bbox.y + det.bbox.h / 2 > 0.55   # 位于底部文字带
    # 纯黑帧 → 无检出
    assert provider_no_models.detect_subtitle([(0.0, make_frame())]) == [[]]


def test_embed_frames_classical_fallback(provider_no_models) -> None:
    # CLIP 缺失 → 经典 512 维特征：确定性、L2 归一、区分不同画面
    p = provider_no_models
    f1 = make_frame()
    f1[:, :, 0] = 200                 # 蓝色画面
    f2 = make_frame()
    f2[:, :, 2] = 200                 # 红色画面
    v1 = p.embed_frames([(0.0, f1), (1.0, f1)])
    v2 = p.embed_frames([(0.0, f2)])
    assert v1.shape == (2, 512) and v2.shape == (1, 512)
    assert np.allclose(v1[0], v1[1])            # 同帧 → 同向量（确定性）
    assert not np.allclose(v1[0], v2[0])        # 不同画面 → 不同向量
    norms = np.linalg.norm(v1, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-5)   # 已归一化


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



# ---------- 推理熔断：连续失败后本会话直接走兜底 ----------
class _FakeInput:
    name = "in"


class _FakeSession:
    def __init__(self, fail: bool = True) -> None:
        self.run_calls = 0
        self._fail = fail

    def get_inputs(self) -> list[_FakeInput]:
        return [_FakeInput()]

    def run(self, *_a: object, **_k: object):
        self.run_calls += 1
        if self._fail:
            raise RuntimeError("boom")
        return [np.zeros((1, 1, 16, 16), dtype=np.float32)]


class _FakeRegistry:
    """session() 返回可替换的当前 session（模拟重新下载/替换模型）。"""

    def __init__(self) -> None:
        self.sess: _FakeSession | None = None

    def session(self, _key: str) -> _FakeSession:
        assert self.sess is not None
        return self.sess


def test_subtitle_breaker_stops_retrying_same_session() -> None:
    reg = _FakeRegistry()
    s1 = _FakeSession(fail=True)
    reg.sess = s1
    p = LocalProvider(reg, templates_dir=None)
    frames = [(0.0, make_frame())]
    for _ in range(3):
        assert p.detect_subtitle(frames) == [[]]   # 兜底经典检测照常工作
    assert s1.run_calls == 2            # 连续失败 2 次 → 第 3 次起熔断不碰模型

    s2 = _FakeSession(fail=True)        # 模型重新下载 → session 换新
    reg.sess = s2
    p.detect_subtitle(frames)
    assert s2.run_calls == 1            # 熔断自动复位，恢复尝试


def test_subtitle_breaker_resets_on_success() -> None:
    reg = _FakeRegistry()
    reg.sess = _FakeSession(fail=True)
    p = LocalProvider(reg, templates_dir=None)
    frames = [(0.0, make_frame())]
    p.detect_subtitle(frames)           # 失败 1 次（未达熔断阈值）
    good = _FakeSession(fail=False)
    reg.sess = good                     # 换好模型
    assert p.detect_subtitle(frames) == [[]]
    assert good.run_calls == 1
    p.detect_subtitle(frames)           # 成功不累积失败计数
    assert good.run_calls == 2


def test_watermark_breaker_stops_retrying_same_session() -> None:
    reg = _FakeRegistry()
    s1 = _FakeSession(fail=True)
    reg.sess = s1
    p = LocalProvider(reg, templates_dir=None)
    frames = [(0.0, make_frame())]
    for _ in range(3):
        assert p.detect_watermark(frames) == [[]]   # 纯模板兜底（无模板→空）
    assert s1.run_calls == 2
