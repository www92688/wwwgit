# 推理后处理纯函数（详设 9.4，重点单测对象；无任何 IO/依赖注入）
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ych.common.schemas import BBox
from ych.services.s2_ai.provider import Detection


@dataclass
class RegionSpan:
    """时域稳定的检测区域（M2 帧级修复只处理激活时段）。

    manual=True 表示来自用户手动框选：修复遵循用户意图（框内内容视为
    待清除，紧凑掩码失效时回退整框修复）；auto 检测区域以紧凑掩码为
    准，掩码为空的帧不贴片（避免误检糊块）。

    temporal_mask：静态区域的时间一致性笔画掩码（uint8, HxW，与 bbox
    像素矩形对齐、与视频同分辨率），由 M2 检测阶段跨采样帧累积证据
    生成；非 None 时帧级修复直接采用，不再逐帧求紧凑掩码。
    """

    bbox: BBox
    t_start: float
    t_end: float
    manual: bool = False
    temporal_mask: Any = None


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


def expand_bbox(b: BBox, margin: float, min_margin: float = 0.012) -> BBox:
    """按比例外扩并 clamp 到 [0,1]。

    每侧保底 min_margin：DBNet 文字核心框极薄（h 可低至 0.01），纯比例
    外扩近乎无效，字幕描边/水印边角会残留；0.012 ≈ 1080p 下 13px。
    """
    mx = max(b.w * margin, min_margin)
    my = max(b.h * margin, min_margin)
    new_w = b.w + 2 * mx
    new_h = b.h + 2 * my
    new_x = b.x - mx
    new_y = b.y - my
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

    链接补一条中心规则：细长条水印（如横贯画面的半透明 ID）的检测框
    逐帧抖动会让 IoU 掉到阈值以下（窄条上轻微纵向偏移对 IoU 极其敏
    感，0.47<0.5 即碎裂），碎簇全被出现率过滤 → 该水印永远到不了修
    复环节。检测中心落入簇框（含少量外扩）同样链接，且簇框随成员并
    集增长，抖动不再碎簇。
    """
    n = len(per_frame_dets)
    if n == 0 or len(frame_ts) != n:
        return []

    center_margin = 0.02

    @dataclass
    class _Cluster:
        bbox: BBox
        frames: set[int]

    def _contains(cl_bbox: BBox, cx: float, cy: float) -> bool:
        m = center_margin
        return (cl_bbox.x - m <= cx <= cl_bbox.x + cl_bbox.w + m
                and cl_bbox.y - m <= cy <= cl_bbox.y + cl_bbox.h + m)

    clusters: list[_Cluster] = []

    for fi, dets in enumerate(per_frame_dets):
        for det in dets:
            cx = det.bbox.x + det.bbox.w / 2
            cy = det.bbox.y + det.bbox.h / 2
            best_ci = -1
            best_iou = 0.0
            fallback_ci = -1
            for ci, cl in enumerate(clusters):
                ov = iou(det.bbox, cl.bbox)
                if ov > best_iou:
                    best_iou, best_ci = ov, ci
                if fallback_ci < 0 and _contains(cl.bbox, cx, cy):
                    fallback_ci = ci
            if best_iou <= iou_link:
                best_ci = fallback_ci   # IoU 不达标时退回中心规则
            if best_ci >= 0:
                cl = clusters[best_ci]
                x1, y1 = min(cl.bbox.x, det.bbox.x), min(cl.bbox.y, det.bbox.y)
                x2 = max(cl.bbox.x + cl.bbox.w, det.bbox.x + det.bbox.w)
                y2 = max(cl.bbox.y + cl.bbox.h, det.bbox.y + det.bbox.h)
                cl.bbox = BBox(x=x1, y=y1, w=x2 - x1, h=y2 - y1)
                cl.frames.add(fi)
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

