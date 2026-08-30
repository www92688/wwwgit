# crop_scale 裁切缩放手法 z=20（详设 14.4.2）
# 关键点：crop 后显式 scale 回原始 W:H（滤镜内 iw/ih 已是裁后尺寸，写 iw:ih 是恒等）
from __future__ import annotations

from typing import ClassVar

from ych.common.schemas import MediaInfo
from ych.core.m3_dedup.techniques.base import (
    ClipContext,
    DedupTechnique,
    ParamField,
    as_float,
)


def even_down(n: float) -> int:
    v = max(2, int(n))
    return v - (v % 2)


class CropScaleTechnique(DedupTechnique):
    id = "crop_scale"
    display_name_zh = "裁切缩放"
    zorder = 20
    param_schema: ClassVar[list[ParamField]] = [
        ParamField(key="mode", label_zh="模式", type="enum",
                   default="crop", choices=["crop", "scale"]),
        ParamField(key="margin_pct", label_zh="裁切比例", type="float",
                   default=0.08, range=(0.02, 0.25)),
    ]

    def apply(self, ctx: ClipContext, params: dict[str, object]) -> ClipContext:
        p = self.validate_params(params)
        probe: MediaInfo = ctx.probe   # type: ignore[assignment]
        w, h = probe.width, probe.height
        m = as_float(p["margin_pct"], 0.08)
        if str(p["mode"]) == "crop":
            ow = even_down(w * (1 - 2 * m))
            oh = even_down(h * (1 - 2 * m))
            ctx.vf_filters.append(
                f"crop={ow}:{oh}:(iw-ow)/2:(ih-oh)/2,scale={w}:{h}"
            )
        else:
            sw = even_down(w * (1 + m))
            ctx.vf_filters.append(
                f"scale={sw}:-2,crop={w}:{h}:(iw-ow)/2:(ih-oh)/2"
            )
        return ctx
