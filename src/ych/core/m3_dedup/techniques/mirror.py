# mirror 镜像手法 z=10（详设 14.4.2）
from __future__ import annotations

from typing import ClassVar

from ych.core.m3_dedup.techniques.base import ClipContext, DedupTechnique, ParamField


class MirrorTechnique(DedupTechnique):
    id = "mirror"
    display_name_zh = "画面镜像"
    zorder = 10
    param_schema: ClassVar[list[ParamField]] = [
        ParamField(key="axis", label_zh="镜像方向", type="enum",
                   default="horizontal",
                   choices=["horizontal", "vertical", "both"]),
    ]
    _FILTERS: ClassVar[dict[str, list[str]]] = {
        "horizontal": ["hflip"],
        "vertical": ["vflip"],
        "both": ["hflip", "vflip"],
    }

    def apply(self, ctx: ClipContext, params: dict[str, object]) -> ClipContext:
        p = self.validate_params(params)
        axis = str(p["axis"])
        assert axis in self._FILTERS
        ctx.vf_filters.extend(self._FILTERS[axis])
        return ctx
