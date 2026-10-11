# 帧级修复管线（详设 13.2 FrameLevelProcessor）：检测→流式修复→耗时守卫
from __future__ import annotations

import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import cv2
import numpy as np
import numpy.typing as npt

from ych.common.cancellation import (
    CancellationToken,
    ProgressFn,
    TaskCanceled,
)
from ych.common.errors import ERR_MED_TRANSCODE_FAILED, AppError
from ych.common.schemas import BBox, MediaInfo
from ych.core.m2_preprocess.filter_only_processor import build_vf_chain
from ych.core.m2_preprocess.ops import PreprocessOps
from ych.core.m2_preprocess.region_temporal import RegionTemporal, manual_spans
from ych.services.s1_media.encoder_spec import EncoderSpec
from ych.services.s1_media.ffmpeg_runner import FFmpegRunner
from ych.services.s1_media.frame_extractor import FrameExtractor
from ych.services.s1_media.probe_service import ProbeService
from ych.services.s2_ai.postprocess import RegionSpan
from ych.services.s2_ai.provider import InferenceProvider
from ych.services.s5_base.config_service import ConfigService

logger = logging.getLogger("ych.m2")

# 静态区缓存刷新周期（帧）；水印/台标位置基本不动，贴缓存可大幅省推理
CACHE_REFRESH_FRAMES = 30
# 紧凑笔画掩码的采用/回退阈值：框内笔画占比低于下限视为该帧无内容
# （字幕空档/背景误检），不推理不贴片；高于上限视为致密内容（实心
# 台标），整框修复才有意义
_STROKE_MIN_COVER = 0.004
_STROKE_DENSE_COVER = 0.65
# 框内与周边环境的中值灰度差阈值：超过视为实心覆盖物（回退整框修复）
_BOX_SHIFT_DENSE = 25.0
# 自适应刷新：当前帧与刷新帧的框内平均差超过该值即重推理（背景运动大，
# 旧修复贴片会穿帮）；静止画面则吃满缓存周期，画质与耗时自动折中
_REFRESH_DIFF = 10.0

# 缓存条目：（修复帧, 刷新时原图框, 修复方式）
#   tight=紧凑笔画掩码修复 / box=整框修复 / none=该帧无可辨识内容
_CacheEntry = tuple[npt.NDArray[np.uint8], npt.NDArray[np.uint8], str]


@dataclass
class RepairStats:
    """修复统计（测试断言缓存命中用）。"""

    spans: int = 0
    inpaint_calls: int = 0
    cache_hits: int = 0
    cache_refreshes: int = 0
    frames: int = 0
    extra: dict[str, float] = field(default_factory=dict)


def pixel_rect(bbox: object, width: int, height: int) -> tuple[int, int, int, int]:
    """归一化 BBox → 像素 (x0, y0, x1, y1)，clamp 到画面内且至少 1px。"""
    x = float(bbox.x)   # type: ignore[attr-defined]
    y = float(bbox.y)   # type: ignore[attr-defined]
    w = float(bbox.w)   # type: ignore[attr-defined]
    h = float(bbox.h)   # type: ignore[attr-defined]
    x0 = min(max(round(x * width), 0), max(width - 1, 0))
    y0 = min(max(round(y * height), 0), max(height - 1, 0))
    x1 = min(max(round((x + w) * width), x0 + 1), width)
    y1 = min(max(round((y + h) * height), y0 + 1), height)
    return x0, y0, x1, y1


