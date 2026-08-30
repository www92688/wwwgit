# 纯滤镜管线（详设 13.1/13.2）：crop→(aspect crop|pad) 滤镜链 + 单条 ffmpeg 执行
from __future__ import annotations

import logging
from pathlib import Path

from ych.common.cancellation import CancellationToken, ProgressFn
from ych.common.errors import ERR_MED_TRANSCODE_FAILED, AppError
from ych.common.schemas import MediaInfo
from ych.core.m2_preprocess.ops import PreprocessOps
from ych.services.s1_media.encoder_spec import EncoderSpec
from ych.services.s1_media.ffmpeg_runner import FFmpegRunner

logger = logging.getLogger("ych.m2")


def even_down(n: int) -> int:
    """向下取偶（yuv420p 要求宽高为偶数）；最小 2。"""
    v = max(2, int(n))
    return v - (v % 2)


def build_vf_chain(ops: PreprocessOps, probe: MediaInfo) -> str:
    """纯函数拼装滤镜链：crop=w:h:x:y → scale/pad；全部像素取偶对齐。

    - crop_rect 归一化 → 像素并 clamp 到画面内；
    - aspect_target：crop 策略居中裁到目标比例 / pad 策略扩画布补黑边；
    - 无任何几何操作返回空串（调用方不挂 -vf）。
    """
    w, h = probe.width, probe.height
    if w <= 0 or h <= 0:
        return ""

    x0 = y0 = 0
    w0 = even_down(w)
    h0 = even_down(h)
    need_crop_filter = False

    rect = ops.crop_rect
    if rect is not None:
        cx = min(max(round(rect.x * w), 0), max(w - 2, 0))
        cy = min(max(round(rect.y * h), 0), max(h - 2, 0))
        cw = max(2, min(round(rect.w * w), w - cx))
        ch = max(2, min(round(rect.h * h), h - cy))
        x0, y0 = cx, cy
        w0, h0 = even_down(cw), even_down(ch)
        need_crop_filter = True

    ratio_t: float | None = None
    if ops.aspect_target is not None and ops.aspect_target[0] > 0:
        tw, th = ops.aspect_target
        ratio_t = tw / th

    parts: list[str] = []
    if ratio_t is not None and h0 > 0 and w0 > 0:
        ratio_c = w0 / h0
        if ops.aspect_strategy == "crop":
            if abs(ratio_c - ratio_t) > 1e-6:
                if ratio_c > ratio_t:      # 过宽 → 居中裁两侧
                    nw = max(2, even_down(int(h0 * ratio_t)))
                    x0 += (w0 - nw) // 2
                    w0 = nw
                else:                       # 过高 → 居中裁上下
                    nh = max(2, even_down(int(w0 / ratio_t)))
                    y0 += (h0 - nh) // 2
                    h0 = nh
                need_crop_filter = True
        else:   # pad：保持原像素，扩展画布补黑边
            if ratio_c < ratio_t:           # 太窄 → 左右加黑边
                ow = even_down(int(h0 * ratio_t))
                parts.append(f"pad={ow}:{h0}:{max((ow - w0) // 2, 0)}:0:black")
            elif ratio_c > ratio_t:         # 太扁 → 上下加黑边
                oh = even_down(int(w0 / ratio_t))
                parts.append(f"pad={w0}:{oh}:0:{max((oh - h0) // 2, 0)}:black")

    if need_crop_filter:
        parts.insert(0, f"crop={w0}:{h0}:{x0}:{y0}")
    return ",".join(parts)


class FilterOnlyProcessor:
    """单条 ffmpeg 完成 crop/比例/去原声（无帧级修复需求时）。"""

    def __init__(self, runner: FFmpegRunner) -> None:
        self._runner = runner

    def run(
        self,
        src: Path,
        probe: MediaInfo,
        ops: PreprocessOps,
        out_target: Path,
        on_progress: ProgressFn | None,
        token: CancellationToken | None,
    ) -> Path:
        vf = build_vf_chain(ops, probe)
        args = ["-i", str(src)]
        if vf:
            args += ["-vf", vf]
        args += EncoderSpec().to_args(with_audio=probe.has_audio and not ops.strip_audio)
        args += ["-f", "mp4", "-y", str(out_target)]

        duration = max(probe.duration_s, 1e-6)

        def on_line(line: str) -> None:
            if on_progress is None:
                return
            ts = FFmpegRunner.parse_progress_line(line)
            if ts is not None:
                on_progress(min(ts / duration, 1.0))

        logger.info("纯滤镜路径：%s", " ".join(args[:10]))
        code = self._runner.run(args, on_line=on_line, token=token)
        if code != 0:
            raise AppError(ERR_MED_TRANSCODE_FAILED, f"预处理转码失败（exit={code}）")
        if on_progress is not None:
            on_progress(1.0)
        return out_target
