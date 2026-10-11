# M2 预处理测试（对照 13.5 / tasks/10-m2-preprocess.md）：纯函数 + 替身单测
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from ych.common.schemas import BBox, ManualRegions, MediaInfo
from ych.core.m2_preprocess.filter_only_processor import build_vf_chain, even_down
from ych.core.m2_preprocess.frame_level_processor import RepairStats, repair_frame
from ych.core.m2_preprocess.ops import (
    PreprocessOps,
    decide_path,
    is_only_soft_strip,
    make_preprocess_payload,
    need_frame_pass,
    revive_ops,
)
from ych.core.m2_preprocess.region_temporal import RegionTemporal, manual_spans
from ych.core.m2_preprocess.subtitle_handler import SubtitleHandler
from ych.services.s2_ai.postprocess import RegionSpan
from ych.services.s2_ai.provider import Detection


def _info(w: int = 1920, h: int = 1080, dur: float = 10.0,
          soft: str = "") -> MediaInfo:
    return MediaInfo(path="fake.mp4", duration_s=dur, width=w, height=h,
                     fps=30.0, soft_subtitle_codec=soft)


# ---------- build_vf_chain 参数矩阵（表驱动） ----------

@pytest.mark.parametrize(
    ("ops", "w", "h", "expect"),
    [
        (PreprocessOps(), 1920, 1080, ""),
        # 裁剪：归一化→像素
        (PreprocessOps(crop_rect=BBox(0.25, 0.25, 0.5, 0.5)), 640, 480,
         "crop=320:240:160:120"),
        # 全幅裁剪 → 偶对齐（101→100）
        (PreprocessOps(crop_rect=BBox(0.0, 0.0, 1.0, 1.0)), 101, 51,
         "crop=100:50:0:0"),
        # 竖屏裁切：1920x1080 → 9:16 居中裁两侧
        (PreprocessOps(aspect_target=(9, 16), aspect_strategy="crop"),
         1920, 1080, "crop=606:1080:657:0"),
        # 横屏裁切：1080x1920 → 16:9 居中裁上下
        (PreprocessOps(aspect_target=(16, 9), aspect_strategy="crop"),
         1080, 1920, "crop=1080:606:0:657"),
        # pad：窄画布左右补黑边（1080x1920 → 16:9）
        (PreprocessOps(aspect_target=(16, 9), aspect_strategy="pad"),
         1080, 1920, "pad=3412:1920:1166:0:black"),
        # pad：宽画布上下补黑边（1920x1080 → 9:16）
        (PreprocessOps(aspect_target=(9, 16), aspect_strategy="pad"),
         1920, 1080, "pad=1920:3412:0:1166:black"),
        # 比例已满足 → 无滤镜
        (PreprocessOps(aspect_target=(16, 9), aspect_strategy="crop"),
         1920, 1080, ""),
    ],
)
def test_build_vf_chain_matrix(ops: PreprocessOps, w: int, h: int,
                               expect: str) -> None:
    assert build_vf_chain(ops, _info(w=w, h=h)) == expect


def test_crop_then_pad_combination() -> None:
    ops = PreprocessOps(
        crop_rect=BBox(0.25, 0.25, 0.5, 0.5),
        aspect_target=(16, 9),
        aspect_strategy="crop",
    )
    # 裁剪后 320x240（4:3）→ 16:9 再居中裁高：nh=int(320/(16/9))=180
    # y 偏移叠加：初始 120 + 居中 (240-180)//2 = 150
    assert build_vf_chain(ops, _info(640, 480)) == "crop=320:180:160:150"


def test_even_down() -> None:
    assert even_down(101) == 100 and even_down(2) == 2 and even_down(1) == 2


# ---------- 路径判定纯函数（表驱动） ----------

