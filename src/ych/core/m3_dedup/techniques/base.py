# 去重手法抽象与上下文（详设 14.4.1）
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Literal

from ych.common.schemas import MediaInfo

ParamType = Literal["int", "float", "enum", "bool", "color"]


@dataclass
class ParamField:
    key: str
    label_zh: str
    type: ParamType
    default: object
    range: tuple[float, float] | None = None
    choices: list[str] | None = None


ParamSchema = list[ParamField]


def as_float(v: object, fallback: float = 0.0) -> float:
    """宽松取浮点：非数值回退。"""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    return float(fallback)


@dataclass
class ClipContext:
    """单条素材的处理上下文：滤镜链累积器。"""

    src: Path | None = None
    probe: MediaInfo | None = None
    vf_filters: list[str] = field(default_factory=list)   # 累积 ffmpeg 滤镜
    speed_factor: float = 1.0                             # 变速特殊标记
    keep_audio: bool = True


class DedupTechnique(ABC):
    """手法契约：zorder 决定执行顺序 mirror10<crop20<color30<speed40<border50。"""

    id: str = ""
    display_name_zh: str = ""
    zorder: int = 0
    param_schema: ClassVar[ParamSchema] = []

    def validate_params(self, params: dict[str, object]) -> dict[str, object]:
        """缺省补全 + 数值 clamp + 枚举白名单（通用实现）。"""
        out: dict[str, object] = {}
        for f in self.param_schema:
            v = params.get(f.key, f.default)
            if f.type == "float":
                fv = as_float(v, as_float(f.default))
                if f.range is not None:
                    fv = min(max(fv, f.range[0]), f.range[1])
                out[f.key] = fv
            elif f.type == "int":
                iv = as_float(v, as_float(f.default))
                if f.range is not None:
                    iv = min(max(iv, f.range[0]), f.range[1])
                out[f.key] = int(iv)
            elif f.type == "enum":
                sv = str(v)
                out[f.key] = sv if f.choices and sv in f.choices else str(f.default)
            elif f.type == "bool":
                out[f.key] = bool(v)
            else:   # color 等：原样字符串
                out[f.key] = str(v)
        unknown = set(params) - {f.key for f in self.param_schema}
        if unknown:
            raise ValueError(f"{self.id} 不支持的参数：{sorted(unknown)}")
        return out

    @abstractmethod
    def apply(self, ctx: ClipContext, params: dict[str, object]) -> ClipContext:
        """校验后的参数 → 追加滤镜/修改上下文。"""
