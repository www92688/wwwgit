# 推理后处理纯函数（详设 9.4，重点单测对象；无任何 IO/依赖注入）
from __future__ import annotations

from dataclasses import dataclass

from ych.common.schemas import BBox
from ych.services.s2_ai.provider import Detection


@dataclass
class RegionSpan:
    """时域稳定的检测区域（M2 帧级修复只处理激活时段）。"""

    bbox: BBox
    t_start: float
    t_end: float


def iou(a: BBox, b: BBox) -> float:
    """归一化坐标交并比。"""
    x1 = max(a.x, b.x)
    y1 = max(a.y, b.y)
    x2 = min(a.x + a.w, b.x + b.w)
    y2 = min(a.y + a.h, b.y + b.h)
    inter_w = max(0.0, x2 - x1)
    inter_h = max(0.0, y2 - y1)
    inter = inter_w * inter_h
    union = a.w * a.h + b.w * b.h - inter
    return inter / union if union > 0 else 0.0


def nms(dets: list[Detection], iou_thr: float) -> list[Detection]:
    """按置信度的非极大值抑制。"""
    ordered = sorted(dets, key=lambda d: d.confidence, reverse=True)
    kept: list[Detection] = []
    for det in ordered:
        if all(iou(det.bbox, k.bbox) <= iou_thr for k in kept):
            kept.append(det)
    return kept


def merge_boxes(dets: list[Detection], iou_thr: float) -> list[Detection]:
    """重叠框合并（IoU>thr 视为同一组，取组内最大外接框与最高置信度）。"""
    remaining = list(dets)
    merged: list[Detection] = []
    while remaining:
        first = remaining.pop(0)
        group = [first]
        changed = True
        while changed:
            changed = False
            for other in list(remaining):
                if any(iou(g.bbox, other.bbox) > iou_thr for g in group):
                    group.append(other)
                    remaining.remove(other)
                    changed = True
        x1 = min(d.bbox.x for d in group)
        y1 = min(d.bbox.y for d in group)
        x2 = max(d.bbox.x + d.bbox.w for d in group)
        y2 = max(d.bbox.y + d.bbox.h for d in group)
        best = max(group, key=lambda d: d.confidence)
        merged.append(Detection(
            bbox=BBox(x=x1, y=y1, w=x2 - x1, h=y2 - y1),
            confidence=best.confidence,
            label=best.label,
            ts=best.ts,
        ))
    return merged


def expand_bbox(b: BBox, margin: float) -> BBox:
    """按比例外扩并 clamp 到 [0,1]。"""
    new_w = b.w * (1 + 2 * margin)
    new_h = b.h * (1 + 2 * margin)
    new_x = b.x - b.w * margin
    new_y = b.y - b.h * margin
    # clamp：右/下越界时收缩尺寸
    if new_x < 0:
        new_w += new_x
        new_x = 0
    if new_y < 0:
        new_h += new_y
        new_y = 0
    if new_x + new_w > 1:
        new_w = 1 - new_x
    if new_y + new_h > 1:
        new_h = 1 - new_y
    return BBox(x=new_x, y=new_y, w=max(0.0, new_w), h=max(0.0, new_h))


def temporal_cluster(
    per_frame_dets: list[list[Detection]],
    frame_ts: list[float],
    iou_link: float = 0.5,
    presence_ratio: float = 0.6,
) -> list[RegionSpan]:
    """时域聚类（详设 9.4）。

    跨帧 IoU>iou_link 链接成簇；簇在其活跃时间段内的出现率 ≥presence_ratio
    才认定为静态区域 → RegionSpan(bbox, t_start, t_end)。
    """
    n = len(per_frame_dets)
    if n == 0 or len(frame_ts) != n:
        return []

    @dataclass
    class _Cluster:
        bbox: BBox
        frames: set[int]

    clusters: list[_Cluster] = []

    for fi, dets in enumerate(per_frame_dets):
        for det in dets:
            best_ci = -1
            best_iou = 0.0
            for ci, cl in enumerate(clusters):
                ov = iou(det.bbox, cl.bbox)
                if ov > iou_link and ov > best_iou:
                    best_ci, best_iou = ci, ov
            if best_ci >= 0:
                clusters[best_ci].frames.add(fi)
            else:
                clusters.append(_Cluster(bbox=det.bbox, frames={fi}))

    spans: list[RegionSpan] = []
    for cl in clusters:
        idxs = sorted(cl.frames)
        t_start = frame_ts[idxs[0]]
        t_end = frame_ts[idxs[-1]]
        span_frames = sum(
            1 for fi in range(n) if t_start <= frame_ts[fi] <= t_end
        )
        presence = len(idxs) / span_frames if span_frames else 0.0
        if presence >= presence_ratio:
            spans.append(RegionSpan(bbox=cl.bbox, t_start=t_start, t_end=t_end))
    spans.sort(key=lambda s: s.t_start)
    return spans


def stabilize_regions(spans: list[RegionSpan], fps: float) -> list[RegionSpan]:
    """相邻 RegionSpan 时间间隔 <2s 且 IoU>0.5 → 合并延展，消除闪烁断档。"""
    if not spans:
        return []
    gap_s = 2.0 * max(fps, 1e-6) / max(fps, 1e-6)   # 间隔阈值恒为 2 秒
    result = [spans[0]]
    for cur in spans[1:]:
        last = result[-1]
        gap = cur.t_start - last.t_end
        if gap < 2.0 and iou(last.bbox, cur.bbox) > 0.5:
            x1 = min(last.bbox.x, cur.bbox.x)
            y1 = min(last.bbox.y, cur.bbox.y)
            x2 = max(last.bbox.x + last.bbox.w, cur.bbox.x + cur.bbox.w)
            y2 = max(last.bbox.y + last.bbox.h, cur.bbox.y + cur.bbox.h)
            result[-1] = RegionSpan(
                bbox=BBox(x=x1, y=y1, w=x2 - x1, h=y2 - y1),
                t_start=last.t_start,
                t_end=cur.t_end,
            )
        else:
            result.append(cur)
    _ = gap_s   # 语义标注：间隔阈值为固定 2 秒（与 fps 无关）
    return result

