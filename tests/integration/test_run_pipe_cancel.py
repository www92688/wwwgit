# run_pipe 取消/异常路径回归（详设 8.4 拾遗）：
# - 取消看护：阻塞中的管道 IO 能被及时解除
# - frame_cb 抛异常：双子进程被杀干净，输出文件不被残留进程锁定
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from ych.common.cancellation import CancellationToken, TaskCanceled
from ych.services.s1_media.encoder_spec import EncoderSpec
from ych.services.s1_media.ffmpeg_runner import FFmpegRunner
from ych.services.s1_media.probe_service import ProbeService

pytestmark = pytest.mark.integration


@pytest.fixture
def real_runner(ffmpeg_bin, ffprobe_bin) -> FFmpegRunner:
    return FFmpegRunner(ffmpeg_path=ffmpeg_bin, ffprobe_path=ffprobe_bin)


def _encode_args(info: object, out: Path) -> list[str]:
    return [
        "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
        "-s", f"{info.width}x{info.height}", "-r", "10", "-i", "pipe:0",
        *EncoderSpec().to_args(with_audio=False), str(out),
    ]


def _assert_output_released(out: Path) -> None:
    """输出文件最终可删除（无残留 ffmpeg 持锁）；从未落盘也算通过。"""
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            out.unlink()
            return
        except FileNotFoundError:
            return
        except PermissionError:
            time.sleep(0.1)
    pytest.fail("输出文件仍被残留 ffmpeg 进程锁定")


def test_cancel_during_pipeline_raises_and_cleans_up(
    real_runner, media_dir, tmp_path,
) -> None:
    prober = ProbeService(real_runner)
    info = prober.probe(media_dir / "solid.mp4")
    out = tmp_path / "out_cancel.mp4"
    token = CancellationToken()

    def slow_cb(frame: object) -> object:
        time.sleep(0.15)     # 拖慢循环，让取消落在回调/读取之间
        return frame

    threading.Timer(0.4, token.cancel).start()
    start = time.monotonic()
    with pytest.raises(TaskCanceled):
        real_runner.run_pipe(
            decode_args=["-i", str(media_dir / "solid.mp4")],
            encode_args=_encode_args(info, out),
            frame_cb=slow_cb,
            total_frames=999,
            on_progress=None,
            token=token,
            frame_size=(info.width, info.height),
            fps=10.0,
        )
    assert time.monotonic() - start < 5    # 看护及时解除阻塞
    _assert_output_released(out)


def test_frame_cb_exception_kills_children(
    real_runner, media_dir, tmp_path,
) -> None:
    prober = ProbeService(real_runner)
    info = prober.probe(media_dir / "solid.mp4")
    out = tmp_path / "out_boom.mp4"

    def boom(_frame: object) -> object:
        raise RuntimeError("frame_cb 内部错误")

    with pytest.raises(RuntimeError, match="frame_cb"):
        real_runner.run_pipe(
            decode_args=["-i", str(media_dir / "solid.mp4")],
            encode_args=_encode_args(info, out),
            frame_cb=boom,
            total_frames=100,
            on_progress=None,
            token=CancellationToken(),
            frame_size=(info.width, info.height),
            fps=10.0,
        )
    # 旧实现此路径不杀进程：残留编码端会短暂/长期锁住输出文件
    _assert_output_released(out)
