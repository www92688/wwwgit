# 自动搜索对比（详设 14.3）：manual refs + auto 平台候选 → 评分报告
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Protocol

from ych.common.cancellation import CancellationToken, ProgressFn, TaskCanceled
from ych.common.errors import ERR_PLG_UNAVAILABLE
from ych.common.schemas import (
    CompareReport,
    CompareTarget,
    FeatureSet,
    SearchFilters,
    VideoMeta,
)
from ych.core.m3_dedup.candidate_cache import CandidateCache
from ych.core.m3_dedup.local_pool import keyword_of, local_similar_pool
from ych.core.m3_dedup.similarity import DEFAULT_WEIGHTS, ReportBuilder, SimilarityCalculator
from ych.core.m5_library.workdir_manager import WorkDirManager
from ych.services.s1_media.probe_service import ProbeService
from ych.services.s5_base.config_service import ConfigService

logger = logging.getLogger("ych.m3")

# 对比平台集（需求 3.1 + 详设 18.2-6）：必选默认开 + 可选；不含小红书。
# douyin/kuaishou/bilibili/tiktok/youtube 当前为 SkeletonPlugin 占位（未实现，
# 一律上报 unavailable）；pexels/pixabay 是已实现且国内可直连的素材站，
# 线上候选实际由它们供给。
REQUIRED_PLATFORMS = ("douyin", "kuaishou", "bilibili", "pexels", "pixabay")
OPTIONAL_PLATFORMS = ("tiktok", "youtube")

# 单候选「下载+特征」合并预算，超时 skip：在线候选曾只限特征不限下载，
# 一个慢下载能把"分析重复度"无限拖住（进度条停在 45% 假死）
_CANDIDATE_BUDGET_S = 90.0
_LOCAL_WORKERS = 4      # 本地素材池特征提取线程数（ffmpeg/cv2 释放 GIL）
_CANDIDATE_WORKERS = 3  # 候选下载+评分线程数（网络 IO 为主，并行收益大）


class _PluginLike(Protocol):
    """duck-typed：PlatformPlugin 子集（避免对 M1 类型硬依赖）。"""

    id: str
    region: str

    def check_available(self) -> tuple[bool, str]: ...

    def search(
        self,
        keyword: str,
        filters: SearchFilters,
        max_count: int,
        token: CancellationToken | None,
    ) -> list[VideoMeta]: ...


class _PluginManagerLike(Protocol):
    def enabled(self, region: str) -> list[_PluginLike]: ...

    def availability(self, plugin: _PluginLike, force: bool = False) -> tuple[bool, str]: ...


_FeatureExtract = Callable[..., FeatureSet]


