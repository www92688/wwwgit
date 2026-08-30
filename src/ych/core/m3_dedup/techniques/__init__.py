# 去重手法引擎（M3.5）公共出口
from __future__ import annotations

from ych.core.m3_dedup.techniques.base import (
    ClipContext,
    DedupTechnique,
    ParamField,
    ParamSchema,
)
from ych.core.m3_dedup.techniques.registry import TechniqueRegistry, make_default_registry

__all__ = [
    "ClipContext",
    "DedupTechnique",
    "ParamField",
    "ParamSchema",
    "TechniqueRegistry",
    "make_default_registry",
]
