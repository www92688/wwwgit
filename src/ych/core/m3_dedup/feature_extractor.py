# 特征提取总流程（详设 14.1.1/14.1.5）
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import numpy.typing as npt

from ych.common.cancellation import CancellationToken
from ych.common.schemas import FeatureSet
from ych.core.m3_dedup.motion_analyzer import MotionAnalyzer
from ych.core.m3_dedup.rhythm_analyzer import RhythmAnalyzer
from ych.core.m3_dedup.scene_detector import SceneDetector
from ych.services.s1_media.frame_extractor import FrameExtractor
from ych.services.s1_media.probe_service import ProbeService
from ych.services.s2_ai.provider import InferenceProvider

logger = logging.getLogger("ych.m3")

SAMPLE_FPS = 2.0
MAX_FRAMES = 300
MAX_SHOTS = 32

FEATURE_VERSION = "1"


class CompositionEmbedder:
    """每镜头中点最近帧 → provider.embed_frames → (m,512) L2 归一化。"""

    def __init__(self, provider: InferenceProvider) -> None:
        self._provider = provider

    def embed(
        self,
        frames_ts: list[float],
        frames_img: list[npt.NDArray[np.uint8]],
        shots: list[tuple[float, float]],
    ) -> tuple[npt.NDArray[np.float32], list[float]]:
        """返回 (composition 矩阵, 对应镜头中点时刻)；m>32 均匀下采样。"""
        mids = [0.5 * (s + e) for s, e in shots]
        if not mids or not frames_ts:
            return (np.zeros((0, 512), dtype=np.float32), [])
        idxs = [int(np.argmin(np.abs(np.asarray(frames_ts) - mid)))
                for mid in mids]
        pairs = [(frames_ts[i], frames_img[i]) for i in idxs]
        vectors = np.asarray(
            self._provider.embed_frames(pairs), dtype=np.float32
        )
        if vectors.ndim != 2 or vectors.shape[0] != len(pairs):
            logger.warning("embed_frames 返回形状异常，构图特征置空")
            return (np.zeros((0, 512), dtype=np.float32), [])
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        vectors = vectors / np.maximum(norms, 1e-12)
        if len(mids) > MAX_SHOTS:      # 均匀下采样至 32
            sel = np.linspace(0, len(mids) - 1, MAX_SHOTS).astype(int)
            vectors = vectors[sel]
            mids = [mids[i] for i in sel]
        return (vectors.astype(np.float32), [round(m, 3) for m in mids])


class FeatureExtractService:
    """probe → 抽帧(fps=2,cap=300) → 切分/运镜/节奏/构图 → FeatureSet(v1)。"""

    def __init__(
        self,
        prober: ProbeService,
        extractor: FrameExtractor,
        provider: InferenceProvider,
    ) -> None:
        self._prober = prober
        self._extractor = extractor
        self._scenes = SceneDetector()
        self._motion = MotionAnalyzer()
        self._rhythm = RhythmAnalyzer()
        self._embedder = CompositionEmbedder(provider)

    def extract(
        self,
        path: Path,
        token: CancellationToken | None = None,
    ) -> FeatureSet:
        info = self._prober.probe(path)
        duration = max(info.duration_s, 1e-6)
        fps = SAMPLE_FPS
        if duration * SAMPLE_FPS > MAX_FRAMES:
            fps = MAX_FRAMES / duration          # 自适应降采样（详设 14.1.1）

        frames = list(self._extractor.stream_pairs_all(
            path, eff_fps=fps, max_frames=MAX_FRAMES, token=token,
        ))
        frames_ts = [f.ts for f in frames]
        frames_img = [f.img for f in frames]

        boundaries = self._scenes.split(frames_ts, frames_img, duration)
        shots = self._scenes.shots_of(boundaries, duration)
        composition, mid_ts = self._embedder.embed(frames_ts, frames_img, shots)
        motion_curve = self._motion.analyze(frames_img)
        rhythm_hist, cut_rate = self._rhythm.compute(boundaries, duration)

        logger.info("特征提取 %s：镜头=%d 边界=%s", path.name,
                    len(shots), boundaries)
        return FeatureSet(
            composition=composition,
            shot_mid_ts=mid_ts,
            motion_curve=motion_curve,
            rhythm_hist=rhythm_hist,
            cut_rate_curve=cut_rate,
            duration_s=duration,
            version=FEATURE_VERSION,
        )

