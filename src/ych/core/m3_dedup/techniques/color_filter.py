# color_filter 调色手法 z=30（详设 14.4.2）
# preset（lut3d 内置 .cube）与数值参数互斥：选 preset 时忽略数值
from __future__ import annotations

import logging
from typing import ClassVar

from ych.common.fsutil import bundle_data_root
from ych.core.m3_dedup.techniques.base import ClipContext, DedupTechnique, ParamField, as_float

logger = logging.getLogger("ych.m3")

# 仓库根（源码运行）或 _MEIPASS（打包）下的 runtime/luts
_LUT_DIR = bundle_data_root() / "runtime" / "luts"

_PRESET_FALLBACK = {
    # .cube 缺失时的近似退化（暖/冷偏色、胶片降饱和提对比）
    "warm": ["colorbalance=rm=0.15:gm=0.0:bm=-0.15"],
    "cool": ["colorbalance=rm=-0.15:gm=0.0:bm=0.15"],
    "film": ["eq=saturation=0.9:contrast=1.08"],
}


def _lut_filter_arg(lut: object) -> str:
    """lut3d 的 file= 参数值：Windows 盘符冒号必须按滤镜图语法转义
    （file=D:/x.cube 会被拆成两个选项导致整条滤镜链解析失败）。"""
    posix = str(lut).replace("\\", "/")
    return posix.replace(":", "\\\\:")


class ColorFilterTechnique(DedupTechnique):
    id = "color_filter"
    display_name_zh = "调色滤镜"
    zorder = 30
    param_schema: ClassVar[list[ParamField]] = [
        ParamField(key="brightness", label_zh="亮度", type="float",
                   default=0.05, range=(-0.2, 0.2)),
        ParamField(key="contrast", label_zh="对比度", type="float",
                   default=1.05, range=(0.8, 1.3)),
        ParamField(key="saturation", label_zh="饱和度", type="float",
                   default=1.1, range=(0.6, 1.6)),
        ParamField(key="temperature", label_zh="色温", type="float",
                   default=0.0, range=(-1.0, 1.0)),
        ParamField(key="preset", label_zh="预设风格", type="enum",
                   default="none", choices=["none", "warm", "cool", "film"]),
    ]

    def apply(self, ctx: ClipContext, params: dict[str, object]) -> ClipContext:
        p = self.validate_params(params)
        preset = str(p["preset"])
        if preset != "none":
            lut = _LUT_DIR / f"{preset}.cube"
            if lut.exists():
                ctx.vf_filters.append(f"lut3d=file={_lut_filter_arg(lut)}")
            else:
                logger.warning("LUT 缺失，%s 退化为 colorbalance 近似", lut)
                ctx.vf_filters.extend(_PRESET_FALLBACK.get(preset, []))
            return ctx

        b = as_float(p["brightness"])
        c = as_float(p["contrast"], 1.0)
        s = as_float(p["saturation"], 1.0)
        t = as_float(p["temperature"])
        ctx.vf_filters.append(
            f"eq=brightness={b:.3f}:contrast={c:.3f}:saturation={s:.3f}"
        )
        if abs(t) > 1e-6:
            ctx.vf_filters.append(f"colorbalance=rm={t:.3f}:gm=0:bm={-t:.3f}")
        return ctx