@pytest.mark.parametrize(
    ("ops", "soft", "route", "expect"),
    [
        (PreprocessOps(remove_watermark_mode="auto"), False, "none", "frame"),
        (PreprocessOps(remove_watermark_mode="manual"), False, "none", "frame"),
        (PreprocessOps(remove_subtitle_mode="manual"), False, "hard", "frame"),
        (PreprocessOps(remove_subtitle_mode="auto"), False, "hard", "frame"),
        # 仅软字幕（其余全 off）→ remux 秒级
        (PreprocessOps(remove_subtitle_mode="auto"), True, "soft", "remux"),
        # 软轨 + 裁剪 → 纯滤镜（转码天然不带字幕轨）
        (PreprocessOps(remove_subtitle_mode="auto", crop_rect=BBox(0, 0, 0.5, 1)),
         True, "soft", "filter"),
        # 仅裁剪 → filter；仅去原声 → remux（偏差修正承接）
        (PreprocessOps(crop_rect=BBox(0, 0, 0.5, 1)), False, "none", "filter"),
        (PreprocessOps(strip_audio=True), False, "none", "remux"),
        # 什么都没有 → skip
        (PreprocessOps(), False, "none", "skip"),
    ],
)
def test_decide_path(ops: PreprocessOps, soft: bool, route: str,
                     expect: str) -> None:
    assert decide_path(ops, soft, route) == expect   # type: ignore[arg-type]


def test_is_only_soft_strip() -> None:
    assert is_only_soft_strip(PreprocessOps(remove_subtitle_mode="auto"),
                              True, "soft")
    assert not is_only_soft_strip(
        PreprocessOps(remove_subtitle_mode="auto", strip_audio=True),
        True, "soft")
    assert not is_only_soft_strip(PreprocessOps(), True, "soft")


def test_need_frame_pass() -> None:
    assert need_frame_pass(PreprocessOps(remove_watermark_mode="off"), "soft") is False
    assert need_frame_pass(PreprocessOps(remove_watermark_mode="auto"), "none")
    assert need_frame_pass(PreprocessOps(), "hard")


# ---------- payload 组装/还原往返 ----------

def test_payload_roundtrip() -> None:
    ops = PreprocessOps(
        remove_watermark_mode="manual",
        watermark_regions=ManualRegions(rects=[BBox(0.1, 0.1, 0.2, 0.2)]),
        crop_rect=BBox(0.0, 0.0, 0.5, 0.5),
        aspect_target=(9, 16),
    )
    payload = make_preprocess_payload([("a.mp4", ops)])
    raw = json.loads(json.dumps(payload.data))
    revived = revive_ops(raw["items"][0]["ops"])
    assert revived.remove_watermark_mode == "manual"
    assert revived.watermark_regions is not None
    assert revived.watermark_regions.rects[0] == BBox(0.1, 0.1, 0.2, 0.2)
    assert revived.crop_rect == BBox(0.0, 0.0, 0.5, 0.5)
    assert revived.aspect_target == (9, 16)


# ---------- RegionTemporal / manual_spans ----------

def test_manual_spans_full_duration() -> None:
    regions = ManualRegions(rects=[BBox(0.25, 0.25, 0.5, 0.5),
                                   BBox(0.0, 0.8, 1.0, 0.2)])
    spans = manual_spans(regions, 30.0)
    assert len(spans) == 2
    assert all(s.t_start == 0.0 and s.t_end == 30.0 for s in spans)
    assert manual_spans(None, 30.0) == []


def test_manual_spans_time_window() -> None:
    regions = ManualRegions(rects=[BBox(0.1, 0.1, 0.3, 0.3)],
                            time_start_s=2.0, time_end_s=5.0)
    spans = manual_spans(regions, 10.0)
    assert spans[0].t_start == 2.0 and spans[0].t_end == 5.0


def test_region_temporal_cluster_and_stabilize() -> None:
    ts = [i * 0.5 for i in range(10)]
    Detection(bbox=BBox(0.4, 0.4, 0.2, 0.2), confidence=0.9,
                    label="watermark", ts=0.0)
    per_frame = [[Detection(bbox=BBox(0.4, 0.4, 0.2, 0.2), confidence=0.9,
                            label="watermark", ts=t)] for t in ts]
    spans = RegionTemporal.build_spans(per_frame, ts, fps=2.0)
    assert len(spans) == 1
    assert spans[0].t_start == pytest.approx(0.0)
    assert spans[0].t_end == pytest.approx(4.5)
    # 外扩后不小于原框
    assert spans[0].bbox.w > 0.2


# ---------- SubtitleHandler.route（DummyProvider） ----------

