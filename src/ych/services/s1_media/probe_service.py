# ffprobe 媒体信息探测（详设 8.3 字段映射）
from __future__ import annotations

import json
import logging
import subprocess
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

from ych.common.errors import ERR_MED_FORMAT_UNSUPPORTED, ERR_MED_PROBE_FAILED, AppError
from ych.common.schemas import SUPPORTED_EXTENSIONS, MediaInfo
from ych.services.s1_media.ffmpeg_runner import FFmpegRunner

logger = logging.getLogger("ych.s1")

_PROBE_CACHE_CAP = 256   # 进程内探测缓存条数（特征提取/去重对比反复 probe 同一文件）


def _fps_from_fraction(text: str) -> float:
    """r_frame_rate 形如 "10/1"、"30000/1001" → 浮点帧率。"""
    from fractions import Fraction

    try:
        return float(Fraction(text))
    except (ValueError, ZeroDivisionError):
        return 0.0


class ProbeService:
    """ffprobe JSON 解析 → MediaInfo；失败 MED002，扩展名不支持 MED003。

    按 (路径, mtime_ns, size) 做进程内缓存：特征提取与去重前后对比会对
    同一文件反复探测，子进程 ffprobe 每次数百毫秒；文件被替换时键变化
    自然失效，MediaInfo 本身不可变，可安全共享。
    """

    def __init__(self, runner: FFmpegRunner) -> None:
        self._runner = runner
        self._cache: OrderedDict[tuple[str, int, int], MediaInfo] = OrderedDict()
        self._cache_lock = threading.Lock()

    def probe(self, path: Path) -> MediaInfo:
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise AppError(
                ERR_MED_FORMAT_UNSUPPORTED,
                f"不支持的格式：{path.suffix}（支持 mp4/avi/mov/mkv/flv）",
            )
        cached = self._cache_get(path)
        if cached is not None:
            return cached
        info = self._probe_uncached(path)
        self._cache_put(path, info)
        return info

    # ---- 探测缓存 ----
    def _cache_key(self, path: Path) -> tuple[str, int, int] | None:
        try:
            st = Path(path).stat()
        except OSError:
            return None
        return (str(Path(path).resolve()), st.st_mtime_ns, st.st_size)

    def _cache_get(self, path: Path) -> MediaInfo | None:
        key = self._cache_key(path)
        if key is None:
            return None
        with self._cache_lock:
            info = self._cache.get(key)
            if info is not None:
                self._cache.move_to_end(key)
            return info

    def _cache_put(self, path: Path, info: MediaInfo) -> None:
        key = self._cache_key(path)
        if key is None:
            return
        with self._cache_lock:
            self._cache[key] = info
            self._cache.move_to_end(key)
            while len(self._cache) > _PROBE_CACHE_CAP:
                self._cache.popitem(last=False)

    def _probe_uncached(self, path: Path) -> MediaInfo:
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
                avg_fps=_fps_from_fraction(
                    video.get("avg_frame_rate", "0/1"),
                ) if video else 0.0,
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

    def keyframe_ts(self, path: Path) -> list[float]:
        """视频流关键帧 pts 列表（升序，供分段直通选切点）。

        解析失败/无关键帧返回空列表——调用方据此放弃分段优化，
        绝不让关键帧查询失败阻断正常处理。
        """
        _, ffprobe = self._runner.locate_binaries()
        cmd = [
            str(ffprobe), "-v", "quiet",
            "-select_streams", "v:0", "-skip_frame", "nokey",
            "-show_entries", "frame=pts_time",
            "-of", "csv=p=0", str(path),
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30,
                check=True, encoding="utf-8", errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            # ffprobe csv 偶发首行带尾逗号（"0.000000,"）：统一去逗号再解析，
            # 非数值行跳过——解析绝不抛错，失败仅放弃分段优化
            out: list[float] = []
            for line in proc.stdout.replace(",", " ").split():
                try:
                    out.append(float(line))
                except ValueError:
                    continue
            return sorted(out)
        except (subprocess.SubprocessError, OSError):
            logger.warning("关键帧列表查询失败：%s", path.name)
            return []
