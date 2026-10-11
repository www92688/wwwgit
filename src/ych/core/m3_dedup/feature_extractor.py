# 特征提取总流程（详设 14.1.1/14.1.5）
from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import threading
from collections import OrderedDict
from pathlib import Path

import numpy as np
import numpy.typing as npt

from ych.common.cancellation import CancellationToken
from ych.common.schemas import FeatureSet
from ych.core.m3_dedup.candidate_cache import user_data_dir
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

_CACHE_CAP = 64   # 内存特征缓存条数上限（短视频特征约占 100KB 级）
_DISK_CACHE_FILES = 512   # 磁盘特征缓存文件数上限（超出按最旧清理）


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
        """返回 (composition 矩阵, 对应镜头中点时刻)；m>32 先选点再嵌入。"""
        mids = [0.5 * (s + e) for s, e in shots]
        if not mids or not frames_ts:
            return (np.zeros((0, 512), dtype=np.float32), [])
        if len(mids) > MAX_SHOTS:      # 先均匀选点再嵌入：省去被丢弃的推理
            sel = np.linspace(0, len(mids) - 1, MAX_SHOTS).astype(int)
            mids = [mids[i] for i in sel]
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
        return (vectors.astype(np.float32), [round(m, 3) for m in mids])


class FeatureExtractService:
    """probe → 抽帧(fps=2,cap=300) → 切分/运镜/节奏/构图 → FeatureSet(v1)。"""

    def __init__(
        self,
        prober: ProbeService,
        extractor: FrameExtractor,
        provider: InferenceProvider,
        max_frames: int = MAX_FRAMES,
        cache_dir: Path | None = None,
    ) -> None:
        self._prober = prober
        self._extractor = extractor
        self._scenes = SceneDetector()
        self._motion = MotionAnalyzer()
        self._rhythm = RhythmAnalyzer()
        self._embedder = CompositionEmbedder(provider)
        self._max_frames = max(1, int(max_frames))
        # (路径, mtime_ns, size) → FeatureSet：对比目标/去重前后重算
        # 反复抽同一文件代价高；文件被替换时键变化自然失效
        self._cache: OrderedDict[tuple[str, int, int], FeatureSet] = OrderedDict()
        self._cache_lock = threading.Lock()
        # 磁盘特征缓存：跨会话复用（同素材重复分析秒出）。命中失败/文件
        # 损坏一律静默重算——缓存只是加速层，绝不影响结果与画质
        self._disk_dir = (cache_dir if cache_dir is not None
                          else user_data_dir() / "feature_cache")

    def extract(
        self,
        path: Path,
        token: CancellationToken | None = None,
    ) -> FeatureSet:
        cached = self._cache_get(path)
        if cached is not None:
            return cached
        disk = self._disk_get(path)
        if disk is not None:
            logger.info("特征缓存命中（磁盘）：%s", Path(path).name)
            self._cache_put_mem(path, disk)
            return disk
        info = self._prober.probe(path)
        duration = max(info.duration_s, 1e-6)
        fps = SAMPLE_FPS
        if duration * SAMPLE_FPS > self._max_frames:
            fps = self._max_frames / duration     # 自适应降采样（详设 14.1.1）

        frames = list(self._extractor.stream_pairs_all(
            path, eff_fps=fps, max_frames=self._max_frames, token=token,
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
        feat = FeatureSet(
            composition=composition,
            shot_mid_ts=mid_ts,
            motion_curve=motion_curve,
            rhythm_hist=rhythm_hist,
            cut_rate_curve=cut_rate,
            duration_s=duration,
            version=FEATURE_VERSION,
        )
        self._cache_put(path, feat)
        return feat

    # ---- 特征缓存 ----
    def _cache_key(self, path: Path) -> tuple[str, int, int] | None:
        try:
            st = Path(path).stat()
        except OSError:
            return None
        return (str(Path(path).resolve()), st.st_mtime_ns, st.st_size)

    def _cache_get(self, path: Path) -> FeatureSet | None:
        key = self._cache_key(path)
        if key is None:
            return None
        with self._cache_lock:
            feat = self._cache.get(key)
            if feat is not None:
                self._cache.move_to_end(key)
            return feat

    def _cache_put(self, path: Path, feat: FeatureSet) -> None:
        self._cache_put_mem(path, feat)
        self._disk_put(path, feat)

    def _cache_put_mem(self, path: Path, feat: FeatureSet) -> None:
        key = self._cache_key(path)
        if key is None:
            return
        with self._cache_lock:
            self._cache[key] = feat
            self._cache.move_to_end(key)
            while len(self._cache) > _CACHE_CAP:
                self._cache.popitem(last=False)

    # ---- 磁盘特征缓存 ----
    def _disk_file(self, path: Path) -> Path | None:
        key = self._cache_key(path)
        if key is None:
            return None
        digest = hashlib.sha1("|".join(map(str, key)).encode("utf-8")).hexdigest()
        return self._disk_dir / f"v{FEATURE_VERSION}_{digest}.npz"

    def _disk_get(self, path: Path) -> FeatureSet | None:
        f = self._disk_file(path)
        if f is None or not f.exists():
            return None
        try:
            with np.load(f) as data:
                if str(data["version"]) != FEATURE_VERSION:
                    return None
                return FeatureSet(
                    composition=data["composition"].astype(np.float32),
                    shot_mid_ts=[float(x) for x in data["shot_mid_ts"]],
                    motion_curve=data["motion_curve"].astype(np.float32),
                    rhythm_hist=data["rhythm_hist"].astype(np.float32),
                    cut_rate_curve=data["cut_rate_curve"].astype(np.float32),
                    duration_s=float(data["duration_s"]),
                    version=str(data["version"]),
                )
        except (OSError, ValueError, KeyError, EOFError):
            logger.debug("特征缓存读取失败（重算）：%s", f.name)
            return None

    def _disk_put(self, path: Path, feat: FeatureSet) -> None:
        f = self._disk_file(path)
        if f is None:
            return
        try:
            self._disk_dir.mkdir(parents=True, exist_ok=True)
            tmp = f.with_name(f.name + ".tmp")
            with open(tmp, "wb") as fh:   # 文件对象写入：避免 numpy 追加 .npz 后缀
                np.savez_compressed(
                    fh,
                    composition=feat.composition.astype(np.float32),
                    shot_mid_ts=np.asarray(feat.shot_mid_ts, dtype=np.float64),
                    motion_curve=feat.motion_curve.astype(np.float32),
                    rhythm_hist=feat.rhythm_hist.astype(np.float32),
                    cut_rate_curve=feat.cut_rate_curve.astype(np.float32),
                    duration_s=feat.duration_s,
                    version=feat.version,
                )
            os.replace(tmp, f)
            self._prune_disk()
        except OSError:
            logger.debug("特征缓存写入失败（忽略）：%s", f.name)

    def _prune_disk(self) -> None:
        files = sorted(self._disk_dir.glob("*.npz"),
                       key=lambda p: p.stat().st_mtime)
        for old in files[:-_DISK_CACHE_FILES]:
            with contextlib.suppress(OSError):
                old.unlink()