def _tight_stroke_mask(box: npt.NDArray[np.uint8]) -> npt.NDArray[np.uint8]:
    """框内紧凑笔画掩码：只标记文字/水印笔画，背景纹理保持原样。

    以逐通道中值滤波估计局部背景（窗口远大于笔画宽度），取三通道
    偏差的最大值作为笔画响应——灰度差会漏掉"亮度相近但色度不同"
    的文字（黄字压沙地、红字压草地），色差不会；形态学开运算去噪
    点、膨胀覆盖笔画边缘。
    """
    h, w = box.shape[:2]
    k = min(21, max(3, (min(h, w) // 2) * 2 + 1))
    bg = cv2.medianBlur(box, k) if k >= 3 else box
    diff = np.abs(box.astype(np.int16) - bg.astype(np.int16)).max(axis=2)
    diff = diff.astype(np.uint8)
    if float(diff.max()) < 12.0:
        return np.zeros((h, w), dtype=np.uint8)   # 纯背景/不可辨识内容
    t_otsu, _ = cv2.threshold(diff, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # 阈值自适应：Otsu 会被框内强结构边界（如手/天空交线）抬得过高，
    # 漏掉半透明文字（差值 20~45）。内容主导直方图（p90>60）→ 直接用
    # Otsu；背景主导（笔画稀疏）→ 用 p85×2.2——平滑背景的噪声顶在
    # p85 附近，笔画差值成倍于此，能整段收进掩码
    p90 = float(np.percentile(diff, 90))
    p85 = float(np.percentile(diff, 85))
    c_bg = p85 * 2.2
    thr = max(8.0, min(float(t_otsu), 35.0, c_bg if p90 <= 60.0 else 35.0))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    mask = cv2.morphologyEx((diff > thr).astype(np.uint8) * 255,
                            cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE,
                            cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7)))
    # 膨胀 3 次（±3px）：盖住文字抗锯齿光晕，否则修复后残留淡色字影
    dil = cv2.dilate(mask, kernel, iterations=3)
    return _fill_holes(cast(npt.NDArray[np.uint8], dil))


def temporal_stroke_mask(
    patches: list[npt.NDArray[np.uint8]],
) -> npt.NDArray[np.uint8] | None:
    """跨采样帧的时间一致性笔画掩码（静态水印/字幕专用，可返回 None）。

    原理：静态水印在每次采样中都是同位置的离群点，对逐采样帧分别求
    "与局部中值的逐通道色差"后取逐像素中值——静态笔画的响应始终高
    （中值保持高水平），背景噪声与运动结构只在个别帧出现（中值被压
    回噪声水平）；再要求离群方向跨帧一致（始终偏亮或始终偏暗），排
    除静止的场景边缘。这是单帧阈值法无法覆盖的淡笔画（与背景差值
    10~30）的补充证据来源。样本 <3 或无可辨识笔画时返回 None。
    """
    if len(patches) < 3:
        return None
    h, w = patches[0].shape[:2]
    k = min(21, max(3, (min(h, w) // 2) * 2 + 1))
    diffs: list[npt.NDArray[np.uint8]] = []
    brighter: list[npt.NDArray[np.bool_]] = []
    for p in patches:
        bg = cv2.medianBlur(p, k) if k >= 3 else p
        d = np.abs(p.astype(np.int16) - bg.astype(np.int16)).max(axis=2)
        diffs.append(d.astype(np.uint8))
        brighter.append(
            p.astype(np.int16).sum(axis=2) > bg.astype(np.int16).sum(axis=2))
    med_resp: npt.NDArray[np.uint8] = np.median(
        np.stack(diffs), axis=0).astype(np.uint8)
    if float(med_resp.max()) < 8.0:
        return None
    t_otsu, _ = cv2.threshold(med_resp, 0, 255,
                              cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    thr = max(8.0, min(float(t_otsu), 45.0))
    sign = np.stack(brighter).mean(axis=0)
    mask = ((med_resp > thr)
            & ((sign >= 0.7) | (sign <= 0.3))).astype(np.uint8) * 255
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    # 带状闭运算+膨胀：把整条文字带连成实心掩码。稀疏笔画掩码会让
    # LaMa 把笔画缝隙里残留的文字当上下文"续"进修复结果（残影），
    # 实心带让模型从带外真实背景重建整条区域
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE,
                            cv2.getStructuringElement(cv2.MORPH_RECT, (25, 25)))
    # 带状闭运算+膨胀：把整条文字带连成实心掩码。稀疏笔画掩码会让
    # LaMa 把笔画缝隙里残留的文字当上下文"续"进修复结果（残影），
    # 实心带让模型从带外真实背景重建整条区域。膨胀须越过半透明水
    # 印的光晕（实测需 ~30px 半径）：边界切在光晕上时泊松/模型会把
    # 亮环向外延续，填充成白色块状伪影
    dil = cv2.dilate(mask,
                     cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (65, 65)),
                     iterations=1)
    mask = _fill_holes(cast(npt.NDArray[np.uint8], dil))
    if np.count_nonzero(mask) < 0.002 * mask.size:
        return None
    return mask


def _feather3(mask: npt.NDArray[np.uint8]) -> npt.NDArray[np.float32]:
    """掩码 → (h,w,1) 羽化系数（0..1），贴片边缘无硬接缝。"""
    f = cv2.GaussianBlur(mask, (5, 5), 0).astype(np.float32) / 255.0
    return f[..., np.newaxis]


def _fill_holes(mask: npt.NDArray[np.uint8]) -> npt.NDArray[np.uint8]:
    """填补掩码内部封闭空洞（不与外界连通的 0 区域置 255）。

    致密实心文字的局部中值会被文字自身主导（窗口内过半是文字色），
    字芯处响应≈0，掩码中心出现空洞——LaMa 只填掩码，洞里的原字迹
    直接透回（修复后残留色块/字影的主因之一）。
    """
    h, w = mask.shape
    pad = np.zeros((h + 2, w + 2), dtype=np.uint8)
    pad[1:-1, 1:-1] = mask
    ff = pad.copy()
    cv2.floodFill(ff, None, (0, 0), 255)   # type: ignore[call-overload]
    pad[ff == 0] = 255
    return pad[1:-1, 1:-1]


def _box_context_shift(frame: npt.NDArray[np.uint8],
                       x0: int, y0: int, x1: int, y1: int) -> float:
    """框内与紧邻周边环境的中值灰度差（实心覆盖物检测）。"""
    pad = max(6, min(24, (x1 - x0 + y1 - y0) // 4))
    ex0, ey0 = max(0, x0 - pad), max(0, y0 - pad)
    ex1, ey1 = min(frame.shape[1], x1 + pad), min(frame.shape[0], y1 + pad)
    ring = frame[ey0:ey1, ex0:ex1]
    ring_mask = np.ones(ring.shape[:2], dtype=bool)
    ring_mask[y0 - ey0:y1 - ey0, x0 - ex0:x1 - ex0] = False
    if int(ring_mask.sum()) < 48:
        return 0.0   # 周边环境不足（框贴边）：放弃环境判定
    box_med = float(np.median(
        cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)))
    ring_gray = cv2.cvtColor(ring, cv2.COLOR_BGR2GRAY)
    ring_med = float(np.median(ring_gray[ring_mask]))
    return abs(box_med - ring_med)


def _solid_overlay(frame: npt.NDArray[np.uint8],
                   x0: int, y0: int, x1: int, y1: int) -> bool:
    """框内是否为"实心覆盖物"（均匀色块/实心台标）。

    检测框通常比内容大一圈，框内会混入背景纹理（内部方差判据失效），
    故用灰度直方图主色占比判定：单一灰度覆盖过半且与框外环境存在
    整体亮度差（≥25）→ 实心内容。实心块对紧凑笔画掩码不可见（局部
    中值差≈0），只能整框修复；字幕/文字框主色占比低，不受影响。
    """
    gray = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    hist = np.bincount(gray.ravel(), minlength=256)
    if float(hist.max()) / gray.size < 0.5:
        return False
    return _box_context_shift(frame, x0, y0, x1, y1) >= _BOX_SHIFT_DENSE


def repair_frame(
    frame: npt.NDArray[np.uint8],
    ts: float,
    spans: list[RegionSpan],
    provider: InferenceProvider,
    cache: dict[int, _CacheEntry],
    last_refresh: dict[int, int],
    frame_idx: int,
    size: tuple[int, int],
    stats: RepairStats,
) -> npt.NDArray[np.uint8]:
    """单帧修复纯逻辑（独立函数便于无 ffmpeg 单测）。

    修复语义（2026-10 画质重构）：不再把整个检测框当掩码重画——那会
    产生整块糊片并随缓存贴片在运动背景上穿帮。现按帧在框内求紧凑
    笔画掩码，只重画笔画像素：
      1. 框内笔画占比正常 → 紧凑修复，笔画间背景为当前帧真实像素；
      2. 笔画占比过低（字幕空档/背景误检）→ auto 区域整帧不贴片；
      3. 笔画致密（实心台标）或手动框选失效时 → 回退整框修复；
      4. 贴片仅作用于掩码像素且羽化边缘，其余像素永远取当前帧。
    缓存 + 自适应刷新保持推理频率与旧实现同量级。
    """
    stats.frames += 1
    # frombuffer 产物为只读视图，回贴需要可写副本
    if not frame.flags.writeable:
        frame = np.array(frame, dtype=np.uint8, copy=True)
    for si, span in enumerate(spans):
        if not (span.t_start <= ts <= span.t_end):
            continue
        x0, y0, x1, y1 = pixel_rect(span.bbox, size[0], size[1])
        box = frame[y0:y1, x0:x1]
        if box.size == 0:
            continue
        entry = cache.get(si)
        if entry is not None:
            moved = float(np.abs(
                box.astype(np.int16) - entry[1].astype(np.int16),
            ).mean())
        else:
            moved = 255.0
        need_refresh = (
            entry is None
            or frame_idx - last_refresh.get(si, -10**9) >= CACHE_REFRESH_FRAMES
            or moved > _REFRESH_DIFF
        )
        if need_refresh:
            # 时间一致性掩码的使用前提：它与当前帧的紧凑掩码吻合（静态
            # 水印吻合；轮播字幕的时间掩码盖的是别的行，重叠率低 → 自
            # 动回退逐帧路径）。吻合判定同时兼容"当前帧笔画为零"（静
            # 止背景上的静态水印，逐帧掩码本来就空）
            use_temporal = False
            tmask = span.temporal_mask
            if tmask is not None and tmask.shape == box.shape[:2]:
                tight_now = _tight_stroke_mask(box)
                t_area = int(np.count_nonzero(tight_now))
                inter = int(np.count_nonzero(
                    cv2.bitwise_and(tmask, tight_now)))
                use_temporal = t_area == 0 or inter >= 0.5 * t_area
            if use_temporal:
                mask = np.zeros(frame.shape[:2], dtype=np.uint8)
                mask[y0:y1, x0:x1] = tmask
                kind = "temporal"
            else:
                tight = _tight_stroke_mask(box)
                cover = float(np.count_nonzero(tight)) / tight.size
                # 实心覆盖物（均匀色块/台标）对笔画掩码不可见 → 整框修
                # 复；否则按笔画占比分派：致密→整框，过疏→按 manual/
                # 背景误检分派
                if _solid_overlay(frame, x0, y0, x1, y1) or \
                        cover >= _STROKE_DENSE_COVER:
                    mask = np.zeros(frame.shape[:2], dtype=np.uint8)
                    mask[y0:y1, x0:x1] = 255
                    kind = "box"
                elif cover < _STROKE_MIN_COVER:
                    if span.manual:
                        # 手动框选遵循用户意图：框内内容视为待清除
                        mask = np.zeros(frame.shape[:2], dtype=np.uint8)
                        mask[y0:y1, x0:x1] = 255
                        kind = "box"
                    else:
                        # 该帧无可辨识笔画：不推理不贴片（auto 语义）
                        cache[si] = (np.array(frame, copy=True),
                                     np.array(box, copy=True), "none")
                        last_refresh[si] = frame_idx
                        continue
                else:
                    mask = np.zeros(frame.shape[:2], dtype=np.uint8)
                    mask[y0:y1, x0:x1] = tight
                    kind = "tight"
            repaired = provider.inpaint(frame, mask)
            cache[si] = (np.array(repaired, dtype=np.uint8, copy=True),
                         np.array(box, dtype=np.uint8, copy=True), kind)
            last_refresh[si] = frame_idx
            stats.inpaint_calls += 1
            stats.cache_refreshes += 1
            entry = cache[si]
        else:
            stats.cache_hits += 1
        if entry is None:   # 不可达：need_refresh 已覆盖 None 情形（防御）
            continue
        repaired, _orig, kind = entry
        if kind == "none":
            continue
        if kind == "box":
            f = _feather3(np.full(box.shape[:2], 255, dtype=np.uint8))
        elif kind == "temporal" and span.temporal_mask is not None:
            tmask = span.temporal_mask
            if tmask.shape != box.shape[:2]:
                continue
            f = _feather3(tmask)
        else:
            tight_now = _tight_stroke_mask(box)
            if not tight_now.any():
                continue   # 当前帧笔画已消失（如字幕切换间隙）
            f = _feather3(tight_now)
        rep_box = repaired[y0:y1, x0:x1].astype(np.float32)
        cur = box.astype(np.float32)
        frame[y0:y1, x0:x1] = (rep_box * f + cur * (1.0 - f)).astype(np.uint8)
    return frame


class FrameLevelProcessor:
    """阶段A 检测（抽样 ≤detect_sample_frames）→ 阶段B run_pipe 流式修复 → 阶段C 守卫。"""

    def __init__(
        self,
        runner: FFmpegRunner,
        extractor: FrameExtractor,
        provider: InferenceProvider,
        config: ConfigService,
        prober: ProbeService | None = None,
    ) -> None:
        self._runner = runner
        self._extractor = extractor
        self._provider = provider
        self._config = config
        self._prober = prober   # 分段直通查关键帧用；缺省则禁用该优化

    def process(
        self,
        src: Path,
        probe: MediaInfo,
        ops: PreprocessOps,
        out_target: Path,
        on_progress: ProgressFn | None,
        token: CancellationToken,
        spans: list[RegionSpan] | None = None,
    ) -> RepairStats:
        t_start = time.monotonic()
        duration_s = max(probe.duration_s, 1e-6)

        # spans 可由管线预检传入（空区域降级判定复用同一次检测，避免双跑）
        t_detect = time.monotonic()
        if spans is None:
            spans = self.detect_spans(src, probe, ops, token, on_progress)
        detect_s = time.monotonic() - t_detect

        vf = build_vf_chain(ops, probe)
        if spans and self._segmented_applicable(spans, probe, vf, duration_s):
            try:
                return self._process_segmented(
                    src, probe, ops, out_target, spans, on_progress, token,
                    duration_s, t_start,
                )
            except TaskCanceled:
                raise
            except Exception:
                # 分段直通任何失败都回退整片流水线：可用性优先，代价只是
                # 这条素材没享受到提速（原因已记日志，产物质量不受影响）
                logger.exception("分段直通失败，回退整片逐帧流水线：%s", src.name)

        stats = RepairStats()
        stats.spans = len(spans)
        if on_progress is not None:
            on_progress(0.15)

        fps = max(probe.fps, 1.0)
        total_frames = max(1, int(duration_s * fps))
        vf = build_vf_chain(ops, probe)
        encode_args = [
            "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-s", f"{probe.width}x{probe.height}", "-r", f"{fps:.6f}",
            "-i", "pipe:0",
        ]
        keep_audio = probe.has_audio and not ops.strip_audio
        if keep_audio:
            # 原声来自第二个输入（rawvideo 管道只有画面）：
            # map 0:v 管道视频 + map 1:a? 源音频，音频缺失时不报错
            encode_args += ["-i", str(src), "-map", "0:v", "-map", "1:a?"]
        if vf:
            encode_args += ["-vf", vf]
        encode_args += EncoderSpec().to_args(with_audio=keep_audio)
        encode_args += ["-f", "mp4", str(out_target)]

        cache: dict[int, _CacheEntry] = {}
        last_refresh: dict[int, int] = {}
        idx_box = [0]

        repair_s = [0.0]

        def repair_cb(frame: npt.NDArray[np.uint8]) -> npt.NDArray[np.uint8]:
            idx = idx_box[0]
            idx_box[0] += 1
            ts = idx / max(fps, 1e-6)
            t0 = time.perf_counter()
            try:
                return repair_frame(
                    frame, ts, spans, self._provider,
                    cache, last_refresh, idx, (probe.width, probe.height),
                    stats,
                )
            finally:
                repair_s[0] += time.perf_counter() - t0

        def wrapped(ratio: float) -> None:
            if on_progress is not None:
                on_progress(0.15 + min(max(ratio, 0.0), 1.0) * 0.85)

        logger.info("帧级路径：%d 个修复区域，输出 %s", len(spans), out_target.name)
        t_pipe = time.monotonic()
        self._runner.run_pipe(
            decode_args=["-i", str(src)],
            encode_args=encode_args,
            frame_cb=repair_cb,
            total_frames=total_frames,
            on_progress=wrapped,
            token=token,
            frame_size=(probe.width, probe.height),
            fps=fps,
        )
        pipe_s = time.monotonic() - t_pipe
        logger.info(
            "帧级路径耗时分解：检测 %.1fs + 流水线 %.1fs"
            "（其中修复累计 %.1fs / %d 帧）",
            detect_s, pipe_s, repair_s[0], stats.frames,
        )

        # 阶段C：耗时守卫（超预算记 WARN 不中断）
        elapsed = time.monotonic() - t_start
        stats.extra["elapsed_s"] = elapsed
        if elapsed > 2.0 * duration_s:
            logger.warning(
                "性能预算超标：帧级处理 %.1fs > 2×时长 %.1fs（%s）",
                elapsed, 2.0 * duration_s, src.name,
            )
        return stats

    def _run_or_raise(
        self, args: list[str], what: str, token: CancellationToken,
    ) -> None:
        """执行 ffmpeg 命令，非零退出时带 stderr 尾部抛错（可诊断）。"""
        tail: list[str] = []
        code = self._runner.run(args, token=token, on_line=tail.append)
        if code != 0:
            detail = " | ".join(tail[-3:])
            raise AppError(
                ERR_MED_TRANSCODE_FAILED, f"{what}失败（exit={code}）：{detail}",
            )

    def _segmented_applicable(
        self,
        spans: list[RegionSpan],
        probe: MediaInfo,
        vf: str,
        duration_s: float,
    ) -> bool:
        """分段直通门控：条件不满足一律走整片流水线（宁慢勿错）。"""
        if self._prober is None or vf:
            # 滤镜链会改分辨率/像素参数，与拷贝段混拼破坏 concat 兼容性
            return False
        if probe.vcodec != "h264" or probe.fps <= 0 or duration_s < 2.0:
            return False
        if probe.avg_fps and abs(probe.avg_fps - probe.fps) > 0.02 * probe.fps:
            return False   # VFR：拷贝段保 VFR、修复段恒定帧率，拼接会顿挫
        covered = 0.0
        cur_end = -1.0
        for s in sorted(spans, key=lambda s: s.t_start):
            a, b = max(0.0, s.t_start), min(duration_s, s.t_end)
            if b <= a:
                continue
            if a > cur_end:
                covered += b - a
                cur_end = b
            elif b > cur_end:
                covered += b - cur_end
                cur_end = b
        unrepaired = duration_s - covered
        # 未修复占比不足（或太短）时分段收益覆盖不了切分开销
        return unrepaired >= 0.25 * duration_s and unrepaired >= 0.5

    def _process_segmented(
        self,
        src: Path,
        probe: MediaInfo,
        ops: PreprocessOps,
        out_target: Path,
        spans: list[RegionSpan],
        on_progress: ProgressFn | None,
        token: CancellationToken,
        duration_s: float,
        t_start: float,
    ) -> RepairStats:
        """分段直通：修复段走逐帧管线，无修复段 -c:v copy 比特级原样保留。

        各段 H.264 MP4 经 concat demuxer 无损拼接；音轨最后整条取自原片
        （AAC 直通，非 AAC 才统一转码一次）——拷贝段零重压缩、原声比特级
        保留，画质严格优于整片重编码。
        """
        stats = RepairStats()
        stats.spans = len(spans)
        fps = max(probe.fps, 1.0)
        kfs = self._prober.keyframe_ts(Path(src))   # type: ignore[union-attr]
        cuts = sorted({0.0, duration_s, *(k for k in kfs if 0.0 < k < duration_s)})

        def snap_start(t: float) -> float:
            return max((c for c in cuts if c <= t + 1e-6), default=0.0)

        def snap_end(t: float) -> float:
            return min((c for c in cuts if c >= t - 1e-6), default=duration_s)

        # 修复段：span 按关键帧外扩（起点向左、终点向右），相邻合并
        repairs: list[list[float]] = []
        for s in sorted(spans, key=lambda s: s.t_start):
            a = snap_start(max(0.0, s.t_start))
            b = min(snap_end(min(duration_s, s.t_end)), duration_s)
            if b - a <= 1e-6:
                continue
            if repairs and a <= repairs[-1][1] + 1e-6:
                repairs[-1][1] = max(repairs[-1][1], b)
            else:
                repairs.append([a, b])
        if not repairs:
            raise AppError(ERR_MED_TRANSCODE_FAILED, "分段切点解析失败")

        segs: list[tuple[str, float, float]] = []
        cur = 0.0
        for a, b in repairs:
            if a - cur > 1e-6:
                segs.append(("copy", cur, a))
            segs.append(("repair", a, b))
            cur = b
        if duration_s - cur > 1e-6:
            segs.append(("copy", cur, duration_s))

        seg_dir = out_target.parent / f"{out_target.name}.segs"
        seg_dir.mkdir(parents=True, exist_ok=True)
        try:
            parts: list[Path] = []
            done_w = 0.0
            for i, (kind, a, b) in enumerate(segs):
                token.check()
                seg_path = seg_dir / f"seg{i:03d}.mp4"
                w = (b - a) / duration_s
                if kind == "copy":
                    # 按帧数精确截断：-t 走 DTS 域，B 帧重排会多带帧导致段重叠
                    n_frames = max(1, round((b - a) * fps))
                    self._run_or_raise([
                        "-y", "-ss", f"{a:.6f}", "-i", str(src),
                        "-frames:v", str(n_frames),
                        "-map", "0:v:0", "-c:v", "copy", "-an",
                        "-avoid_negative_ts", "make_zero",
                        "-f", "mp4", str(seg_path),
                    ], "拷贝段", token)
                    parts.append(seg_path)
                    done_w += w
                    if on_progress is not None:
                        on_progress(min(0.99, done_w))
                else:
                    def wrapped(
                        ratio: float, _w: float = w, _d: float = done_w,
                    ) -> None:
                        if on_progress is not None:
                            on_progress(min(
                                0.99, _d + min(max(ratio, 0.0), 1.0) * _w,
                            ))

                    self._run_repair_segment(
                        src, probe, ops, seg_path, a, b, fps, spans,
                        token, stats, wrapped,
                    )
                    parts.append(seg_path)
                    done_w += w

            # 无损拼接（video-only）
            concat_path = seg_dir / "concat.mp4"
            list_file = seg_dir / "concat.txt"
            list_file.write_text(
                "".join(f"file '{p.name}'\n" for p in parts),
                encoding="utf-8",
            )
            self._run_or_raise([
                "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
                "-c", "copy", "-movflags", "+faststart",
                "-f", "mp4", str(concat_path),
            ], "分段拼接", token)

            keep_audio = probe.has_audio and not ops.strip_audio
            if keep_audio:
                a_args = (["-c:a", "copy"] if probe.acodec == "aac" else
                          ["-c:a", "aac", "-b:a", "128k", "-ar", "44100",
                           "-ac", "2"])
                self._run_or_raise([
                    "-y", "-i", str(concat_path), "-i", str(src),
                    "-map", "0:v:0", "-map", "1:a:0?", "-c:v", "copy",
                    *a_args, "-shortest", "-movflags", "+faststart",
                    "-f", "mp4", str(out_target),
                ], "音轨封装", token)
            else:
                shutil.copyfile(concat_path, out_target)

            copied_s = sum(b - a for k, a, b in segs if k == "copy")
            stats.extra["copied_s"] = round(copied_s, 2)
            stats.extra["segments"] = len(segs)
            logger.info(
                "分段直通：%d 段（拷贝 %.1fs + 修复 %.1fs），输出 %s",
                len(segs), copied_s, duration_s - copied_s, out_target.name,
            )
            stats.extra["elapsed_s"] = time.monotonic() - t_start
            return stats
        finally:
            shutil.rmtree(seg_dir, ignore_errors=True)

    def _run_repair_segment(
        self,
        src: Path,
        probe: MediaInfo,
        ops: PreprocessOps,
        seg_path: Path,
        seg_start: float,
        seg_end: float,
        fps: float,
        spans: list[RegionSpan],
        token: CancellationToken,
        stats: RepairStats,
        on_progress: ProgressFn | None,
    ) -> None:
        """单修复段：解码 [start,end) → 逐帧修复 → video-only 编码。"""
        total_frames = max(1, round((seg_end - seg_start) * fps))
        encode_args = [
            "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-s", f"{probe.width}x{probe.height}", "-r", f"{fps:.6f}",
            "-i", "pipe:0", "-an",
        ]
        encode_args += EncoderSpec().to_args(with_audio=False)
        encode_args += ["-f", "mp4", str(seg_path)]

        cache: dict[int, _CacheEntry] = {}
        last_refresh: dict[int, int] = {}
        idx_box = [0]

        def repair_cb(frame: npt.NDArray[np.uint8]) -> npt.NDArray[np.uint8]:
            idx = idx_box[0]
            idx_box[0] += 1
            ts = seg_start + idx / max(fps, 1e-6)
            return repair_frame(
                frame, ts, spans, self._provider,
                cache, last_refresh, idx, (probe.width, probe.height),
                stats,
            )

        self._runner.run_pipe(
            decode_args=[
                "-ss", f"{seg_start:.6f}", "-t", f"{seg_end - seg_start:.6f}",
                "-i", str(src),
            ],
            encode_args=encode_args,
            frame_cb=repair_cb,
            total_frames=total_frames,
            on_progress=on_progress,
            token=token,
            frame_size=(probe.width, probe.height),
            fps=fps,
        )

    def detect_spans(
        self,
        src: Path,
        probe: MediaInfo,
        ops: PreprocessOps,
        token: CancellationToken,
        on_progress: ProgressFn | None,
    ) -> list[RegionSpan]:
        """auto：抽帧（fps≈2、cap=detect_sample_frames）→ 检测 → 时域稳定。

        manual：直接由 ManualRegions 构造全程 span，不经聚类。
        自动区域随后做跨帧时间一致性采样，为静态水印/字幕生成
        temporal_mask（单帧阈值法漏掉的淡笔画靠它兜底）。
        """
        spans: list[RegionSpan] = []
        need_sampling = (
            (ops.remove_watermark_mode == "auto")
            or (ops.remove_subtitle_mode == "auto")
        )
        pairs: list[tuple[float, npt.NDArray[np.uint8]]] = []
        ts_list: list[float] = []
        if need_sampling:
            cap = self._config.get_typed("detect_sample_frames", int)
            frames = self._extractor.uniform(src, fps=2.0, max_frames=cap, token=token)
            pairs = [(f.ts, f.img) for f in frames]
            ts_list = [t for t, _img in pairs]
            if on_progress is not None:
                on_progress(0.05)

        if ops.remove_watermark_mode == "auto" and pairs:
            per_frame = self._provider.detect_watermark(pairs)
            # 水印出现率门槛低于字幕：半透明水印在部分采样帧会低于
            # YOLO 置信度（实测 ~0.53），0.6 会把整个水印簇过滤掉；
            # 0.45 仍能过滤单帧闪烁误检
            spans += RegionTemporal.build_spans(per_frame, ts_list, 2.0,
                                                presence_ratio=0.45)
        if ops.remove_subtitle_mode == "auto" and pairs:
            per_frame = self._provider.detect_subtitle(pairs)
            spans += RegionTemporal.build_spans(per_frame, ts_list, 2.0)
        # 自动框外扩 12%：聚类框来自抽样帧，实际文字位置逐帧有出入
        # （行宽变化/淡入位移），不外扩会漏掉行列首尾的字
        for s in spans:
            gx, gy = s.bbox.w * 0.12, s.bbox.h * 0.12
            s.bbox = BBox(
                x=max(0.0, s.bbox.x - gx),
                y=max(0.0, s.bbox.y - gy),
                w=min(1.0, s.bbox.w + 2 * gx),
                h=min(1.0, s.bbox.h + 2 * gy),
            )
        spans += manual_spans(ops.watermark_regions, probe.duration_s)
        spans += manual_spans(ops.subtitle_regions, probe.duration_s)
        # 时间一致性掩码挂所有自动区域；是否采用由修复时逐帧验证
        # （时间掩码须与当前帧紧凑掩码吻合，轮播字幕自动回退逐帧）
        self._attach_temporal_masks(src, probe, spans, token)
        return spans

    def _attach_temporal_masks(
        self,
        src: Path,
        probe: MediaInfo,
        spans: list[RegionSpan],
        token: CancellationToken,
    ) -> None:
        """为自动检测的静态区域生成时间一致性掩码（按区域时段采样）。

        半透明白字压在明亮背景上时，单帧色差（12~45）与背景噪声难分，
        逐帧紧凑掩码会漏；同一位置跨时间取多帧样本，静态文字在每次采
        样中都是同位置离群点，时间一致性证据能把整段笔画收进掩码。
        任一环节失败只放弃该优化（退回逐帧紧凑掩码），不阻断检测。
        """
        auto_spans = [s for s in spans
                      if not s.manual and s.t_end - s.t_start >= 2.0]
        if not auto_spans:
            return
        try:
            sampled: dict[int, npt.NDArray[np.uint8] | None] = {}

            def sample_at(ts: float) -> npt.NDArray[np.uint8] | None:
                key = round(ts * 10)
                if key in sampled:
                    return sampled[key]
                if token.cancelled:
                    return None
                img: npt.NDArray[np.uint8] | None = None
                for _attempt in range(2):   # seek 偶发失败重试一次
                    try:
                        img = self._extractor.single(src, ts).img
                        break
                    except Exception as exc:
                        logger.debug("时间掩码采样失败 ts=%.2f：%s", ts, exc)
                sampled[key] = img
                return img

            for s in auto_spans:
                x0, y0, x1, y1 = pixel_rect(s.bbox, probe.width, probe.height)
                if x1 - x0 < 8 or y1 - y0 < 8:
                    continue
                dur = s.t_end - s.t_start
                k = min(6, max(3, int(dur // 4) + 1))
                patches: list[npt.NDArray[np.uint8]] = []
                for i in range(k):
                    ts = s.t_start + dur * (i + 0.5) / k
                    img = sample_at(ts)
                    if img is not None:
                        patches.append(img[y0:y1, x0:x1])
                if len(patches) < 3:
                    continue
                mask = temporal_stroke_mask(patches)
                if mask is None or mask.shape != (y1 - y0, x1 - x0):
                    continue
                cover = float(np.count_nonzero(mask)) / mask.size
                # 覆盖率护栏：太弱（<3%）说明该区域文字随时间变化（如
                # 轮播字幕），时间证据不可靠 → 留给逐帧紧凑掩码；异常
                # 饱满（>90%）更像检测框本身就是内容块 → 交给实心判定
                if not 0.03 <= cover <= 0.90:
                    continue
                s.temporal_mask = mask
        except Exception:
            logger.debug("时间一致性掩码生成失败（退回逐帧掩码）", exc_info=True)



