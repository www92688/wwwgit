# 相似度计算与对比报告（详设 14.2，公式逐条落地；重点单测对象）
from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

import numpy as np
import numpy.typing as npt

from ych.common.errors import ERR_AI_INVALID_INPUT, AppError
from ych.common.schemas import CompareReport, CompareTarget, DimScores, FeatureSet

logger = logging.getLogger("ych.m3")

DEFAULT_WEIGHTS: tuple[float, float, float] = (0.5, 0.25, 0.25)
_HEAVY_THRESHOLD = 0.80


def _cosine_matrix(a: npt.NDArray[np.float32],
                   b: npt.NDArray[np.float32]) -> npt.NDArray[np.float32]:
    """行向量余弦相似度矩阵（输入应已 L2 归一化，此处防御性再归一）。"""
    a_n = np.linalg.norm(a, axis=1, keepdims=True)
    b_n = np.linalg.norm(b, axis=1, keepdims=True)
    a_u = a / np.maximum(a_n, 1e-12)
    b_u = b / np.maximum(b_n, 1e-12)
    sim: npt.NDArray[np.float32] = np.asarray(a_u @ b_u.T, dtype=np.float32)
    return sim


def composition_similarity(
    comp_a: npt.NDArray[np.float32],
    comp_b: npt.NDArray[np.float32],
) -> float:
    """双向 Chamfer 最大匹配余弦（14.2）；任一方无镜头记 0。"""
    if len(comp_a) == 0 or len(comp_b) == 0:
        return 0.0
    sim = _cosine_matrix(comp_a, comp_b)
    forward = float(sim.max(axis=1).mean())     # A 每行对 B 最佳匹配
    backward = float(sim.max(axis=0).mean())    # B 每列对 A 最佳匹配
    return 0.5 * (forward + backward)


def motion_similarity(curve_a: npt.NDArray[np.float32],
                      curve_b: npt.NDArray[np.float32]) -> float:
    """S_motion = 1 − ½·mean|Δ|（曲线元素域 [-1,1]，L1 均值上限 2）。"""
    diff = np.abs(np.asarray(curve_a, dtype=np.float64)
                  - np.asarray(curve_b, dtype=np.float64))
    return 1.0 - 0.5 * float(diff.mean())


def rhythm_similarity(hist_a: npt.NDArray[np.float32],
                      hist_b: npt.NDArray[np.float32],
                      rate_a: npt.NDArray[np.float32],
                      rate_b: npt.NDArray[np.float32]) -> float:
    """S_rhythm = 0.5·直方图交 + 0.5·(1−mean|Δcut_rate|)。"""
    inter = float(np.minimum(
        np.asarray(hist_a, dtype=np.float64),
        np.asarray(hist_b, dtype=np.float64),
    ).sum())
    s_hist = min(max(inter, 0.0), 1.0)
    rate_diff = np.abs(np.asarray(rate_a, dtype=np.float64)
                       - np.asarray(rate_b, dtype=np.float64))
    s_rate = 1.0 - min(float(rate_diff.mean()), 1.0)
    return 0.5 * s_hist + 0.5 * s_rate


class SimilarityCalculator:
    """三分量加权综合重复度（公式见详设 14.2）。"""

    def compare(
        self,
        a: FeatureSet,
        b: FeatureSet,
        weights: tuple[float, float, float] = DEFAULT_WEIGHTS,
    ) -> DimScores:
        if a.version != b.version:
            raise AppError(ERR_AI_INVALID_INPUT,
                           f"特征版本不兼容：{a.version} vs {b.version}")
        s_comp = composition_similarity(a.composition, b.composition)
        s_motion = motion_similarity(a.motion_curve, b.motion_curve)
        s_rhythm = rhythm_similarity(
            a.rhythm_hist, b.rhythm_hist, a.cut_rate_curve, b.cut_rate_curve,
        )
        w_c, w_m, w_r = weights
        overall = float(np.clip(w_c * s_comp + w_m * s_motion + w_r * s_rhythm,
                                0.0, 1.0))
        return DimScores(
            composition=min(max(s_comp, 0.0), 1.0),
            motion=min(max(s_motion, 0.0), 1.0),
            rhythm=min(max(s_rhythm, 0.0), 1.0),
            overall=float(overall),
        )


class _ReportDaoLike(Protocol):
    def add(self, src: Path, report: CompareReport) -> int: ...


class ReportBuilder:
    """overall=max(targets)、targets 降序、落库 ReportDao.add。"""

    def __init__(self, reports: _ReportDaoLike) -> None:
        self._reports = reports

    def build(
        self,
        src: Path,
        targets: list[CompareTarget],
        weights: tuple[float, float, float] = DEFAULT_WEIGHTS,
        unavailable_platforms: list[str] | None = None,
    ) -> CompareReport:
        scored = [
            t for t in targets if t.scores is not None and t.status == "ok"
        ]
        scored.sort(key=lambda t: t.scores.overall if t.scores else 0.0,
                    reverse=True)
        overall = max(
            (t.scores.overall if t.scores else 0.0 for t in scored),
            default=0.0,
        )
        best_dims = scored[0].scores if scored and scored[0].scores else DimScores(
            composition=0.0, motion=0.0, rhythm=0.0, overall=0.0,
        )
        ordered = sorted(
            targets,
            key=lambda t: t.scores.overall if t.scores else -1.0,
            reverse=True,
        )
        report = CompareReport(
            src_path=str(src),
            overall_score=round(overall * 100.0, 1),
            dims=best_dims,
            weights=(weights[0], weights[1], weights[2]),
            targets=ordered,
            unavailable_platforms=list(unavailable_platforms or []),
        )
        self._reports.add(src, report)
        logger.info("对比报告：%s overall=%.1f%%（targets=%d）",
                    src.name, report.overall_score, len(scored))
        return report


def count_over_threshold(report: CompareReport,
                         threshold: float = _HEAVY_THRESHOLD) -> int:
    """"与 N 个视频相似度超过 80% 的 N"。threshold 以 [0,1] 计。"""
    return sum(
        1 for t in report.targets
        if t.scores is not None and t.status == "ok"
        and t.scores.overall >= threshold
    )


def recommend_level(score: float) -> str:
    """档位推荐边界：<0.50 轻度 / 0.50~0.80 中度 / >0.80 重度。"""
    if score < 0.50:
        return "light"
    if score <= 0.80:
        return "mid"
    return "heavy"
