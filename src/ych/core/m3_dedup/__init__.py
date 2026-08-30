# 智能去重模块（M3）公共出口
from __future__ import annotations

from ych.core.m3_dedup.candidate_cache import CandidateCache, user_data_dir
from ych.core.m3_dedup.dedup_pipeline import DedupItemResult, DedupPipeline
from ych.core.m3_dedup.feature_extractor import (
    FEATURE_VERSION,
    FeatureExtractService,
)
from ych.core.m3_dedup.scheme_manager import PRESETS, SchemeManager
from ych.core.m3_dedup.similarity import (
    DEFAULT_WEIGHTS,
    ReportBuilder,
    SimilarityCalculator,
    count_over_threshold,
    recommend_level,
)

__all__ = [
    "DEFAULT_WEIGHTS",
    "FEATURE_VERSION",
    "PRESETS",
    "CandidateCache",
    "DedupItemResult",
    "DedupPipeline",
    "FeatureExtractService",
    "ReportBuilder",
    "SchemeManager",
    "SimilarityCalculator",
    "count_over_threshold",
    "recommend_level",
    "user_data_dir",
]
