# postprocess 纯函数测试（对照 9.4；覆盖率目标 ≥90%）
from __future__ import annotations

import pytest

from ych.common.schemas import BBox
from ych.services.s2_ai.postprocess import (
    RegionSpan,
    expand_bbox,
    iou,
    merge_boxes,
    nms,
    stabilize_regions,
    temporal_cluster,
)
from ych.services.s2_ai.provider import Detection


def det(x, y, w, h, conf=0.9, ts=0.0):
    return Detection(bbox=BBox(x=x, y=y, w=w, h=h), confidence=conf,
                     label="watermark", ts=ts)


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        (BBox(0, 0, 1, 1), BBox(0, 0, 1, 1), 1.0),
        (BBox(0, 0, 1, 1), BBox(1, 1, 1, 1), 0.0),
        (BBox(0, 0, 2, 2), BBox(1, 1, 1, 1), 0.25),
        (BBox(0.9, 0.9, 0.5, 0.5), BBox(0, 0, 1, 1), 0.01 / 1.24),
    ],
)
def test_iou_values(a, b, expected) -> None:
    assert abs(iou(a, b) - expected) < 1e-6


def test_nms_empty_and_single() -> None:
    assert nms([], 0.45) == []
    assert len(nms([det(0.1, 0.1, 0.2, 0.2)], 0.45)) == 1


def test_nms_suppresses_overlap_keeps_highest_conf() -> None:
    high = det(0.1, 0.1, 0.3, 0.3, conf=0.95)
    low = det(0.12, 0.12, 0.3, 0.3, conf=0.6)
    far = det(0.8, 0.8, 0.15, 0.15, conf=0.5)
    kept = nms([low, high, far], 0.45)
    assert len(kept) == 2
    assert kept[0].confidence == 0.95


def test_merge_boxes_groups_overlaps() -> None:
    a = det(0.0, 0.0, 0.3, 0.3, conf=0.7)
    b = det(0.05, 0.05, 0.3, 0.3, conf=0.95)
    c = det(0.7, 0.7, 0.2, 0.2, conf=0.5)
    merged = merge_boxes([a, b, c], iou_thr=0.3)
    assert len(merged) == 2
    top = max(merged, key=lambda d: d.confidence)
    assert top.confidence == 0.95
    # 外接框覆盖两框并集
    assert top.bbox.x <= 0.0 and top.bbox.w >= 0.35


def test_expand_bbox_clamps_to_unit_square() -> None:
    inner = BBox(x=0.4, y=0.4, w=0.2, h=0.2)
    grown = expand_bbox(inner, margin=0.5)
    assert abs(grown.x - 0.3) < 1e-9 and abs(grown.y - 0.3) < 1e-9
    assert abs(grown.w - 0.4) < 1e-6 and abs(grown.h - 0.4) < 1e-6
    edge = expand_bbox(BBox(x=0.0, y=0.0, w=0.2, h=0.2), margin=0.5)
    assert edge.x == 0.0 and edge.y == 0.0
    overflow = expand_bbox(BBox(x=0.9, y=0.9, w=0.2, h=0.2), margin=0.5)
    assert overflow.x + overflow.w <= 1.0 + 1e-9
    assert overflow.y + overflow.h <= 1.0 + 1e-9
# ---------- temporal_cluster ----------
def test_temporal_cluster_empty_inputs() -> None:
    assert temporal_cluster([], []) == []
    assert temporal_cluster([[]], [0.0]) == []


def test_static_region_passes_presence_ratio() -> None:
    # 同一 BBox 连续出现 6 帧 → 出现率 100% ≥ 60% → 静态区域
    frames = [[det(0.4, 0.9, 0.2, 0.08, ts=i * 0.5)] for i in range(6)]
    ts = [i * 0.5 for i in range(6)]
    spans = temporal_cluster(frames, ts)
    assert len(spans) == 1
    assert spans[0].t_start == 0.0 and spans[0].t_end == 2.5


def test_transient_flicker_below_presence_ratio_dropped() -> None:
    # 活跃 3 帧、活跃跨度 10 帧 → 出现率 30% < 60% → 剔除
    ts = [float(i) for i in range(10)]
    frames: list[list[Detection]] = [[] for _ in range(10)]
    frames[2] = [det(0.4, 0.9, 0.2, 0.08, ts=ts[2])]
    frames[4] = [det(0.4, 0.9, 0.2, 0.08, ts=ts[4])]
    frames[9] = [det(0.4, 0.9, 0.2, 0.08, ts=ts[9])]
    assert temporal_cluster(frames, ts) == []


def test_presence_ratio_boundary_exactly_06_passes() -> None:
    # 活跃 6 帧 / 跨度内总帧数 10 → 0.6，恰好达标
    ts = [float(i) for i in range(10)]
    frames: list[list[Detection]] = [[] for _ in range(10)]
    for i in (0, 1, 2, 3, 4, 9):
        frames[i] = [det(0.3, 0.3, 0.1, 0.1)]
    spans = temporal_cluster(frames, ts)
    assert len(spans) == 1


# ---------- stabilize_regions ----------
def test_stabilize_merges_close_overlapping_spans() -> None:
    s1 = RegionSpan(BBox(0.4, 0.9, 0.2, 0.08), t_start=0.0, t_end=1.0)
    s2 = RegionSpan(BBox(0.42, 0.91, 0.2, 0.08), t_start=1.5, t_end=2.0)  # 间隔0.5s
    merged = stabilize_regions([s1, s2], fps=10.0)
    assert len(merged) == 1
    assert merged[0].t_start == 0.0 and merged[0].t_end == 2.0


def test_stabilize_keeps_far_or_distinct_spans() -> None:
    a = RegionSpan(BBox(0.4, 0.9, 0.2, 0.08), t_start=0.0, t_end=1.0)
    far_time = RegionSpan(BBox(0.4, 0.9, 0.2, 0.08), t_start=5.0, t_end=6.0)
    distinct = RegionSpan(BBox(0.0, 0.0, 0.2, 0.2), t_start=1.2, t_end=1.5)
    out = stabilize_regions([a, distinct, far_time], fps=10.0)
    assert len(out) == 3


def test_stabilize_empty() -> None:
    assert stabilize_regions([], 24.0) == []



