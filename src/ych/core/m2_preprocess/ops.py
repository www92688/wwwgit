# 预处理处理项（Ops）与路径判定（详设 13.1；性能预算 ≤2×时长）
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

from ych.common.schemas import BBox, ManualRegions, TaskPayload

WmMode = Literal["off", "auto", "manual"]
SubMode = Literal["off", "auto", "manual"]
SubtitleRoute = Literal["soft", "hard", "none"]


@dataclass
class PreprocessOps:
    """五处理项：去水印/去字幕（三态）、裁剪、比例、去原声。"""

    remove_watermark_mode: WmMode = "off"
    watermark_regions: ManualRegions | None = None       # manual 时有效
    remove_subtitle_mode: SubMode = "off"
    subtitle_regions: ManualRegions | None = None
    crop_rect: BBox | None = None                        # 归一化裁剪框
    aspect_target: tuple[int, int] | None = None         # 如 (9, 16)
    aspect_strategy: Literal["crop", "pad"] = "crop"
    strip_audio: bool = False


def watermark_active(ops: PreprocessOps) -> bool:
    return ops.remove_watermark_mode != "off"


def need_frame_pass(ops: PreprocessOps, route: SubtitleRoute) -> bool:
    """帧级路径判定：去水印≠off 或 去字幕∈{auto硬, manual}（route=hard 已含 manual）。"""
    return watermark_active(ops) or route == "hard"


def need_transcode(ops: PreprocessOps, route: SubtitleRoute) -> bool:
    return (ops.crop_rect is not None or ops.aspect_target is not None
            or need_frame_pass(ops, route))


def is_only_soft_strip(ops: PreprocessOps, probe_has_soft: bool,
                       route: SubtitleRoute) -> bool:
    """仅软字幕剥离（其余全 off）→ -map 0:v -map 0:a? -sn -c copy 秒级完成。"""
    others_off = (
        not watermark_active(ops)
        and ops.remove_subtitle_mode == "auto"
        and ops.crop_rect is None
        and ops.aspect_target is None
        and not ops.strip_audio
    )
    return route == "soft" and probe_has_soft and others_off


def decide_path(ops: PreprocessOps, probe_has_soft: bool,
                route: SubtitleRoute) -> Literal["frame", "filter", "remux", "skip"]:
    """路径分派纯函数（可表驱动测试）。

    帧级 > 纯滤镜 > remux > 跳过。
    ⚠ 偏差修正（tasks/10-m2-preprocess.md）：详设 13.1 未覆盖"仅去原声"，
    此处以流复制 remux(-an) 承接，秒级完成且不破坏 ≤2×时长预算。
    """
    if need_frame_pass(ops, route):
        return "frame"
    if ops.crop_rect is not None or ops.aspect_target is not None:
        return "filter"
    if route == "soft" or ops.strip_audio:
        return "remux"
    return "skip"


# ---- payload 组装与还原（13.3）----

def make_preprocess_payload(items: list[tuple[str, PreprocessOps]]) -> TaskPayload:
    return TaskPayload(type="preprocess", data={
        "items": [{"src": src, "ops": asdict(ops)} for src, ops in items],
    })


def _revive_regions(raw: object) -> ManualRegions | None:
    if not isinstance(raw, dict):
        return None
    rects = [BBox(**b) for b in raw.get("rects") or [] if isinstance(b, dict)]
    ts_raw = raw.get("time_start_s")
    te_raw = raw.get("time_end_s")
    return ManualRegions(
        rects=rects,
        time_start_s=float(ts_raw) if isinstance(ts_raw, (int, float)) else 0.0,
        time_end_s=float(te_raw) if isinstance(te_raw, (int, float)) else -1.0,
    )


def _revive_bbox(v: object) -> BBox | None:
    if isinstance(v, BBox):
        return v
    if isinstance(v, dict):
        return BBox(**v)
    return None


def revive_ops(raw: dict[str, object]) -> PreprocessOps:
    """JSON 反序列化 → PreprocessOps（嵌套 BBox/ManualRegions/tuple 还原）。"""
    wm = _revive_regions(raw.get("watermark_regions"))
    sub = _revive_regions(raw.get("subtitle_regions"))
    at = raw.get("aspect_target")
    aspect: tuple[int, int] | None = None
    if isinstance(at, (list, tuple)) and len(at) == 2:
        aspect = (int(at[0]), int(at[1]))
    wm_mode = raw.get("remove_watermark_mode", "off")
    sub_mode = raw.get("remove_subtitle_mode", "off")
    strategy = raw.get("aspect_strategy", "crop")
    return PreprocessOps(
        remove_watermark_mode=wm_mode,      # type: ignore[arg-type]
        watermark_regions=wm,
        remove_subtitle_mode=sub_mode,      # type: ignore[arg-type]
        subtitle_regions=sub,
        crop_rect=_revive_bbox(raw.get("crop_rect")),
        aspect_target=aspect,
        aspect_strategy=strategy,           # type: ignore[arg-type]
        strip_audio=bool(raw.get("strip_audio", False)),
    )
