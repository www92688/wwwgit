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
from ych.core.m3_dedup.local_pool import local_similar_pool
from ych.core.m3_dedup.similarity import DEFAULT_WEIGHTS, SimilarityCalculator
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
        weights: tuple[float, float, float] = DEFAULT_WEIGHTS,
        pool_limit: int = 8,
    ) -> None:
        self._runner = runner
        self._prober = prober
        self._archive = archive
        self._workdirs = workdirs
        self._registry = registry
        self._extract = feature_extract
        self._calc = calculator
        self._reports = reports
        self._weights_cfg = weights
        self._pool_limit = max(1, int(pool_limit))

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
        # id 去重（保留首次出现的参数）：重复手法既丢参数，又会让
        # blur 边框产生同名 label 的滤镜图导致 ffmpeg 解析失败
        params_by_id: dict[str, dict[str, object]] = {}
        for item in technique_params:
            tid = str(item.get("id"))
            raw = item.get("params") or {}
            assert isinstance(raw, dict)
            params_by_id.setdefault(tid, raw)
        for technique in self._registry.ordered(list(params_by_id)):
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
        # atempo 合法域 [0.5, 2.0]：多个 speed 叠加越界时收敛到边界
        speed = min(max(ctx.speed_factor, 0.5), 2.0)
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
        """处理前后与同一批对比对象的最大相似度。

        对象优先取 ReportDao.latest_for(src) 的可用 targets（手动参考/
        在线候选）；没有可用 targets 时回退本地同关键词素材池——保证
        "重复度 x% → y%" 在离线环境也是真实计算而非恒空。
        """
        if self._extract is None or self._calc is None:
            return (None, None)
        report = None
        try:
            report = self._reports.latest_for(src) if self._reports else None
        except Exception as exc:
            logger.debug("读取历史对比报告失败：%s", exc)
        target_paths: list[Path] = []
        before_pct: float | None = None
        if report is not None:
            target_paths = [
                Path(t.local_path) for t in report.targets
                if t.status == "ok" and t.local_path
                and Path(t.local_path).exists()
            ]
            if target_paths:
                before_pct = float(report.overall_score)
        if not target_paths:
            # 本地池兜底：前后用同一批对象，分数才可比
            try:
                target_paths = local_similar_pool(
                    src, self._workdirs.workdir(), self._pool_limit)
            except Exception as exc:   # 未设置工作目录等
                logger.debug("本地素材池构建失败：%s", exc)
                return (None, None)
        if not target_paths:
            return (None, None)

        try:
            feat_after = self._extract(Path(out))
        except Exception as exc:
            logger.warning("去重后特征提取失败：%s", exc)
            return (before_pct, None)
        if before_pct is None:
            # 本地池路径：before 需要源视频对同一批对象重算
            try:
                feat_src = self._extract(src)
            except Exception as exc:
                logger.warning("去重前特征提取失败：%s", exc)
                return (None, None)
        else:
            feat_src = None
        after_overall = 0.0
        before_overall = 0.0
        compared = 0
        for local in target_paths:
            try:
                feat_t = self._extract(local)
            except Exception as exc:
                logger.debug("target 特征失效 %s：%s", local.name, exc)
                continue
            after_overall = max(
                after_overall,
                self._calc.compare(feat_after, feat_t, self._weights(report)).overall,
            )
            if feat_src is not None:
                before_overall = max(
                    before_overall,
                    self._calc.compare(feat_src, feat_t, self._weights(report)).overall,
                )
            compared += 1
        if feat_src is not None and compared:
            before_pct = round(before_overall * 100.0, 1)
        if compared == 0:
            return (before_pct, None)
        return (before_pct, round(after_overall * 100.0, 1))

    def _weights(self, report: object) -> tuple[float, float, float]:
        """历史报告存在时沿用其权重，否则用配置权重。"""
        weights = getattr(report, "weights", None)
        if weights and len(weights) >= 3:
            try:
                return (float(weights[0]), float(weights[1]),
                        float(weights[2]))
            except (TypeError, ValueError):
                pass
        return self._weights_cfg