class DummySubtitleProvider:
    """detect_subtitle 可开关的替身：检出框位于底部文字带。"""

    def __init__(self, found: bool, bottom: bool = True) -> None:
        self.found = found
        self.bottom = bottom
        self.calls = 0

    def _dets(self, frames):   # type: ignore[no-untyped-def]
        self.calls += 1
        if not self.found:
            return [[] for _ in frames]
        y = 0.75 if self.bottom else 0.05
        return [
            [Detection(bbox=BBox(0.2, y, 0.6, 0.15), confidence=0.95,
                       label="subtitle", ts=t)]
            for (t, _f) in frames
        ]

    def detect_watermark(self, frames):    # type: ignore[no-untyped-def]
        return [[] for _ in frames]

    def detect_subtitle(self, frames):     # type: ignore[no-untyped-def]
        return self._dets(frames)

    def inpaint(self, frame, mask):        # type: ignore[no-untyped-def]
        raise AssertionError("route 不应调用 inpaint")

    def embed_frames(self, frames):        # type: ignore[no-untyped-def]
        raise AssertionError("route 不应调用 embed_frames")


class DummyExtractor:
    """single(ts) 返回固定黑帧。"""

    def __init__(self) -> None:
        self.calls = 0

    def single(self, path: Path, ts: float):   # type: ignore[no-untyped-def]
        self.calls += 1
        return SimpleNamespace(ts=ts, img=np.zeros((48, 64, 3), dtype=np.uint8))


def _handler(provider: DummySubtitleProvider) -> tuple[SubtitleHandler, DummyExtractor]:
    extractor = DummyExtractor()
    handler = SubtitleHandler(None, extractor, provider)   # type: ignore[arg-type]
    return handler, extractor


@pytest.mark.parametrize(
    ("mode", "soft", "found", "bottom", "expect"),
    [
        ("off", False, True, True, "none"),
        ("off", True, True, True, "none"),
        ("manual", False, False, True, "hard"),
        ("auto", True, False, True, "soft"),       # 有软轨：不触发 OCR
        ("auto", False, True, True, "hard"),       # OCR 检出底部文字带
        ("auto", False, False, True, "none"),      # 无检出 → 跳过不报错
        ("auto", False, True, False, "none"),      # 顶部文字不算字幕带
    ],
)
def test_subtitle_route(mode: str, soft: bool, found: bool, bottom: bool,
                        expect: str) -> None:
    provider = DummySubtitleProvider(found=found, bottom=bottom)
    handler, extractor = _handler(provider)
    assert handler.route(_info(soft="srt" if soft else ""), mode) == expect
    assert extractor.calls == (6 if (mode == "auto" and not soft) else 0)


# ---------- repair_frame 纯逻辑（紧凑笔画掩码 + 贴片 + 缓存） ----------

class InpaintProvider:
    """inpaint 将掩码区填充为绿色（BGR 0,255,0），记录掩码并计数。"""

    def __init__(self) -> None:
        self.calls = 0
        self.last_mask: np.ndarray | None = None

    def inpaint(self, frame, mask):    # type: ignore[no-untyped-def]
        self.calls += 1
        self.last_mask = mask.copy()
        out = frame.copy()
        out[mask > 0] = (0, 255, 0)
        return out

    def detect_watermark(self, frames):     # type: ignore[no-untyped-def]
        return [[] for _ in frames]

    def detect_subtitle(self, frames):      # type: ignore[no-untyped-def]
        return [[] for _ in frames]

    def embed_frames(self, frames):         # type: ignore[no-untyped-def]
        raise AssertionError


CENTER_SPAN = RegionSpan(bbox=BBox(0.25, 0.25, 0.5, 0.5),
                         t_start=0.0, t_end=10.0)
CENTER_SPAN_MANUAL = RegionSpan(bbox=BBox(0.25, 0.25, 0.5, 0.5),
                                t_start=0.0, t_end=10.0, manual=True)


def _noise_frame(seed: int, bright: int = 0) -> np.ndarray:
    """低幅噪声背景（局部中值≈100+bright），紧凑掩码可稳定区分笔画。"""
    rng = np.random.default_rng(seed)
    return rng.integers(88, 113, (48, 64, 3), dtype=np.uint8) + bright


def _stroke_frame(seed: int = 7, bright: int = 0) -> np.ndarray:
    """噪声背景 + 中部白色笔画块（CENTER_SPAN 框内）。"""
    frame = _noise_frame(seed, bright)
    frame[18:26, 28:34] = 255          # 笔画块（y12..36 / x16..48 框内）
    return frame


