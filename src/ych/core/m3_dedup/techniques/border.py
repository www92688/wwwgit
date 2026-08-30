# border 边框手法 z=50（详设 14.4.2）
# solid：pad 放大画幅；blur：split+gblur+overlay 用画面自身模糊垫底
from __future__ import annotations

from typing import ClassVar

from ych.common.schemas import MediaInfo
from ych.core.m3_dedup.techniques.base import (
    ClipContext,
    DedupTechnique,
    ParamField,
    as_float,
)


class BorderTechnique(DedupTechnique):
    id = "border"
    display_name_zh = "边框"
    zorder = 50
    param_schema: ClassVar[list[ParamField]] = [
        ParamField(key="width_pct", label_zh="边框宽度", type="float",
                   default=0.03, range=(0.02, 0.08)),
        ParamField(key="style", label_zh="样式", type="enum",
                   default="solid", choices=["solid", "blur"]),
        ParamField(key="color", label_zh="颜色", type="color",
                   default="black"),
    ]

    def apply(self, ctx: ClipContext, params: dict[str, object]) -> ClipContext:
        p = self.validate_params(params)
        probe: MediaInfo = ctx.probe   # type: ignore[assignment]
        b = max(2, round(probe.width * as_float(p["width_pct"], 0.03) / 2) * 2)
        if str(p["style"]) == "solid":
            color = str(p["color"])
            ctx.vf_filters.append(f"pad=iw+{2 * b}:ih+{2 * b}:{b}:{b}:{color}")
        else:
            ctx.vf_filters.append(
                f"split[a][b];[b]scale=iw+{2 * b}:ih+{2 * b},gblur=sigma=20[bg];"
                f"[bg][a]overlay={b}:{b}"
            )
        return ctx
