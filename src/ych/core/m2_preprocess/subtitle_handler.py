# 字幕路由与软字幕剥离（详设 13.2 SubtitleHandler）
from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

import numpy as np
import numpy.typing as npt

from ych.common.cancellation import CancellationToken
from ych.common.errors import ERR_MED_TRANSCODE_FAILED, AppError
from ych.common.schemas import MediaInfo
from ych.core.m2_preprocess.ops import SubtitleRoute
from ych.services.s1_media.ffmpeg_runner import FFmpegRunner
from ych.services.s1_media.frame_extractor import FrameExtractor
from ych.services.s2_ai.provider import InferenceProvider

logger = logging.getLogger("ych.m2")


class _ExtractorLike(Protocol):
    def single(self, path: Path, ts: float) -> object: ...


class SubtitleHandler:
    """auto：软轨→soft；无软轨则抽样 ≤6 帧 OCR 查底部文字带→hard；否则 none。

    manual 视为硬字幕（用户已框定烧录区域）；off 恒为 none。
    """

    SAMPLE_FRAMES = 6
    BOTTOM_BAND_CENTER_Y = 0.6     # 检出框中心位于画面下方 40% 即视为底部文字带

    def __init__(
        self,
        runner: FFmpegRunner,
        extractor: FrameExtractor,
        provider: InferenceProvider,
    ) -> None:
        self._runner = runner
        self._extractor = extractor
        self._provider = provider

    def route(self, probe: MediaInfo, mode: str) -> SubtitleRoute:
        if mode == "off":
            return "none"
        if mode == "manual":
            return "hard"
        # auto
        if probe.soft_subtitle_codec:
            return "soft"
        frames = self._sample_frames(probe)
        if not frames:
            return "none"
        dets_per_frame = self._provider.detect_subtitle(frames)
        for dets in dets_per_frame:
            for det in dets:
                center_y = det.bbox.y + det.bbox.h / 2.0
                if center_y >= self.BOTTOM_BAND_CENTER_Y:
                    return "hard"
        return "none"

    def _sample_frames(
        self, probe: MediaInfo,
    ) -> list[tuple[float, npt.NDArray[np.uint8]]]:
        """均匀抽 ≤SAMPLE_FRAMES 帧供 OCR；单帧抽取失败静默跳过。"""
        src = Path(probe.path)
        duration = probe.duration_s
        if duration <= 0:
            ts_list = [0.0]
        else:
            ts_list = [
                duration * (i + 0.5) / self.SAMPLE_FRAMES
                for i in range(self.SAMPLE_FRAMES)
            ]
        out: list[tuple[float, npt.NDArray[np.uint8]]] = []
        for ts in ts_list[: self.SAMPLE_FRAMES]:
            try:
                frame = self._extractor.single(src, ts)
            except AppError as exc:      # 单帧失败不阻断路由判定
                logger.warning("字幕检测抽帧失败 ts=%.2f：%s", ts, exc)
                continue
            out.append((ts, frame.img))
        return out

    # ---- 软字幕剥离（流复制，秒级）----
    def strip_to(
        self,
        src: Path,
        out_target: Path,
        drop_audio: bool = False,
        token: CancellationToken | None = None,
    ) -> Path:
        """-map 0:v [-map 0:a?] -sn -c copy；drop_audio 时以 -an 替代音频映射。"""
        args = ["-i", str(src), "-map", "0:v"]
        if not drop_audio:
            args += ["-map", "0:a?"]
        args += ["-sn", "-c", "copy"]
        if drop_audio:
            args.append("-an")
        args += ["-f", "mp4", "-y", str(out_target)]
        logger.info("软字幕剥离（流复制）：%s", src.name)
        code = self._runner.run(args, token=token)
        if code != 0:
            raise AppError(ERR_MED_TRANSCODE_FAILED, f"软字幕剥离失败（exit={code}）")
        return out_target
