# 策略管理（详设 14.5）：三档预设 + 区间随机实例化 + 自定义方案持久化
from __future__ import annotations

import random
from dataclasses import dataclass, field

from ych.core.m3_dedup.techniques.registry import TechniqueRegistry
from ych.services.s3_db.daos import SchemeDao


@dataclass
class PresetDef:
    label: str
    score_range: tuple[float, float]
    techniques: list[tuple[str, dict[str, tuple[float, float]]]] = field(
        default_factory=list,
    )


PRESETS: dict[str, PresetDef] = {
    "light": PresetDef("轻度去重", (0.0, 0.5), [
        ("crop_scale", {"margin_pct": (0.04, 0.06)}),
        ("color_filter", {"brightness": (-0.05, 0.05)}),
    ]),
    "mid": PresetDef("中度去重", (0.5, 0.8), [
        ("mirror", {}),
        ("crop_scale", {"margin_pct": (0.08, 0.12)}),
        ("color_filter", {"contrast": (1.05, 1.15)}),
    ]),
    "heavy": PresetDef("重度去重", (0.8, 1.01), [
        ("mirror", {}),
        ("crop_scale", {"margin_pct": (0.12, 0.18)}),
        ("color_filter", {"saturation": (0.85, 1.35)}),
        ("speed", {"factor": (0.92, 1.10)}),
        ("border", {"width_pct": (0.02, 0.04)}),
    ]),
}

# 随机化精度：与 ffmpeg 参数可读性对齐
_DECIMALS = {"margin_pct": 3, "brightness": 3, "contrast": 3,
             "saturation": 3, "temperature": 2, "factor": 3,
             "width_pct": 3}


class SchemeManager:
    """recommend / instantiate / save_custom / load_custom / preset_to_custom。"""

    def __init__(self, registry: TechniqueRegistry, schemes: SchemeDao | None) -> None:
        self._registry = registry
        self._schemes = schemes

    # ---- 推荐 ----
    @staticmethod
    def recommend(score: float) -> str:
        """<0.50 轻度；0.50~0.80 中度（含端点）；>0.80 重度。"""
        if score < 0.50:
            return "light"
        if score <= 0.80:
            return "mid"
        return "heavy"

    # ---- 实例化 ----
    def instantiate(self, preset_id: str, seed: int | None = None) -> list[dict[str, object]]:
        """区间均匀随机取参（seed 可复现）；未指定参数走手法默认值。"""
        preset = PRESETS.get(preset_id)
        if preset is None:
            raise KeyError(f"未知预设：{preset_id}")
        rng = random.Random(seed)
        out: list[dict[str, object]] = []
        for tid, param_ranges in preset.techniques:
            technique = self._registry.get(tid)
            assert technique is not None
            params: dict[str, object] = {}
            for key, (lo, hi) in param_ranges.items():
                val = round(rng.uniform(lo, hi), _DECIMALS.get(key, 3))
                params[key] = val
            out.append({
                "id": tid,
                "params": technique.validate_params(params),
            })
        return out

    # ---- 自定义方案（SchemeDao 薄封装）----
    def save_custom(self, name: str, config: list[dict[str, object]]) -> int:
        assert self._schemes is not None, "SchemeDao 未注入"
        return self._schemes.save(name, config)

    def load_custom(self, name: str) -> list[dict[str, object]] | None:
        if self._schemes is None:
            return None
        config = self._schemes.load(name)
        if config is None:
            return None
        return [self._normalize(item) for item in config]

    def preset_to_custom(self, preset_id: str, seed: int | None = None) -> list[dict[str, object]]:
        """预设 → 自定义（支撑"先用预设生成版本，再切换自定义微调"）。"""
        return self.instantiate(preset_id, seed)

    # ---- 内部 ----
    def _normalize(self, item: object) -> dict[str, object]:
        assert isinstance(item, dict)
        tid = str(item.get("id"))
        technique = self._registry.get(tid)
        raw_params = item.get("params") or {}
        assert isinstance(raw_params, dict)
        params = (
            technique.validate_params(raw_params) if technique else raw_params
        )
        return {"id": tid, "params": params}