def test_repair_frame_tight_mask_replaces_strokes_only() -> None:
    """笔画像素被修复填充，笔画外的背景纹理保持当前帧原样。"""
    provider = InpaintProvider()
    stats = RepairStats()
    cache: dict = {}
    last: dict = {}
    frame = _stroke_frame()

    f1 = repair_frame(frame, 1.0, [CENTER_SPAN], provider,
                      cache, last, frame_idx=0, size=(64, 48), stats=stats)
    assert provider.calls == 1
    assert provider.last_mask is not None
    # 掩码覆盖笔画、不覆盖远处背景（整框掩码是旧缺陷，回归护栏）
    assert provider.last_mask[22, 30] == 255
    assert provider.last_mask[12, 16] == 0
    # 笔画中心 = 修复填充；框内远处背景与框外角落 = 原像素
    assert (f1[22, 30] == (0, 255, 0)).all()
    assert (f1[33, 18] == frame[33, 18]).all()
    assert (f1[2, 2] == frame[2, 2]).all()


def test_repair_frame_auto_empty_mask_skips() -> None:
    """auto 区域内无可辨识笔画（纯色/纯背景）：不推理、不贴片。"""
    provider = InpaintProvider()
    stats = RepairStats()
    cache: dict = {}
    last: dict = {}
    frame = _noise_frame(3)
    frame[:, :] = (0, 0, 255)          # 纯色框内容 → 紧凑掩码为空

    f1 = repair_frame(frame, 1.0, [CENTER_SPAN], provider,
                      cache, last, frame_idx=0, size=(64, 48), stats=stats)
    assert provider.calls == 0
    assert (f1 == frame).all()


def test_repair_frame_manual_empty_mask_falls_back_full_box() -> None:
    """手动框选 + 纯色内容：遵循用户意图回退整框修复。"""
    provider = InpaintProvider()
    stats = RepairStats()
    cache: dict = {}
    last: dict = {}
    frame = _noise_frame(3)
    frame[:, :] = (0, 0, 255)

    f1 = repair_frame(frame, 1.0, [CENTER_SPAN_MANUAL], provider,
                      cache, last, frame_idx=0, size=(64, 48), stats=stats)
    assert provider.calls == 1
    assert (f1[24, 32] == (0, 255, 0)).all()    # 框中心 = 修复填充
    assert (f1[2, 2] == frame[2, 2]).all()      # 框外保持原样


def test_repair_frame_dense_content_falls_back_full_box() -> None:
    """框内几乎全是笔画（致密台标）：整框修复而非碎片掩码。"""
    provider = InpaintProvider()
    stats = RepairStats()
    cache: dict = {}
    last: dict = {}
    frame = _noise_frame(5)
    frame[13:35, 17:47] = 255          # 占框内 ~85%（含边缘留噪声）

    f1 = repair_frame(frame, 1.0, [CENTER_SPAN], provider,
                      cache, last, frame_idx=0, size=(64, 48), stats=stats)
    assert provider.calls == 1
    assert (f1[24, 32] == (0, 255, 0)).all()


def test_repair_frame_cache_hit_and_period_refresh() -> None:
    """静止画面吃满缓存周期；≥30 帧强制刷新。"""
    provider = InpaintProvider()
    stats = RepairStats()
    cache: dict = {}
    last: dict = {}
    frame = _stroke_frame()

    repair_frame(frame.copy(), 1.0, [CENTER_SPAN], provider,
                 cache, last, frame_idx=0, size=(64, 48), stats=stats)
    assert provider.calls == 1
    f2 = repair_frame(frame.copy(), 1.5, [CENTER_SPAN], provider,
                      cache, last, frame_idx=1, size=(64, 48), stats=stats)
    assert provider.calls == 1                  # 命中缓存不再推理
    assert stats.cache_hits == 1
    assert (f2[22, 30] == (0, 255, 0)).all()    # 贴片仍生效

    repair_frame(frame.copy(), 2.0, [CENTER_SPAN], provider,
                 cache, last, frame_idx=31, size=(64, 48), stats=stats)
    assert provider.calls == 2                  # 周期到 → 强制刷新
    assert stats.inpaint_calls == 2


