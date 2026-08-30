# 手法注册表（详设 14.4.1）
from __future__ import annotations

from ych.core.m3_dedup.techniques.base import DedupTechnique


class TechniqueRegistry:
    """id 去重注册；ordered 按 zorder 升序（mirror10<crop20<color30<speed40<border50）。"""

    def __init__(self) -> None:
        self._items: dict[str, DedupTechnique] = {}

    def register(self, technique: DedupTechnique) -> None:
        if technique.id in self._items:
            raise ValueError(f"手法 id 重复注册：{technique.id}")
        self._items[technique.id] = technique

    def get(self, technique_id: str) -> DedupTechnique | None:
        return self._items.get(technique_id)

    def all(self) -> list[DedupTechnique]:
        return sorted(self._items.values(), key=lambda t: t.zorder)

    def ordered(self, ids: list[str]) -> list[DedupTechnique]:
        """给定 id 子集，按 zorder 排序；未知 id 抛错。"""
        out = []
        for tid in ids:
            t = self._items.get(tid)
            if t is None:
                raise KeyError(f"未注册的手法：{tid}")
            out.append(t)
        return sorted(out, key=lambda t: t.zorder)


def make_default_registry() -> TechniqueRegistry:
    """app 启动注册全部内置手法（详设 ALL 列表）。"""
    from ych.core.m3_dedup.techniques.border import BorderTechnique
    from ych.core.m3_dedup.techniques.color_filter import ColorFilterTechnique
    from ych.core.m3_dedup.techniques.crop_scale import CropScaleTechnique
    from ych.core.m3_dedup.techniques.mirror import MirrorTechnique
    from ych.core.m3_dedup.techniques.speed import SpeedTechnique

    registry = TechniqueRegistry()
    for cls in (MirrorTechnique, CropScaleTechnique, ColorFilterTechnique,
                SpeedTechnique, BorderTechnique):
        registry.register(cls())
    return registry
