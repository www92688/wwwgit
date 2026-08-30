# DummyProvider：确定性测试替身（详设 9.6；M2/M3 单测统一基于它）
from __future__ import annotations

import hashlib

import numpy as np
import numpy.typing as npt

from ych.common.schemas import BBox
from ych.services.s2_ai.provider import Detection


class DummyProvider:
    """确定性替身：

    - detect_watermark：每帧返回预设中部 BBox；
    - detect_subtitle：返回底部字幕带 BBox（可开关）；
    - inpaint：mask 区域填充固定色块（BGR 紫色 200,50,180）；
    - embed_frames：由图像内容哈希生成可复现 512 维向量，L2 归一化。
    """

    def __init__(
        self,
        watermark_bbox: BBox | None = None,
        subtitle_bbox: BBox | None = None,
        subtitle_enabled: bool = True,
        fill_color: tuple[int, int, int] = (200, 50, 180),
        embed_dim: int = 512,
    ) -> None:
        self.watermark_bbox = watermark_bbox or BBox(x=0.4, y=0.4, w=0.2, h=0.1)
        self.subtitle_bbox = subtitle_bbox or BBox(x=0.05, y=0.85, w=0.9, h=0.12)
        self.subtitle_enabled = subtitle_enabled
        self.fill_color = fill_color
        self.embed_dim = embed_dim
        # 统计计数（供缓存命中/调用次数断言）
        self.inpaint_calls = 0

    def detect_watermark(
        self, frames: list[tuple[float, npt.NDArray[np.uint8]]]
    ) -> list[list[Detection]]:
        return [
            [Detection(bbox=self.watermark_bbox, confidence=0.9,
                       label="watermark", ts=ts)]
            for ts, _frame in frames
        ]

    def detect_subtitle(
        self, frames: list[tuple[float, npt.NDArray[np.uint8]]]
    ) -> list[list[Detection]]:
        if not self.subtitle_enabled:
            return [[] for _ in frames]
        return [
            [Detection(bbox=self.subtitle_bbox, confidence=0.85,
                       label="subtitle", ts=ts)]
            for ts, _frame in frames
        ]

    def inpaint(
        self,
        frame: npt.NDArray[np.uint8],
        mask: npt.NDArray[np.uint8],
    ) -> npt.NDArray[np.uint8]:
        self.inpaint_calls += 1
        out = frame.copy()
        out[mask > 0] = np.array(self.fill_color, dtype=np.uint8)
        return out

    def embed_frames(
        self, frames: list[tuple[float, npt.NDArray[np.uint8]]]
    ) -> npt.NDArray[np.float32]:
        vecs: list[npt.NDArray[np.float32]] = []
        for _ts, frame in frames:
            digest = hashlib.sha256(frame.tobytes()).digest()
            rng = np.random.default_rng(int.from_bytes(digest[:8], "little"))
            vec = rng.standard_normal(self.embed_dim).astype(np.float32)
            vec /= max(float(np.linalg.norm(vec)), 1e-9)
            vecs.append(vec)
        if not vecs:
            return np.zeros((0, self.embed_dim), dtype=np.float32)
        return np.stack(vecs)
