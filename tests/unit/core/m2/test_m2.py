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


# ---------- repair_frame 纯逻辑（遮罩生效 + 缓存命中） ----------

class InpaintProvider:
    """inpaint 将掩码区填充为绿色（BGR 0,255,0），并计数。"""

    def __init__(self) -> None:
        self.calls = 0

    def inpaint(self, frame, mask):    # type: ignore[no-untyped-def]
        self.calls += 1
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


def _red_frame() -> np.ndarray:
    frame = np.full((48, 64, 3), 0, dtype=np.uint8)
    frame[:, :] = (0, 0, 255)          # BGR 红
    return frame


def test_repair_frame_mask_and_cache() -> None:
    provider = InpaintProvider()
    stats = RepairStats()
    cache: dict = {}
    last: dict = {}
    size = (64, 48)

    f1 = repair_frame(_red_frame(), 1.0, [CENTER_SPAN], provider,
                      cache, last, frame_idx=0, size=size, stats=stats)
    assert provider.calls == 1
    # 中部被替换为绿色，四角保持红色
    assert (f1[24, 32] == (0, 255, 0)).all()
    assert (f1[2, 2] == (0, 0, 255)).all()

    # 第 2 帧命中缓存（<30 帧不刷新）
    f2 = repair_frame(_red_frame(), 1.5, [CENTER_SPAN], provider,
                      cache, last, frame_idx=1, size=size, stats=stats)
    assert provider.calls == 1
    assert stats.cache_hits == 1
    assert (f2[24, 32] == (0, 255, 0)).all()

    # 时间窗外不修复
    f3 = repair_frame(_red_frame(), 99.0, [CENTER_SPAN], provider,
                      cache, last, frame_idx=2, size=size, stats=stats)
    assert (f3[24, 32] == (0, 0, 255)).all()
    assert stats.cache_hits == 1

    # ≥30 帧后强制刷新缓存
    repair_frame(_red_frame(), 2.0, [CENTER_SPAN], provider,
                 cache, last, frame_idx=31, size=size, stats=stats)
    assert provider.calls == 2
    assert stats.inpaint_calls == 2



