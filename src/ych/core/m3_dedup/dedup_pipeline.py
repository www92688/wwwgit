# 去重流水线（详设 14.6）：手法链 → 单条 ffmpeg → 前后重复度对比
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ych.common.cancellation import CancellationToken, ProgressFn, SkippedSignal
from ych.common.schemas import FeatureSet, MediaInfo
from ych.core.interfaces import IDownloadArchiveTarget
from ych.core.m3_dedup.similarity import SimilarityCalculator
from ych.core.m3_dedup.techniques.base import ClipContext
from ych.core.m3_dedup.techniques.registry import TechniqueRegistry
from ych.services.s1_media.encoder_spec import EncoderSpec
from ych.services.s1_media.ffmpeg_runner import FFmpegRunner
from ych.services.s1_media.probe_service import ProbeService
from ych.services.s3_db.daos import ReportDao

logger = logging.getLogger("ych.m3")

_DEDUP_SUFFIX = "_deduped"
_OUT_DIR_NAME = "已去重"


@dataclass
class DedupItemResult:
    out_path: Path
    before_pct: float | None = None      # 处理前综合重复度（%）
    after_pct: float | None = None       # 处理后对同批 targets 重算（%）


class _WorkDirsLike(Protocol):
    def workdir(self) -> Path: ...


_FeatureExtract = Callable[..., FeatureSet]


class DedupPipeline:
    """execute_item：SkippedSignal / 滤镜链执行 / before-after 对比 / 耗时守卫。"""

    def __init__(
        self,
        runner: FFmpegRunner,
        prober: ProbeService,
        archive: IDownloadArchiveTarget,
        workdirs: _WorkDirsLike,
        registry: TechniqueRegistry,
        feature_extract: _FeatureExtract | None = None,   # FeatureExtractService.extract
        calculator: SimilarityCalculator | None = None,
        reports: ReportDao | None = None,                 # latest_for
    ) -> None:
        self._runner = runner
        self._prober = prober
        self._archive = archive
        self._workdirs = workdirs
        self._registry = registry
        self._extract = feature_extract
        self._calc = calculator
        self._reports = reports

    # ---- 对外 ----
    def output_path_for(self, src: Path) -> Path:
        out = self._archive.mirror_path_for_output(
            src, _DEDUP_SUFFIX, out_root=self._workdirs.workdir() / _OUT_DIR_NAME,
        )
        return out.with_suffix(".mp4")

    def execute_item(
        self,
        src: Path,
        technique_params: list[dict[str, object]],
        on_progress: ProgressFn | None,
        token: CancellationToken | None = None,
    ) -> DedupItemResult:
        t0 = time.monotonic()
        probe = self._prober.probe(src)
        out = self.output_path_for(src)
        if out.exists():
            raise SkippedSignal(f"{out.name} 已存在，跳过重复去重")

        ctx = self._build_context(src, probe, technique_params)
        run_token = token if token is not None else CancellationToken()

        from ych.common.fsutil import SafeFileOps

        def writer(tmp_target: Path) -> None:
            self._run_ffmpeg(src, probe, ctx, tmp_target, on_progress, run_token)

        SafeFileOps.atomic_write(out, writer)
        self._prober.probe(out)          # success 语义：ffprobe 可读

        elapsed = time.monotonic() - t0
        dur_eff = max(probe.duration_s / max(ctx.speed_factor, 1e-6), 1e-6)
        if elapsed > 3.0 * dur_eff:
            logger.warning("耗时守卫：去重 %.1fs > 3×时长 %.1fs（%s）",
                           elapsed, 3.0 * dur_eff, src.name)

        before_pct, after_pct = self._compare_scores(src, out)
        logger.info("去重完成 %s：before=%s after=%s（%.1fs）",
                    out.name, before_pct, after_pct, elapsed)
        return DedupItemResult(out_path=out,
                               before_pct=before_pct,
                               after_pct=after_pct)

    # ---- 手法链 ----
    def _build_context(
        self,
        src: Path,
        probe: MediaInfo,
        technique_params: list[dict[str, object]],
    ) -> ClipContext:
        ctx = ClipContext(src=src, probe=probe)
        ids: list[str] = []
        params_by_id: dict[str, dict[str, object]] = {}
        for item in technique_params:
            tid = str(item.get("id"))
            raw = item.get("params") or {}
            assert isinstance(raw, dict)
            ids.append(tid)
            params_by_id[tid] = raw
        for technique in self._registry.ordered(ids):
            ctx = technique.apply(ctx, params_by_id[technique.id])
        return ctx

    # ---- 单条 ffmpeg ----
    def _run_ffmpeg(
        self,
        src: Path,
        probe: MediaInfo,
        ctx: ClipContext,
        out_target: Path,
        on_progress: ProgressFn | None,
        token: CancellationToken,
    ) -> None:
        speed = max(ctx.speed_factor, 1e-6)
        filters = list(ctx.vf_filters)
        filters.append(f"setpts=PTS/{speed:.6f}")
        keep_audio = ctx.keep_audio and probe.has_audio

        args = ["-i", str(ctx.src or src)]
        args += ["-vf", ",".join(filters)]
        if keep_audio and abs(speed - 1.0) > 1e-6:
            args += ["-af", f"atempo={speed:.6f}"]
        args += EncoderSpec().to_args(with_audio=keep_audio)
        args += ["-f", "mp4", "-y", str(out_target)]

        duration_eff = max(probe.duration_s / speed, 1e-6)

        from ych.common.errors import ERR_MED_TRANSCODE_FAILED, AppError

        def on_line(line: str) -> None:
            if on_progress is None:
                return
            ts = FFmpegRunner.parse_progress_line(line)
            if ts is not None and on_progress is not None:
                on_progress(min(ts / duration_eff, 1.0))

        code = self._runner.run(args, on_line=on_line, token=token)
        if code != 0:
            raise AppError(ERR_MED_TRANSCODE_FAILED, f"去重转码失败（exit={code}）")
        if on_progress is not None:
            on_progress(1.0)

    # ---- 前后重复度对比（14.6 第 4 步）----
    def _compare_scores(self, src: Path, out: Path) -> tuple[float | None, float | None]:
        """复用 ReportDao.latest_for(src) 的同批 targets 重算；不可比时返回 (None, None)。"""
        if self._extract is None or self._calc is None or self._reports is None:
            return (None, None)
        report = None
        try:
            report = self._reports.latest_for(src)
        except Exception as exc:
            logger.debug("读取历史对比报告失败：%s", exc)
        if report is None:
            return (None, None)
        weights = (float(report.weights[0]), float(report.weights[1]),
                   float(report.weights[2]))
        before_pct = float(report.overall_score)
        after_overall = 0.0
        compared = 0
        try:
            feat_after = self._extract(Path(out))
        except Exception as exc:
            logger.warning("去重后特征提取失败：%s", exc)
            return (before_pct, None)
        for target in report.targets:
            if target.status != "ok" or not target.local_path:
                continue
            local = Path(target.local_path)
            if not local.exists():
                continue
            try:
                feat_t = self._extract(local)
            except Exception as exc:
                logger.debug("target 特征失效 %s：%s", local.name, exc)
                continue
            scores = self._calc.compare(feat_after, feat_t, weights)
            after_overall = max(after_overall, scores.overall)
            compared += 1
        if compared == 0:
            return (before_pct, None)
        return (before_pct, round(after_overall * 100.0, 1))

