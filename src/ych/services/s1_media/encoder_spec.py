# 输出编码规范（详设 2.5，全局唯一）
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EncoderSpec:
    """统一 MP4 输出规格：libx264/crf20/medium + aac128k/44.1k + faststart。"""

    vcodec: str = "libx264"
    crf: int = 20
    preset: str = "medium"
    pix_fmt: str = "yuv420p"
    acodec: str | None = "aac"
    abitrate_k: int = 128
    asr: int = 44100
    movflags: str = "+faststart"

    def to_args(self, with_audio: bool) -> list[str]:
        """生成 ffmpeg 编码参数；无音轨素材不写音频流（-an）。"""
        args = [
            "-c:v", self.vcodec,
            "-crf", str(self.crf),
            "-preset", self.preset,
            "-pix_fmt", self.pix_fmt,
        ]
        if with_audio and self.acodec:
            args += [
                "-c:a", self.acodec,
                "-b:a", f"{self.abitrate_k}k",
                "-ar", str(self.asr),
                "-ac", "2",
            ]
        else:
            args.append("-an")
        if self.movflags:
            args += ["-movflags", self.movflags]
        return args