def test_repair_frame_adaptive_refresh_on_motion() -> None:
    """背景大幅运动：未到 30 帧也立即刷新（旧贴片会穿帮）。"""
    provider = InpaintProvider()
    stats = RepairStats()
    cache: dict = {}
    last: dict = {}
    frame1 = _stroke_frame(seed=7)
    frame2 = _stroke_frame(seed=8, bright=60)   # 背景整体抬升 → 大偏差

    repair_frame(frame1.copy(), 1.0, [CENTER_SPAN], provider,
                 cache, last, frame_idx=0, size=(64, 48), stats=stats)
    assert provider.calls == 1
    r2 = repair_frame(frame2, 1.1, [CENTER_SPAN], provider,
                      cache, last, frame_idx=1, size=(64, 48), stats=stats)
    assert provider.calls == 2                  # 自适应刷新触发
    assert (r2[22, 30] == (0, 255, 0)).all()    # 新背景上笔画仍被修复


# ---------- 时间一致性掩码（静态水印兜底证据） ----------

def test_temporal_mask_catches_static_faint_strokes() -> None:
    """静态淡笔画：单帧低于阈值，但跨帧一致 → 时间掩码整段收进。"""
    rng = np.random.default_rng(2)
    patches = []
    for _i in range(6):
        # 大贴片：掩码带 ~32px 光晕外扩，远处背景断言需离笔画足够远
        p = rng.integers(88, 113, (120, 160, 3), dtype=np.uint8)
        # 淡笔画：与背景差仅 ~20（单帧阈值 12/自适应后接近噪声，易漏）
        p[55:63, 75:81] = rng.integers(105, 120, (8, 6, 3), dtype=np.uint8)
        patches.append(p)
    from ych.core.m2_preprocess.frame_level_processor import temporal_stroke_mask

    mask = temporal_stroke_mask(patches)
    assert mask is not None
    assert mask[59, 77] == 255                  # 笔画被收进掩码
    assert mask[8, 8] == 0                      # 远处背景不误标


def test_temporal_mask_ignores_moving_content() -> None:
    """只在个别帧出现的亮斑（噪声/运动物）：时间掩码不收。"""
    rng = np.random.default_rng(4)
    patches = []
    for i in range(6):
        p = rng.integers(88, 113, (48, 64, 3), dtype=np.uint8)
        pos = (8 + i * 5, 8 + i * 8)            # 亮斑逐帧移动
        p[pos[0]:pos[0] + 6, pos[1]:pos[1] + 6] = 255
        patches.append(p)
    from ych.core.m2_preprocess.frame_level_processor import temporal_stroke_mask

    assert temporal_stroke_mask(patches) is None


def test_repair_frame_prefers_temporal_mask() -> None:
    """span 带时间掩码：修复与回贴都用它（单帧紧凑掩码失效也照修）。"""
    from ych.services.s2_ai.postprocess import RegionSpan as RS

    provider = InpaintProvider()
    stats = RepairStats()
    cache: dict = {}
    last: dict = {}
    frame = _noise_frame(3)
    frame[:, :] = (0, 0, 255)                   # 纯色：单帧紧凑掩码为空
    tmask = np.zeros((24, 32), dtype=np.uint8)
    tmask[8:16, 12:18] = 255                    # 时间证据：框内笔画在此
    span = RS(bbox=BBox(0.25, 0.25, 0.5, 0.5), t_start=0.0, t_end=10.0,
              temporal_mask=tmask)

    f1 = repair_frame(frame, 1.0, [span], provider,
                      cache, last, frame_idx=0, size=(64, 48), stats=stats)
    assert provider.calls == 1
    assert provider.last_mask is not None
    assert provider.last_mask[24, 30] == 255    # 掩码来自时间证据（块内部点）
    assert (f1[24, 30] == (0, 255, 0)).all()    # 笔画被修复
    assert (f1[4, 4] == frame[4, 4]).all()      # 掩码外原样

    f2 = repair_frame(frame, 1.5, [span], provider,
                      cache, last, frame_idx=1, size=(64, 48), stats=stats)
    assert provider.calls == 1                  # 缓存命中
    assert (f2[24, 30] == (0, 255, 0)).all()    # 贴片仍按时间掩码生效



