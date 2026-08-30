# 区域时域稳定（详设 13.2 RegionTemporal；9.4 的 M2 侧薄封装 + 单测宿主）
from __future__ import annotations

from ych.common.schemas import ManualRegions
from ych.services.s2_ai.postprocess import (
    RegionSpan,
    expand_bbox,
    stabilize_regions,
    temporal_cluster,
)
from ych.services.s2_ai.provider import Detection

# 检出框外扩比例：避免修复边缘残留水印描边
MARGIN_RATIO = 0.08


class RegionTemporal:
    """检测框 → 时域聚类 → 相邻合并延展，产出修复用 RegionSpan 列表。"""

    @staticmethod
    def build_spans(
        per_frame_dets: list[list[Detection]],
        frame_ts: list[float],
        fps: float,
        margin: float = MARGIN_RATIO,
    ) -> list[RegionSpan]:
        spans = temporal_cluster(per_frame_dets, frame_ts)
        stabilized = stabilize_regions(spans, fps)
        return [
            RegionSpan(bbox=expand_bbox(s.bbox, margin),
                       t_start=s.t_start, t_end=s.t_end)
            for s in stabilized
        ]


def manual_spans(
    regions: ManualRegions | None,
    duration_s: float,
) -> list[RegionSpan]:
    """手动框选 → 全程（或指定时段）RegionSpan，不经聚类。"""
    if regions is None or not regions.rects:
        return []
    t0 = max(0.0, regions.time_start_s)
    t1 = duration_s if regions.time_end_s < 0 else min(regions.time_end_s, duration_s)
    return [RegionSpan(bbox=rect, t_start=t0, t_end=t1) for rect in regions.rects]
