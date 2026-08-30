# speed 变速手法 z=40（详设 14.4.2）
# 不直接追加 -vf；记录 speed_factor，由流水线注入 setpts/atempo
from __future__ import annotations

from typing import ClassVar

from ych.core.m3_dedup.techniques.base import ClipContext, DedupTechnique, ParamField, as_float


class SpeedTechnique(DedupTechnique):
    id = "speed"
    display_name_zh = "变速"
    zorder = 40
    param_schema: ClassVar[list[ParamField]] = [
        ParamField(key="factor", label_zh="倍速", type="float",
                   default=1.0, range=(0.75, 1.25)),
        ParamField(key="scope", label_zh="作用范围", type="enum",
                   default="global", choices=["global"]),
    ]

    def apply(self, ctx: ClipContext, params: dict[str, object]) -> ClipContext:
        p = self.validate_params(params)
        f = as_float(p["factor"], 1.0)
        if abs(f - 1.0) > 1e-6:
            # 连乘叠加（理论上仅一个 speed 手法；防御性支持多次）
            ctx.speed_factor *= f
        return ctx