class CandidateSearcher:
    """run(src, mode, ref_paths) → CompareReport（含 unavailable 标注）。"""

    def __init__(
        self,
        feature_extract: _FeatureExtract,
        calculator: SimilarityCalculator,
        builder: ReportBuilder,
        plugin_manager: _PluginManagerLike,
        cache: CandidateCache,
        workdirs: WorkDirManager,
        config: ConfigService,
        prober: ProbeService,
    ) -> None:
        self._extract = feature_extract
        self._calc = calculator
        self._builder = builder
        self._plugins = plugin_manager
        self._cache = cache
        self._workdirs = workdirs
        self._config = config
        self._prober = prober

    # ---- 对外 ----
    def run(
        self,
        src: Path,
        mode: str,                             # auto | manual | both
        ref_paths: list[Path] | None = None,
        keyword: str | None = None,
        on_progress: ProgressFn | None = None,
        token: CancellationToken | None = None,
    ) -> CompareReport:
        weights = self._read_weights()
        targets: list[CompareTarget] = []
        unavailable: list[str] = []

        feat_a = self._extract(src, token)
        if on_progress:
            on_progress(0.2)

        if mode in ("manual", "both") and ref_paths:
            targets.extend(self._score_local_batch(
                ref_paths, feat_a, weights, token, on_progress,
                source="manual"))

        if mode in ("auto", "both"):
            # 本地相似素材池：同关键词的已有素材始终参与对比（不依赖
            # 在线平台可达性），保证"分析重复度"永远有真实对比对象
            pool = self._local_pool(src)
            targets.extend(self._score_local_batch(
                pool, feat_a, weights, token, on_progress, source="local"))
            kw = keyword or self._keyword_from_archive(src)
            if not kw:
                if not pool:
                    logger.warning("非归档素材未提供关键词，跳过自动对比")
                    unavailable.append("no_keyword")
            else:
                # 先收集全部平台候选（搜索很快，拿到确定总数），再并行
                # 下载+评分——进度按条推进到 95%，不再长时间停在 45%
                plan, unavail_platforms = self._collect_candidates(kw, token)
                unavailable.extend(unavail_platforms)
                targets.extend(self._score_candidates(
                    plan, feat_a, weights, token, on_progress))

        if on_progress:
            on_progress(1.0)
        report = self._builder.build(src, targets, weights, unavailable)
        if unavailable and mode in ("auto", "both"):
            logger.warning("部分对比源不可用 %s，建议手动指定参考视频兜底",
                           unavailable)
        return report

    # ---- manual / 本地素材池（并行特征提取）----
    def _score_local_batch(
        self,
        paths: Sequence[Path],
        feat_a: FeatureSet,
        weights: tuple[float, float, float],
        token: CancellationToken | None,
        on_progress: ProgressFn | None,
        source: str = "manual",
    ) -> list[CompareTarget]:
        """并行提取本地目标特征；结果顺序与输入一致，按完成数推进进度。"""
        paths = list(paths)
        if not paths:
            return []
        targets: list[CompareTarget | None] = [None] * len(paths)
        done = [0]

        def work(i: int, path: Path) -> None:
            targets[i] = self._score_local(
                path, feat_a, weights, token, source=source)
            done[0] += 1
            if on_progress:
                on_progress(0.2 + 0.25 * min(done[0] / len(paths), 1.0))

        with ThreadPoolExecutor(
            max_workers=min(_LOCAL_WORKERS, len(paths)),
            thread_name_prefix="ych-cmp-local",
        ) as pool:
            list(pool.map(lambda a: work(*a), enumerate(paths)))
        if token is not None and token.cancelled:
            raise TaskCanceled("重复度分析已被取消")
        return [t for t in targets if t is not None]

    def _score_local(
        self, path: Path, feat_a: FeatureSet,
        weights: tuple[float, float, float], token: CancellationToken | None,
        source: str = "manual",
    ) -> CompareTarget:
        target = CompareTarget(source=source,   # type: ignore[arg-type]
                               local_path=str(path), title=path.name)
        if source == "local":
            target.platform_id = "local"
        try:
            feat_b = self._extract(path, token)
            target.scores = self._calc.compare(feat_a, feat_b, weights)
        except TaskCanceled:
            raise
        except Exception as exc:   # 单个坏参考文件不拖垮整轮对比
            target.status = "unavailable"
            target.status_reason = str(exc)
            logger.warning("本地参考提取失败 %s：%s", path.name, exc)
        return target

    # ---- auto 平台候选 ----
    def _keyword_from_archive(self, src: Path) -> str | None:
        """归档路径 <workdir>/<大类>/<关键词>/<日期>/file.mp4 → 关键词。"""
        try:
            return keyword_of(src, self._workdirs.workdir())
        except Exception:   # 未设置工作目录等：视为非归档素材
            return None

    def _local_pool(self, src: Path) -> list[Path]:
        """同关键词本地素材池；未设置工作目录/取配置失败时为空。"""
        try:
            limit = int(
                self._config.get_typed("compare_local_pool_max", int))
        except Exception:
            limit = 8
        try:
            return local_similar_pool(src, self._workdirs.workdir(), limit)
        except Exception as exc:
            logger.debug("本地素材池构建失败：%s", exc)
            return []

    def _compare_plugins(self) -> list[_PluginLike]:
        ids = set(REQUIRED_PLATFORMS) | set(OPTIONAL_PLATFORMS)
        plugins = []
        for region in ("cn", "global"):
            for p in self._plugins.enabled(region):
                if p.id in ids:
                    plugins.append(p)
        return plugins

    def _collect_candidates(
        self, keyword: str, token: CancellationToken | None,
    ) -> tuple[list[tuple[_PluginLike, VideoMeta]], list[str]]:
        """全平台搜索并收集候选（不做下载）；返回 (plugin, meta) 计划表。

        搜索与评分分离：先拿到确定的候选总数，评分阶段才能按条精确推进。
        """
        k = int(self._config.get_typed("compare_candidates_per_platform", int))
        filters = SearchFilters(duration_max_s=90.0)   # 宽筛 ≤90s
        plan: list[tuple[_PluginLike, VideoMeta]] = []
        unavailable: list[str] = []
        for p in self._compare_plugins():
            if token is not None and token.cancelled:
                raise TaskCanceled("重复度分析已被取消")
            try:
                ok, _reason = self._plugins.availability(p)
            except Exception as exc:
                logger.warning("平台 %s 可用性检查失败：%s", p.id, exc)
                ok, _reason = False, ERR_PLG_UNAVAILABLE
            if not ok:
                unavailable.append(p.id)
                continue
            try:
                metas = p.search(keyword, filters, k, token)
            except TaskCanceled:
                raise
            except Exception as exc:
                logger.warning("平台 %s 搜索失败：%s", p.id, exc)
                unavailable.append(p.id)
                continue
            plan.extend((p, meta) for meta in metas[:k])
        return plan, unavailable

    def _score_candidates(
        self,
        plan: list[tuple[_PluginLike, VideoMeta]],
        feat_a: FeatureSet,
        weights: tuple[float, float, float],
        token: CancellationToken | None,
        on_progress: ProgressFn | None,
    ) -> list[CompareTarget]:
        """并行下载+评分候选；进度 0.45→0.95 按完成条数推进。"""
        if not plan:
            return []
        total = len(plan)
        targets: list[CompareTarget | None] = [None] * total
        done = [0]

        def work(i: int, pair: tuple[_PluginLike, VideoMeta]) -> None:
            plugin, meta = pair
            targets[i] = self._score_candidate(plugin, meta, feat_a, weights,
                                               token)
            done[0] += 1
            if on_progress:
                on_progress(0.45 + 0.5 * min(done[0] / total, 1.0))

        with ThreadPoolExecutor(
            max_workers=min(_CANDIDATE_WORKERS, total),
            thread_name_prefix="ych-cmp-cand",
        ) as pool:
            list(pool.map(lambda a: work(*a), enumerate(plan)))
        if token is not None and token.cancelled:
            raise TaskCanceled("重复度分析已被取消")
        # 防御补位（正常路径 targets 全部就位）：缺格按 skip 占位
        out: list[CompareTarget] = []
        for (plugin, meta), t in zip(plan, targets, strict=True):
            if t is None:
                t = CompareTarget(source="auto", platform_id=plugin.id,
                                  video_key=meta.video_key,
                                  title=meta.title or meta.video_key,
                                  url=meta.page_url)
                t.status = "skipped"
                t.status_reason = "未完成"
            out.append(t)
        return out

    def _score_candidate(
        self, plugin: _PluginLike, meta: VideoMeta, feat_a: FeatureSet,
        weights: tuple[float, float, float], token: CancellationToken | None,
    ) -> CompareTarget:
        target = CompareTarget(
            source="auto", platform_id=plugin.id, video_key=meta.video_key,
            title=meta.title or meta.video_key, url=meta.page_url,
        )
        # 单候选「下载+特征」合并预算：工作线程 + join 超时（超时不中断
        # 整体；被放弃的后台线程可能仍会把缓存写完，惠及下一次分析）
        result: dict[str, object] = {}

        def worker() -> None:
            try:
                local = self._cache.fetch_or_download(meta, token)
                result["local"] = str(local)
                result["feat"] = self._extract(Path(local), token)
            except BaseException as exc:   # noqa: BLE001（含 TaskCanceled）
                result["error"] = exc

        thread = threading.Thread(target=worker, daemon=True)
        start = time.monotonic()
        thread.start()
        thread.join(_CANDIDATE_BUDGET_S)
        if thread.is_alive():
            logger.warning("候选下载/特征超时(%ds)，skip：%s/%s",
                           _CANDIDATE_BUDGET_S, plugin.id, meta.video_key)
            target.status = "skipped"
            target.status_reason = "下载或特征提取超时"
            return target
        if "error" in result:
            exc = result["error"]
            if isinstance(exc, TaskCanceled):
                raise exc
            target.status = "skipped"
            target.status_reason = str(exc)
            return target
        elapsed = time.monotonic() - start
        logger.debug("候选下载+特征耗时 %.1fs：%s", elapsed, meta.video_key)
        target.local_path = str(result["local"])
        target.scores = self._calc.compare(feat_a, result["feat"], weights)   # type: ignore[arg-type]
        return target

    def _read_weights(self) -> tuple[float, float, float]:
        raw = self._config.get("dedup_weights") or [0.5, 0.25, 0.25]
        vals = [float(x) for x in raw]   # type: ignore[attr-defined]
        w: tuple[float, float, float] = (
            (vals[0], vals[1], vals[2]) if len(vals) >= 3 else DEFAULT_WEIGHTS
        )
        total = sum(w)
        return w if abs(total - 1.0) < 1e-6 else DEFAULT_WEIGHTS
