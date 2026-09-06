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
from ych.common.errors import (
    ERR_MED_TIMEOUT_KILLED,
    ERR_MED_TRANSCODE_FAILED,
    AppError,
)
from ych.services.s1_media.ffmpeg_runner import FFmpegRunner
from ych.services.s1_media.probe_service import ProbeService

logger = logging.getLogger("ych.s1")

# 分析帧最长边上限：特征提取整载 ≤300 帧，1080p 全分辨率约 1.9GB 内存
# （检测/嵌入/直方图内部各自 resize，归一化坐标与分辨率无关）
_ANALYSIS_MAX_SIDE = 640


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


def analysis_size(width: int, height: int) -> tuple[int, int] | None:
    """分析帧降采样目标（最长边 ≤640，偶数对齐）；无需缩放返回 None。"""
    if width <= 0 or height <= 0 or max(width, height) <= _ANALYSIS_MAX_SIDE:
        return None
    if width >= height:
        nw = _ANALYSIS_MAX_SIDE - (_ANALYSIS_MAX_SIDE % 2)
        nh = max(2, round(height * nw / width / 2) * 2)
    else:
        nh = _ANALYSIS_MAX_SIDE - (_ANALYSIS_MAX_SIDE % 2)
        nw = max(2, round(width * nh / height / 2) * 2)
    return nw, nh


class FrameExtractor:
    """基于 ffmpeg rawvideo 管道的抽帧；分析路径内存占用恒定。"""

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
        aborted = False
        try:
            while True:
                if token is not None and token.cancelled:
                    aborted = True
                    break
                buf = proc.stdout.read(frame_bytes)
                if not buf or len(buf) != frame_bytes:
                    break
                img = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 3)
                yield Frame(ts=idx / eff_fps if eff_fps > 0 else 0.0, img=img)
                idx += 1
        except GeneratorExit:
            # 消费方提前 break（max_frames 截断）：不算抽帧失败
            aborted = True
            raise
        finally:
            if proc.stdout is not None:
                proc.stdout.close()
            try:
                code = proc.wait(timeout=30)
            except subprocess.TimeoutExpired as exc:
                proc.kill()
                proc.wait(timeout=10)
                if not aborted:
                    raise AppError(
                        ERR_MED_TIMEOUT_KILLED, "抽帧进程未退出，已终止"
                    ) from exc
                code = -1
            if not aborted and code != 0:
                # 解码中断（损坏文件/退出码异常）：明确报错，
                # 避免半截帧集静默参与特征提取/检测
                raise AppError(
                    ERR_MED_TRANSCODE_FAILED, f"抽帧失败（exit={code}）"
                )

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
        return list(self.stream_pairs_all(path, eff, max_frames, token))

    def stream_pairs_all(
        self,
        path: Path,
        eff_fps: float,
        max_frames: int,
        token: CancellationToken | None = None,
    ) -> Iterator[Frame]:
        """逐帧产出（uniform 的流式内核）。

        解码侧挂 fps 滤镜实现真正的均匀采样（否则 ffmpeg 输出全部原生帧，
        ts=idx/eff_fps 的时间戳与画面内容错位）；超尺寸素材同步降采样。
        """
        info = self._prober.probe(path)
        size = self._size_of(info.width, info.height)
        scaled = analysis_size(info.width, info.height)
        vf = [f"fps={max(eff_fps, 1e-6):.6f}"]
        if scaled is not None:
            vf.append(f"scale={scaled[0]}:{scaled[1]}")
            size = scaled
        decode_args = ["-i", str(path), "-vf", ",".join(vf)]
        for count, frame in enumerate(
            self._iter_frames(path, decode_args, size, eff_fps, token)
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


