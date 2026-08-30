# S1 集成测试（-m integration：需要真实 ffmpeg，缺失自动 skip）
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from ych.common.cancellation import CancellationToken
from ych.common.errors import ERR_MED_TRANSCODE_FAILED, AppError
from ych.services.s1_media.encoder_spec import EncoderSpec
from ych.services.s1_media.ffmpeg_runner import FFmpegRunner
from ych.services.s1_media.frame_extractor import FrameExtractor
from ych.services.s1_media.probe_service import ProbeService

pytestmark = pytest.mark.integration


@pytest.fixture
def real_runner(ffmpeg_bin, ffprobe_bin) -> FFmpegRunner:
    return FFmpegRunner(ffmpeg_path=ffmpeg_bin, ffprobe_path=ffprobe_bin)


def test_uniform_and_single(real_runner, media_dir) -> None:
    prober = ProbeService(real_runner)
    extractor = FrameExtractor(real_runner, prober)
    src = media_dir / "solid.mp4"
    frames = extractor.uniform(src, fps=5.0, max_frames=100)
    assert len(frames) >= 3
    assert frames[0].img.shape == (48, 64, 3)
    # 时间戳单调
    ts_list = [f.ts for f in frames]
    assert ts_list == sorted(ts_list)

    one = extractor.single(src, 0.2)
    assert one.img.shape == (48, 64, 3)


def test_stream_pairs_yields_adjacent_pairs(real_runner, media_dir) -> None:
    prober = ProbeService(real_runner)
    extractor = FrameExtractor(real_runner, prober)
    pairs = list(extractor.stream_pairs(media_dir / "checker.mp4",
                                        fps=5.0, max_frames=50))
    assert len(pairs) >= 1
    prev, cur = pairs[0]
    assert cur.ts > prev.ts


def test_run_pipe_end_to_end_output_reproducible(real_runner, media_dir,
                                                 tmp_path) -> None:
    """run_pipe 端到端：反相帧回调 → 输出可被再次 probe（详设 8.4）。"""
    prober = ProbeService(real_runner)
    src = media_dir / "solid.mp4"
    info = prober.probe(src)
    out = tmp_path / "out.mp4"
    spec = EncoderSpec()
    encode_args = [
        "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
        "-s", f"{info.width}x{info.height}", "-r", "10", "-i", "pipe:0",
        *spec.to_args(with_audio=False), str(out),
    ]
    runner = real_runner

    def invert(frame: np.ndarray) -> np.ndarray:
        return 255 - frame

    progress: list[float] = []
    returned = runner.run_pipe(
        decode_args=["-i", str(src)],
        encode_args=encode_args,
        frame_cb=invert,
        total_frames=max(1, int(info.duration_s * 10)),
        on_progress=progress.append,
        token=CancellationToken(),
        frame_size=(info.width, info.height),
        fps=10.0,
    )
    assert Path(returned).exists() and out.stat().st_size > 0
    info2 = prober.probe(out)
    assert info2.width == info.width and info2.height == info.height
    assert progress and progress[-1] == 1.0


def test_run_pipe_decode_failure_raises_med010(tmp_path, ffmpeg_bin,
                                               ffprobe_bin) -> None:
    runner = FFmpegRunner(ffmpeg_path=ffmpeg_bin, ffprobe_path=ffprobe_bin)
    missing = tmp_path / "no_such_input.mp4"
    out = tmp_path / "never.mp4"
    with pytest.raises(AppError) as exc:
        runner.run_pipe(
            decode_args=["-i", str(missing)],
            encode_args=["-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
                         "-s", "64x48", "-r", "10", "-i", "pipe:0",
                         "-c:v", "libx264", str(out)],
            frame_cb=lambda f: f,
            total_frames=10,
            on_progress=None,
            token=CancellationToken(),
            frame_size=(64, 48),
            fps=10.0,
        )
    assert exc.value.code == ERR_MED_TRANSCODE_FAILED



