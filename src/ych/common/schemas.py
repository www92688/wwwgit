# 公共数据结构（详设 4.1 全量落地）
# ⚠ 偏差修正（tasks/01-common.md）：TaskState 采用七态——含 canceled，
#   与 11.2 状态机一致；详设 4.1 的六态定义与之矛盾，此处以七态为准。
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import numpy as np
import numpy.typing as npt

WatermarkTag = Literal["yes", "no", "unknown"]
# 七态状态机：pending/running/success/failed/skipped/interrupted/canceled
TaskState = Literal[
    "pending",
    "running",
    "success",
    "failed",
    "skipped",
    "interrupted",
    "canceled",
]
Region = Literal["cn", "global"]

# 支持导入的视频扩展名白名单（需求 4.4）
SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(
    {".mp4", ".avi", ".mov", ".mkv", ".flv"}
)


@dataclass
class BBox:
    """归一化坐标框，原点左上，取值 0~1。"""

    x: float
    y: float
    w: float
    h: float


@dataclass
class VideoMeta:
    """平台插件搜索结果的标准结构（概要设计 5.1 的落地）。"""

    plugin_id: str                 # 如 "pexels"
    video_key: str                 # 平台内唯一 id
    title: str = ""
    page_url: str = ""
    duration_s: float = 0.0
    width: int = 0
    height: int = 0
    file_size_bytes: int | None = None    # 平台未提供时为 None，筛选按"不限"
    watermark_tag: WatermarkTag = "unknown"
    download_url: str = ""        # 选定清晰度的直链
    thumbnail_url: str = ""
    extra: dict[str, object] = field(default_factory=dict)


@dataclass
class SearchFilters:
    """搜索结果筛选条件（时长/画质/大小/水印）。"""

    duration_min_s: float = 0.0
    duration_max_s: float = 36000.0
    min_height: int = 0            # 0=原始画质不限；720/1080=要求≥该高度
    size_min_mb: float = 0.0
    size_max_mb: float = 1e9
    watermark: WatermarkTag = "unknown"   # unknown 即"不限制"


@dataclass
class ResumeState:
    """断点续传状态（S4 download_stream 产物，由调用方持久化）。"""

    downloaded_bytes: int = 0
    etag: str = ""
    total_bytes: int = 0
    temp_path: str = ""


@dataclass
class MediaInfo:
    """ffprobe 解析出的媒体信息。"""

    path: str
    duration_s: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    vcodec: str = ""
    acodec: str = ""
    has_audio: bool = False
    soft_subtitle_codec: str = ""     # 非 "" 即存在软字幕轨
    size_bytes: int = 0
    format_name: str = ""

    @property
    def is_supported(self) -> bool:
        """扩展名 ∈ {mp4, avi, mov, mkv, flv} 才可导入。"""
        return Path(self.path).suffix.lower() in SUPPORTED_EXTENSIONS


@dataclass
class ManualRegions:
    """U2 手动框选结果，归一化坐标。"""

    rects: list[BBox] = field(default_factory=list)
    time_start_s: float = 0.0
    time_end_s: float = -1.0         # -1 表示到片尾


@dataclass
class FeatureSet:
    """M3 特征提取产物。"""

    composition: npt.NDArray[np.float32]        # (shot_count, 512) L2 归一化 CLIP 向量
    shot_mid_ts: list[float]                    # 各镜头中点时刻
    motion_curve: npt.NDArray[np.float32]       # (32, 3)，列=[pan_x, pan_y, zoom]，归一化 [-1,1]
    rhythm_hist: npt.NDArray[np.float32]        # (16,) 镜头时长 log 直方图，和为 1
    cut_rate_curve: npt.NDArray[np.float32]     # (32,) 切换率曲线，归一化 [0,1]
    duration_s: float = 0.0
    version: str = "1"             # 特征版本号，版本不同则不可直接比对


@dataclass
class DimScores:
    """比对维度得分，均 ∈ [0,1]。"""

    composition: float
    motion: float
    rhythm: float
    overall: float


@dataclass
class CompareTarget:
    """单个对比对象（自动候选或手动指定）。"""

    source: Literal["auto", "manual"]
    platform_id: str = ""
    video_key: str = ""
    title: str = ""
    url: str = ""
    local_path: str = ""              # manual 或已缓存候选时填写
    scores: DimScores | None = None
    status: Literal["ok", "skipped", "unavailable"] = "ok"
    status_reason: str = ""


@dataclass
class CompareReport:
    """重复度比对报告。"""

    src_path: str
    overall_score: float              # 0~100
    dims: DimScores
    weights: tuple[float, float, float]
    targets: list[CompareTarget]
    unavailable_platforms: list[str] = field(default_factory=list)
    created_at: str = ""


@dataclass
class TaskPayload:
    """M4 任务载荷。type 字段决定必填子字段，校验规则见 M4 模块。

    - download: metas=list[VideoMeta], keyword=str, limit=int
    - preprocess: items=list[{src, ops}]
    - dedup: items=list[{src, scheme_id 或 technique_params}]
    - compare: srcs=list[path], mode=auto/manual/both, ref_paths=list[path]
    """

    type: Literal["download", "preprocess", "dedup", "compare"]
    data: dict[str, object] = field(default_factory=dict)

