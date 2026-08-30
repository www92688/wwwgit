# 帧级修复管线（详设 13.2 FrameLevelProcessor）：检测→流式修复→耗时守卫
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import numpy.typing as npt

from ych.common.cancellation import CancellationToken, ProgressFn
from ych.common.schemas import MediaInfo
from ych.core.m2_preprocess.filter_only_processor import build_vf_chain
from ych.core.m2_preprocess.ops import PreprocessOps
from ych.core.m2_preprocess.region_temporal import RegionTemporal, manual_spans
from ych.services.s1_media.encoder_spec import EncoderSpec
from ych.services.s1_media.ffmpeg_runner import FFmpegRunner
from ych.services.s1_media.frame_extractor import FrameExtractor
from ych.services.s2_ai.postprocess import RegionSpan
from ych.services.s2_ai.provider import InferenceProvider
from ych.services.s5_base.config_service import ConfigService

logger = logging.getLogger("ych.m2")

# 静态区缓存刷新周期（帧）；水印/台标位置基本不动，贴缓存可大幅省推理
CACHE_REFRESH_FRAMES = 30


@dataclass
class RepairStats:
    """修复统计（测试断言缓存命中用）。"""

    spans: int = 0
    inpaint_calls: int = 0
    cache_hits: int = 0
    cache_refreshes: int = 0
    frames: int = 0
    extra: dict[str, float] = field(default_factory=dict)


def pixel_rect(bbox: object, width: int, height: int) -> tuple[int, int, int, int]:
    """归一化 BBox → 像素 (x0, y0, x1, y1)，clamp 到画面内且至少 1px。"""
    x = float(bbox.x)   # type: ignore[attr-defined]
    y = float(bbox.y)   # type: ignore[attr-defined]
    w = float(bbox.w)   # type: ignore[attr-defined]
    h = float(bbox.h)   # type: ignore[attr-defined]
    x0 = min(max(round(x * width), 0), max(width - 1, 0))
    y0 = min(max(round(y * height), 0), max(height - 1, 0))
    x1 = min(max(round((x + w) * width), x0 + 1), width)
    y1 = min(max(round((y + h) * height), y0 + 1), height)
    return x0, y0, x1, y1


def repair_frame(
    frame: npt.NDArray[np.uint8],
    ts: float,
    spans: list[RegionSpan],
    provider: InferenceProvider,
    cache: dict[int, npt.NDArray[np.uint8]],
    last_refresh: dict[int, int],
    frame_idx: int,
    size: tuple[int, int],
    stats: RepairStats,
) -> npt.NDArray[np.uint8]:
    """单帧修复纯逻辑（独立函数便于无 ffmpeg 单测）：掩码→缓存/推理→回贴。"""
    stats.frames += 1
    # frombuffer 产物为只读视图，回贴需要可写副本
    if not frame.flags.writeable:
        frame = np.array(frame, dtype=np.uint8, copy=True)
    for si, span in enumerate(spans):
        if not (span.t_start <= ts <= span.t_end):
            continue
        x0, y0, x1, y1 = pixel_rect(span.bbox, size[0], size[1])
        region = frame[y0:y1, x0:x1]
        if region.size == 0:
            continue
        need_refresh = (
            si not in cache
            or frame_idx - last_refresh.get(si, -10**9) >= CACHE_REFRESH_FRAMES
        )
        if need_refresh:
            mask = np.full(region.shape[:2], 255, dtype=np.uint8)
            repaired = provider.inpaint(region, mask)
            cache[si] = np.array(repaired, dtype=np.uint8, copy=True)
            last_refresh[si] = frame_idx
            stats.inpaint_calls += 1
            stats.cache_refreshes += 1
        else:
            repaired = cache[si]
            stats.cache_hits += 1
        frame[y0:y1, x0:x1] = repaired[: y1 - y0, : x1 - x0]
    return frame


