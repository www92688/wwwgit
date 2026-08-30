# ffprobe 媒体信息探测（详设 8.3 字段映射）
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from ych.common.errors import ERR_MED_FORMAT_UNSUPPORTED, ERR_MED_PROBE_FAILED, AppError
from ych.common.schemas import SUPPORTED_EXTENSIONS, MediaInfo
from ych.services.s1_media.ffmpeg_runner import FFmpegRunner

logger = logging.getLogger("ych.s1")


def _fps_from_fraction(text: str) -> float:
    """r_frame_rate 形如 "10/1"、"30000/1001" → 浮点帧率。"""
    from fractions import Fraction

    try:
        return float(Fraction(text))
    except (ValueError, ZeroDivisionError):
        return 0.0


class ProbeService:
    """ffprobe JSON 解析 → MediaInfo；失败 MED002，扩展名不支持 MED003。"""

    def __init__(self, runner: FFmpegRunner) -> None:
        self._runner = runner

    def probe(self, path: Path) -> MediaInfo:
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise AppError(
                ERR_MED_FORMAT_UNSUPPORTED,
                f"不支持的格式：{path.suffix}（支持 mp4/avi/mov/mkv/flv）",
            )
        ffprobe, _ = (self._runner.locate_binaries()[1],
                      self._runner.locate_binaries()[0])
        cmd = [
            str(ffprobe), "-v", "quiet",
            "-print_format", "json", "-show_format", "-show_streams",
            str(path),
        ]
        try:
            # encoding 必须显式 utf-8：Windows 中文区域默认 GBK 会把 ffprobe 的
            # UTF-8 输出解码成非法转义序列，导致含中文路径的文件全部 MED002
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30,
                check=True, encoding="utf-8", errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            data: dict[str, Any] = json.loads(proc.stdout)
        except (subprocess.SubprocessError, json.JSONDecodeError, OSError) as exc:
            raise AppError(ERR_MED_PROBE_FAILED, "媒体信息解析失败", cause=exc) from exc

        streams = data.get("streams", [])
        fmt = data.get("format", {})
        video = next((s for s in streams if s.get("codec_type") == "video"), None)
        audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
        subtitle = next((s for s in streams if s.get("codec_type") == "subtitle"), None)
        if video is None and audio is None:
            raise AppError(ERR_MED_PROBE_FAILED, "非媒体文件或无可用流")

        try:
            info = MediaInfo(
                path=str(path),
                duration_s=float(fmt.get("duration", 0.0) or 0.0),
                width=int(video.get("width", 0)) if video else 0,
                height=int(video.get("height", 0)) if video else 0,
                fps=_fps_from_fraction(video.get("r_frame_rate", "0/1")) if video else 0.0,
                vcodec=str(video.get("codec_name", "")) if video else "",
                acodec=str(audio.get("codec_name", "")) if audio else "",
                has_audio=audio is not None,
                soft_subtitle_codec=(
                    str(subtitle.get("codec_name", "")) if subtitle else ""
                ),
                size_bytes=int(fmt.get("size", 0) or 0),
                format_name=str(fmt.get("format_name", "")),
            )
        except (TypeError, ValueError) as exc:
            raise AppError(ERR_MED_PROBE_FAILED, "媒体字段解析异常", cause=exc) from exc
        logger.debug("probed %s: %sx%s %.2fs", path.name, info.width, info.height,
                     info.duration_s)
        return info
