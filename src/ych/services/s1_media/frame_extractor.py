# 抽帧服务（详设 8.2）：uniform / single / stream_pairs
from __future__ import annotations

import logging
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt

from ych.common.cancellation import CancellationToken
from ych.common.errors import ERR_MED_TRANSCODE_FAILED, AppError
from ych.services.s1_media.ffmpeg_runner import FFmpegRunner
from ych.services.s1_media.probe_service import ProbeService

logger = logging.getLogger("ych.s1")


@dataclass
class Frame:
    """单帧：时间戳 + BGR 图像。"""

    ts: float
    img: npt.NDArray[np.uint8]


def effective_fps(duration_s: float, fps: float, max_frames: int) -> float:
    """自适应帧率：dur*fps 超上限时取 max_frames/dur（纯函数，可表驱动测试）。"""
    if duration_s <= 0 or max_frames <= 0:
        return fps
    if duration_s * fps > max_frames:
        return max_frames / duration_s
    return fps


class FrameExtractor:
    """基于 ffmpeg rawvideo 管道的抽帧；内存占用恒定。"""

    def __init__(self, runner: FFmpegRunner, prober: ProbeService) -> None:
        self._runner = runner
        self._prober = prober

    # ---- 内部流式读取 ----
    def _iter_frames(
        self,
        path: Path,
        decode_args: list[str],
        size: tuple[int, int],
        eff_fps: float,
        token: CancellationToken | None,
    ) -> Iterator[Frame]:
        ffmpeg, _ = self._runner.locate_binaries()
        w, h = size
        frame_bytes = w * h * 3
        cmd = [
            str(ffmpeg), *decode_args,
            "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1",
        ]
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert proc.stdout is not None
        idx = 0
        try:
            while True:
                if token is not None and token.cancelled:
                    break
                buf = proc.stdout.read(frame_bytes)
                if not buf or len(buf) != frame_bytes:
                    break
                img = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 3)
                yield Frame(ts=idx / eff_fps if eff_fps > 0 else 0.0, img=img)
                idx += 1
        finally:
            if proc.stdout is not None:
                proc.stdout.close()
            code = proc.wait(timeout=30)
            if code != 0 and idx == 0:
                raise AppError(ERR_MED_TRANSCODE_FAILED, f"抽帧失败（exit={code}）")

    def _size_of(self, info_w: int, info_h: int) -> tuple[int, int]:
        return (info_w, info_h)

    # ---- 公开 API ----
    def uniform(
        self,
        path: Path,
        fps: float = 2.0,
        max_frames: int = 300,
        token: CancellationToken | None = None,
    ) -> list[Frame]:
        """按（自适应）帧率均匀抽帧；超上限自动降 fps。"""
        info = self._prober.probe(path)
        eff = effective_fps(info.duration_s, fps, max_frames)
        frames = list(self.stream_pairs_all(path, eff, max_frames, token))
        return frames

    def stream_pairs_all(
        self,
        path: Path,
        eff_fps: float,
        max_frames: int,
        token: CancellationToken | None = None,
    ) -> Iterator[Frame]:
        """逐帧产出（uniform 的流式内核）。"""
        info = self._prober.probe(path)
        for count, frame in enumerate(
            self._iter_frames(
                path, ["-i", str(path)], self._size_of(info.width, info.height),
                eff_fps, token,
            )
        ):
            yield frame
            if count + 1 >= max_frames:
                break

    def single(self, path: Path, ts: float) -> Frame:
        """取指定时刻的单帧（-ss 快速定位）。"""
        info = self._prober.probe(path)
        frames = list(self._iter_frames(
            path, ["-ss", f"{max(0.0, ts):.3f}", "-i", str(path), "-frames:v", "1"],
            self._size_of(info.width, info.height),
            eff_fps=1.0,
            token=None,
        ))
        if not frames:
            raise AppError(ERR_MED_TRANSCODE_FAILED, "抽帧失败：无输出帧")
        # 时间戳以请求的 ts 为准（-frames:v 1 时 idx 计数无意义）
        return Frame(ts=ts, img=frames[0].img)

    def stream_pairs(
        self,
        path: Path,
        fps: float = 2.0,
        max_frames: int = 300,
        token: CancellationToken | None = None,
    ) -> Iterator[tuple[Frame, Frame]]:
        """相邻帧对生成器（光流用），避免整载内存（详设 8.2）。"""
        prev: Frame | None = None
        emitted = 0
        for frame in self.stream_pairs_all(path, fps, max_frames, token):
            if prev is not None:
                yield prev, frame
                emitted += 1
                if emitted >= max_frames - 1:
                    break
            prev = frame


