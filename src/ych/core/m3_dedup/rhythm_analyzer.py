# 剪辑节奏特征（详设 14.1.4）：镜头时长 log2 直方图 + 滑窗切换率曲线
from __future__ import annotations

import numpy as np
import numpy.typing as npt

_HIST_DIM = 16              # 10 档 + 右侧补零至 16（预留）
_BIN_EDGES = [0.0, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0, 128.0]
_CURVE_LEN = 32
_WINDOW_S = 5.0


class RhythmAnalyzer:
    """boundaries/duration → (rhythm_hist(16,), cut_rate_curve(32,))。"""

    def compute(
        self,
        boundaries: list[float],
        duration_s: float,
    ) -> tuple[npt.NDArray[np.float32], npt.NDArray[np.float32]]:
        return (
            self.rhythm_hist(boundaries, duration_s),
            self.cut_rate_curve(boundaries, duration_s),
        )

    @staticmethod
    def rhythm_hist(
        boundaries: list[float], duration_s: float,
    ) -> npt.NDArray[np.float32]:
        """log2 分箱概率分布；无镜头时均匀零向量。"""
        edges = [0.0, *boundaries, float(duration_s)]
        lens = np.array([
            max(edges[k + 1] - edges[k], 1e-6)
            for k in range(len(edges) - 1)
        ])
        if len(lens) == 0 or duration_s <= 0:
            return np.zeros(_HIST_DIM, dtype=np.float32)
        logs = np.log2(lens)
        counts, _ = np.histogram(
            logs, bins=[np.log2(e) if e > 0 else -20.0 for e in _BIN_EDGES]
            + [np.inf],
        )
        total = float(counts.sum())
        hist = counts / total if total > 0 else counts.astype(np.float64)
        out = np.zeros(_HIST_DIM, dtype=np.float32)
        out[:min(_HIST_DIM, len(hist))] = hist[:_HIST_DIM]
        return out

    @staticmethod
    def cut_rate_curve(
        boundaries: list[float], duration_s: float,
    ) -> npt.NDArray[np.float32]:
        """滑窗 w=5s 步长 w/6 → 窗内边界数/w → 插值 32 点 → 全局最大归一。"""
        bounds = np.asarray(sorted(boundaries), dtype=np.float64)
        step = _WINDOW_S / 6.0
        if duration_s <= step / 2:
            # 极短/无时长素材：窗口中心为空，直接给零曲线
            # （duration 可能被上游 clamp 成 1e-6，不能只判 <=0）
            return np.zeros(_CURVE_LEN, dtype=np.float32)
        centers = np.arange(step / 2, duration_s, step)
        rates: list[float] = []
        for c in centers:
            lo, hi = c - _WINDOW_S / 2, c + _WINDOW_S / 2
            in_win = ((bounds >= lo) & (bounds <= hi)).sum()
            rates.append(float(in_win) / _WINDOW_S)
        src_x = np.linspace(0.0, 1.0, len(rates))
        if len(rates) == 1:
            rates = rates * 2
            src_x = np.linspace(0.0, 1.0, 2)
        dst_x = np.linspace(0.0, 1.0, _CURVE_LEN)
        curve = np.interp(dst_x, src_x, np.asarray(rates))
        peak = float(curve.max())
        if peak > 0:
            curve = curve / peak
        return curve.astype(np.float32)
