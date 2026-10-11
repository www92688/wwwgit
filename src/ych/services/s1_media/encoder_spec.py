# 输出编码规范（详设 2.5，全局唯一）
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EncoderSpec:
    """统一 MP4 输出规格：libx264/crf18/faster + aac128k/44.1k + faststart。

    参数经 scripts/encoder_gate.py 质量门实测采纳：对源 SSIM 全面不低于
    旧规格（medium/crf20，motion 素材 +0.0006），编码提速约 1.31x。
    改动编码参数必须先跑该扫描，SSIM 对源不低于旧规格才允许变更。
    """

    vcodec: str = "libx264"
    crf: int = 18
    preset: str = "faster"
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