class FrameLevelProcessor:
    """阶段A 检测（抽样 ≤detect_sample_frames）→ 阶段B run_pipe 流式修复 → 阶段C 守卫。"""

    def __init__(
        self,
        runner: FFmpegRunner,
        extractor: FrameExtractor,
        provider: InferenceProvider,
        config: ConfigService,
    ) -> None:
        self._runner = runner
        self._extractor = extractor
        self._provider = provider
        self._config = config

    def process(
        self,
        src: Path,
        probe: MediaInfo,
        ops: PreprocessOps,
        out_target: Path,
        on_progress: ProgressFn | None,
        token: CancellationToken,
    ) -> RepairStats:
        t_start = time.monotonic()
        stats = RepairStats()
        duration_s = max(probe.duration_s, 1e-6)

        spans = self._detect_spans(src, probe, ops, token, on_progress)
        stats.spans = len(spans)
        if on_progress is not None:
            on_progress(0.15)

        fps = max(probe.fps, 1.0)
        total_frames = max(1, int(duration_s * fps))
        vf = build_vf_chain(ops, probe)
        encode_args = [
            "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-s", f"{probe.width}x{probe.height}", "-r", f"{fps:.6f}",
            "-i", "pipe:0",
        ]
        if vf:
            encode_args += ["-vf", vf]
        encode_args += EncoderSpec().to_args(
            with_audio=probe.has_audio and not ops.strip_audio
        )
        encode_args += ["-f", "mp4", str(out_target)]

        cache: dict[int, npt.NDArray[np.uint8]] = {}
        last_refresh: dict[int, int] = {}
        idx_box = [0]

        def repair_cb(frame: npt.NDArray[np.uint8]) -> npt.NDArray[np.uint8]:
            idx = idx_box[0]
            idx_box[0] += 1
            ts = idx / max(fps, 1e-6)
            return repair_frame(
                frame, ts, spans, self._provider,
                cache, last_refresh, idx, (probe.width, probe.height), stats,
            )

        def wrapped(ratio: float) -> None:
            if on_progress is not None:
                on_progress(0.15 + min(max(ratio, 0.0), 1.0) * 0.85)

        logger.info("帧级路径：%d 个修复区域，输出 %s", len(spans), out_target.name)
        self._runner.run_pipe(
            decode_args=["-i", str(src)],
            encode_args=encode_args,
            frame_cb=repair_cb,
            total_frames=total_frames,
            on_progress=wrapped,
            token=token,
            frame_size=(probe.width, probe.height),
            fps=fps,
        )

        # 阶段C：耗时守卫（超预算记 WARN 不中断）
        elapsed = time.monotonic() - t_start
        stats.extra["elapsed_s"] = elapsed
        if elapsed > 2.0 * duration_s:
            logger.warning(
                "性能预算超标：帧级处理 %.1fs > 2×时长 %.1fs（%s）",
                elapsed, 2.0 * duration_s, src.name,
            )
        return stats

    def _detect_spans(
        self,
        src: Path,
        probe: MediaInfo,
        ops: PreprocessOps,
        token: CancellationToken,
        on_progress: ProgressFn | None,
    ) -> list[RegionSpan]:
        """auto：抽帧（fps≈2、cap=detect_sample_frames）→ 检测 → 时域稳定。

        manual：直接由 ManualRegions 构造全程 span，不经聚类。
        """
        spans: list[RegionSpan] = []
        need_sampling = (
            (ops.remove_watermark_mode == "auto")
            or (ops.remove_subtitle_mode == "auto")
        )
        pairs: list[tuple[float, npt.NDArray[np.uint8]]] = []
        ts_list: list[float] = []
        if need_sampling:
            cap = self._config.get_typed("detect_sample_frames", int)
            frames = self._extractor.uniform(src, fps=2.0, max_frames=cap, token=token)
            pairs = [(f.ts, f.img) for f in frames]
            ts_list = [t for t, _img in pairs]
            if on_progress is not None:
                on_progress(0.05)

        if ops.remove_watermark_mode == "auto" and pairs:
            per_frame = self._provider.detect_watermark(pairs)
            spans += RegionTemporal.build_spans(per_frame, ts_list, 2.0)
        if ops.remove_subtitle_mode == "auto" and pairs:
            per_frame = self._provider.detect_subtitle(pairs)
            spans += RegionTemporal.build_spans(per_frame, ts_list, 2.0)
        spans += manual_spans(ops.watermark_regions, probe.duration_s)
        spans += manual_spans(ops.subtitle_regions, probe.duration_s)
        return spans



