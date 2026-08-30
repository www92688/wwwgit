# S1 单元测试：EncoderSpec / parse_progress_line / effective_fps / ProbeService（假 ffprobe）
from __future__ import annotations

from pathlib import Path

import pytest

from ych.common.errors import ERR_MED_FORMAT_UNSUPPORTED, ERR_MED_PROBE_FAILED, AppError
from ych.services.s1_media.encoder_spec import EncoderSpec
from ych.services.s1_media.ffmpeg_runner import FFmpegRunner
from ych.services.s1_media.frame_extractor import effective_fps
from ych.services.s1_media.probe_service import ProbeService

FIXTURES = Path(__file__).parents[3] / "fixtures" / "fake_ffmpeg"


# ---------- EncoderSpec ----------
def test_encoder_spec_to_args_with_audio() -> None:
    args = EncoderSpec().to_args(with_audio=True)
    joined = " ".join(args)
    assert "-c:v libx264" in joined and "-crf 20" in joined
    assert "-preset medium" in joined and "-pix_fmt yuv420p" in joined
    assert "-c:a aac" in joined and "-b:a 128k" in joined and "-ar 44100" in joined
    assert "-movflags +faststart" in joined
    assert "-an" not in joined


def test_encoder_spec_to_args_without_audio_uses_an() -> None:
    args = EncoderSpec().to_args(with_audio=False)
    assert "-an" in args


# ---------- parse_progress_line 表驱动 ----------
@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("frame= 30 fps=25 q=28.0 time=00:01:23.45 bitrate=...", 83.45),
        ("time=00:00:00.00", 0.0),
        ("time=01:00:00.00", 3600.0),
        ("frame= 1 fps=0.0 q=-1.0 Lsize=N/A", None),
        ("", None),
        ("Duration: 00:00:01.04, start: 0", None),   # Duration 行不是进度行
    ],
)
def test_parse_progress_line(line: str, expected: float | None) -> None:
    assert FFmpegRunner.parse_progress_line(line) == expected


# ---------- effective_fps 自适应表驱动 ----------
@pytest.mark.parametrize(
    ("dur", "fps", "cap", "expected"),
    [
        (10.0, 2.0, 300, 2.0),      # 不超上限 → 原帧率
        (200.0, 2.0, 300, 1.5),     # 400 帧 > 300 → 300/200
        (0.0, 2.0, 300, 2.0),       # 时长异常 → 原样返回
        (100.0, 3.0, 150, 1.5),     # 300 帧 > 150 → 1.5
    ],
)
def test_effective_fps(dur: float, fps: float, cap: int, expected: float) -> None:
    assert abs(effective_fps(dur, fps, cap) - expected) < 1e-9


# ---------- ProbeService（fake_ffprobe 替身） ----------
@pytest.fixture
def prober() -> ProbeService:
    runner = FFmpegRunner(ffmpeg_path=FIXTURES / "fake_ffmpeg.bat",
                          ffprobe_path=FIXTURES / "fake_ffprobe.bat")
    return ProbeService(runner)


def test_probe_maps_all_fields(prober, tmp_path) -> None:
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"x")
    info = prober.probe(media)
    assert info.width == 64 and info.height == 48
    assert info.fps == pytest.approx(10.0)
    assert info.vcodec == "h264"
    assert info.acodec == "aac"
    assert info.has_audio is True
    assert info.soft_subtitle_codec == "mov_text"
    assert info.duration_s == pytest.approx(1.041667, abs=1e-4)
    assert info.size_bytes == 12345
    assert info.format_name.startswith("mov,mp4")


def test_probe_unsupported_extension_raises_med003(prober, tmp_path) -> None:
    f = tmp_path / "pic.gif"
    f.write_bytes(b"x")
    with pytest.raises(AppError) as exc:
        prober.probe(f)
    assert exc.value.code == ERR_MED_FORMAT_UNSUPPORTED


def test_probe_failure_raises_med002(prober, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_FFPROBE_FAIL", "1")
    f = tmp_path / "broken.mp4"
    f.write_bytes(b"x")
    with pytest.raises(AppError) as exc:
        prober.probe(f)
    assert exc.value.code == ERR_MED_PROBE_FAILED


def test_probe_chinese_path_utf8_output(prober, tmp_path) -> None:
    """缺陷回归（MED002 中文路径崩溃）：GBK 区域下 ffprobe 输出必须显式 utf-8 解码。

    替身对齐真实 ffprobe 口径恒定输出 UTF-8 并回显输入路径；若解码退化为
    区域编码（cp936），UTF-8 字节流将触发 UnicodeDecodeError 使本用例变红。
    """
    media = tmp_path / "中文 目录" / "测试 视频.mp4"
    media.parent.mkdir()
    media.write_bytes(b"x")
    info = prober.probe(media)
    assert info.path == str(media)   # 子进程参数往返无损
    assert info.width == 64 and info.height == 48
    assert info.duration_s == pytest.approx(1.041667, abs=1e-4)
    assert info.size_bytes == 12345
