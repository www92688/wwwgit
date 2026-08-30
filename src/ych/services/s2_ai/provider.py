# InferenceProvider 协议与检测结果类型（详设 9.3）
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np
import numpy.typing as npt

from ych.common.schemas import BBox


@dataclass
class Detection:
    """单帧检测结果；bbox 为映射回原分辨率后的归一化坐标。"""

    bbox: BBox
    confidence: float
    label: str               # "watermark" | "subtitle"
    ts: float                # 该帧时间戳



class InferenceProvider(Protocol):
    """四类推理能力的统一出口协议（M2/M3 只依赖本协议）。"""

    def detect_watermark(
        self, frames: list[tuple[float, npt.NDArray[np.uint8]]]
    ) -> list[list[Detection]]:
        """逐帧水印检测；入参为 (ts, frame BGR) 列表。"""
        ...

    def detect_subtitle(
        self, frames: list[tuple[float, npt.NDArray[np.uint8]]]
    ) -> list[list[Detection]]:
        """逐帧字幕区域检测（中英文通用）。"""
        ...

    def inpaint(
        self,
        frame: npt.NDArray[np.uint8],
        mask: npt.NDArray[np.uint8],
    ) -> npt.NDArray[np.uint8]:
        """对 mask（255=待修复区域）进行画面修复，返回修复后图像。"""
        ...

    def embed_frames(
        self, frames: list[tuple[float, npt.NDArray[np.uint8]]]
    ) -> npt.NDArray[np.float32]:
        """关键帧嵌入向量，输出 (n, 512)，已 L2 归一化。"""
        ...

