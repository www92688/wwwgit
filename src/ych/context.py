# AppContext 服务定位器（详设 15.1/工程结构）：持有全部服务单例引用
# 全部懒加载：支撑冷启动 ≤5s（UI 先行，重资源按需初始化）
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from PySide6.QtCore import QObject

if TYPE_CHECKING:
    from ych.core.m1_capture.download_manager import DownloadManager
    from ych.core.m1_capture.history_service import HistoryService
    from ych.core.m1_capture.net_checker import ForeignNetChecker
    from ych.core.m1_capture.plugin_manager import PluginManager
    from ych.core.m1_capture.search_coordinator import SearchCoordinator
    from ych.core.m3_dedup.candidate_cache import CandidateCache
    from ych.core.m3_dedup.candidate_searcher import CandidateSearcher
    from ych.core.m3_dedup.dedup_pipeline import DedupPipeline
    from ych.core.m3_dedup.feature_extractor import FeatureExtractService
    from ych.core.m3_dedup.scheme_manager import SchemeManager
    from ych.core.m3_dedup.similarity import ReportBuilder, SimilarityCalculator
    from ych.core.m3_dedup.techniques.registry import TechniqueRegistry
    from ych.core.m4_scheduler.task_scheduler import TaskScheduler
    from ych.core.m5_library.archive_service import ArchiveService
    from ych.core.m5_library.workdir_manager import WorkDirManager
    from ych.services.s1_media.ffmpeg_runner import FFmpegRunner
    from ych.services.s1_media.frame_extractor import FrameExtractor
    from ych.services.s1_media.probe_service import ProbeService
    from ych.services.s2_ai.ai_gateway import AiGateway
    from ych.services.s2_ai.model_downloader import ModelDownloader
    from ych.services.s2_ai.provider import InferenceProvider
    from ych.services.s3_db.daos import DaosBundle
    from ych.services.s3_db.database import Database
    from ych.services.s4_net.http_client import HttpClient
    from ych.services.s4_net.rate_limiter import RateLimiter
    from ych.services.s5_base.config_service import ConfigService
    from ych.services.s5_base.i18n_service import I18nService

logger = logging.getLogger("ych.app")


def user_data_dir() -> Path:
    import os

    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "YuChongGou"
    return Path.home() / ".yu_chong_gou"


@dataclass
class AppContext:
    """DI 组装点；属性均为懒构造单例。"""

    _cache: dict[str, object] = field(default_factory=dict)

    def _cache_cast(self, key: str) -> Any:
        """缓存取值（懒加载单例均由对应方法构造，类型由调用方注解保证）。"""
        return self._cache[key]

    # ---- S5 基础 ----
    def config(self) -> ConfigService:
        if "config" not in self._cache:
            from ych.services.s5_base.config_service import ConfigService

            self._cache["config"] = ConfigService()
        return cast("ConfigService", self._cache_cast("config"))

    def i18n(self) -> I18nService:
        if "i18n" not in self._cache:
            from ych.services.s5_base.i18n_service import I18nService

            self._cache["i18n"] = I18nService()
        return cast("I18nService", self._cache_cast("i18n"))

    def log_dir(self) -> Path:
        path = user_data_dir() / "logs"
        path.mkdir(parents=True, exist_ok=True)
        return path

    # ---- S3 数据 ----
    def database(self) -> Database:
        if "db" not in self._cache:
            from ych.services.s3_db.daos import make_daos
            from ych.services.s3_db.database import Database

            db_path = user_data_dir() / "app.db"
            db = Database(db_path)
            daos = make_daos(db)
            self._cache["db"] = db
            self._cache["daos"] = daos
            # SQLite 就绪后回填配置（详设 5.3 启动时序）
            from ych.services.s5_base.config_service import ConfigService

            assert isinstance(self.config(), ConfigService)
            self.config().database_ready(daos.settings)
        return cast("Database", self._cache_cast("db"))

    def daos(self) -> DaosBundle:
        self.database()
        return cast("DaosBundle", self._cache_cast("daos"))

    # ---- S4 网络 ----
    def http(self) -> HttpClient:
        if "http" not in self._cache:
            from ych.services.s4_net.http_client import HttpClient

            self._cache["http"] = HttpClient(self.config())
        return cast("HttpClient", self._cache_cast("http"))

    # ---- S2 远程 AI（自定义服务）----
    def ai_gateway(self) -> AiGateway:
        if "ai_gateway" not in self._cache:
            from ych.services.s2_ai.ai_gateway import AiGateway

            self._cache["ai_gateway"] = AiGateway(self.config(), self.http())
        return cast("AiGateway", self._cache_cast("ai_gateway"))

    def rate_limiter(self) -> RateLimiter:
        if "limiter" not in self._cache:
            from ych.services.s4_net.rate_limiter import RateLimiter

            self._cache["limiter"] = RateLimiter()
        return cast("RateLimiter", self._cache_cast("limiter"))

    # ---- M5 素材库 ----
    def workdirs(self) -> WorkDirManager:
        if "workdirs" not in self._cache:
            from ych.core.m5_library.workdir_manager import WorkDirManager

            self._cache["workdirs"] = WorkDirManager(self.config())
        return cast("WorkDirManager", self._cache_cast("workdirs"))

    def archive(self) -> ArchiveService:
        if "archive" not in self._cache:
            from ych.core.m5_library.archive_service import ArchiveService

            daos = self.daos()
            self._cache["archive"] = ArchiveService(
                self.workdirs(), self.config(), daos.assets, daos.categories,
            )
        return cast("ArchiveService", self._cache_cast("archive"))

    # ---- S1/S2 ----
    def ffmpeg_runner(self) -> FFmpegRunner:
        if "runner" not in self._cache:
            from ych.services.s1_media.ffmpeg_runner import FFmpegRunner

            self._cache["runner"] = FFmpegRunner()
        return cast("FFmpegRunner", self._cache_cast("runner"))

    def prober(self) -> ProbeService:
        if "prober" not in self._cache:
            from ych.services.s1_media.probe_service import ProbeService

            self._cache["prober"] = ProbeService(self.ffmpeg_runner())
        return cast("ProbeService", self._cache_cast("prober"))

    def frame_extractor(self) -> FrameExtractor:
        if "extractor" not in self._cache:
            from ych.services.s1_media.frame_extractor import FrameExtractor

            self._cache["extractor"] = FrameExtractor(
                self.ffmpeg_runner(), self.prober(),
            )
        return cast("FrameExtractor", self._cache_cast("extractor"))

    def provider(self) -> InferenceProvider:
        if "provider" not in self._cache:
            from ych.services.s2_ai.local_provider import LocalProvider
            from ych.services.s2_ai.model_registry import ModelRegistry

            registry = ModelRegistry()
            self._cache["provider"] = LocalProvider(registry)
        return cast("InferenceProvider", self._cache_cast("provider"))

    def model_downloader(self) -> ModelDownloader:
        if "model_downloader" not in self._cache:
            from ych.services.s2_ai.model_downloader import ModelDownloader

            self._cache["model_downloader"] = ModelDownloader(self.http())
        return cast("ModelDownloader", self._cache_cast("model_downloader"))

    # ---- M4 调度 ----
    def scheduler(self) -> TaskScheduler:
        if "scheduler" not in self._cache:
            from PySide6.QtCore import QThreadPool

            from ych.core.m4_scheduler.task_scheduler import TaskScheduler

            pool = QThreadPool.globalInstance()
            sched = TaskScheduler(pool, self.config(), self.daos())
            self._cache["scheduler"] = sched
        return cast("TaskScheduler", self._cache_cast("scheduler"))

    # ---- M1 采集 ----
    def plugin_manager(self) -> PluginManager:
        if "plugins" not in self._cache:
            from ych.core.m1_capture.plugin_manager import PluginManager

            manager = PluginManager(
                self.http(), self.config(), self.rate_limiter(),
            )
            manager.discover()
            self._cache["plugins"] = manager
        return cast("PluginManager", self._cache_cast("plugins"))

    def history(self) -> HistoryService:
        if "history" not in self._cache:
            from ych.core.m1_capture.history_service import HistoryService

            self._cache["history"] = HistoryService(self.daos().history)
        return cast("HistoryService", self._cache_cast("history"))

    def net_checker(self) -> ForeignNetChecker:
        if "net_checker" not in self._cache:
            from ych.core.m1_capture.net_checker import ForeignNetChecker

            self._cache["net_checker"] = ForeignNetChecker(
                self.http(), self.config(), self.daos().settings,
            )
        return cast("ForeignNetChecker", self._cache_cast("net_checker"))

    def search_coordinator(self) -> SearchCoordinator:
        if "coordinator" not in self._cache:
            from ych.core.m1_capture.search_coordinator import SearchCoordinator

            self._cache["coordinator"] = SearchCoordinator(
                self.plugin_manager(), self.history(), self.config(),
            )
        return cast("SearchCoordinator", self._cache_cast("coordinator"))

    def download_manager(self) -> DownloadManager:
        if "download_manager" not in self._cache:
            from ych.core.m1_capture.download_manager import DownloadManager

            self._cache["download_manager"] = DownloadManager(
                self.scheduler(), self.plugin_manager(), self.archive(),
                self.workdirs(), self.daos(), self.config(),
            )
        return cast("DownloadManager", self._cache_cast("download_manager"))

    # ---- M2 预处理 ----
    def preprocess_pipeline(self) -> Any:
        if "pipeline_m2" not in self._cache:
            from ych.core.m2_preprocess.filter_only_processor import (
                FilterOnlyProcessor,
            )
            from ych.core.m2_preprocess.frame_level_processor import (
                FrameLevelProcessor,
            )
            from ych.core.m2_preprocess.preprocess_pipeline import (
                PreprocessPipeline,
            )
            from ych.core.m2_preprocess.subtitle_handler import SubtitleHandler

            subtitles = SubtitleHandler(
                self.ffmpeg_runner(), self.frame_extractor(), self.provider(),
            )
            filters = FilterOnlyProcessor(self.ffmpeg_runner())
            frames = FrameLevelProcessor(
                self.ffmpeg_runner(), self.frame_extractor(),
                self.provider(), self.config(),
            )
            pipeline = PreprocessPipeline(
                self.prober(), filters, subtitles, frames, self.archive(),
            )
            self._cache["subtitles"] = subtitles
            self._cache["filters_m2"] = filters
            self._cache["frames_m2"] = frames
            self._cache["pipeline_m2"] = pipeline
        return cast(Any, self._cache_cast("pipeline_m2"))

    # ---- M3 去重 ----
    def feature_extract_service(self) -> FeatureExtractService:
        if "feats_m3" not in self._cache:
            from ych.core.m3_dedup.feature_extractor import FeatureExtractService

            self._cache["feats_m3"] = FeatureExtractService(
                self.prober(), self.frame_extractor(), self.provider(),
            )
        return cast("FeatureExtractService", self._cache_cast("feats_m3"))

    def similarity(self) -> SimilarityCalculator:
        if "similarity" not in self._cache:
            from ych.core.m3_dedup.similarity import SimilarityCalculator

            self._cache["similarity"] = SimilarityCalculator()
        return cast("SimilarityCalculator", self._cache_cast("similarity"))

    def report_builder(self) -> ReportBuilder:
        if "report_builder" not in self._cache:
            from ych.core.m3_dedup.similarity import ReportBuilder

            self._cache["report_builder"] = ReportBuilder(self.daos().reports)
        return cast("ReportBuilder", self._cache_cast("report_builder"))

    def technique_registry(self) -> TechniqueRegistry:
        if "registry_m3" not in self._cache:
            from ych.core.m3_dedup.techniques.registry import (
                make_default_registry,
            )

            self._cache["registry_m3"] = make_default_registry()
        return cast("TechniqueRegistry", self._cache_cast("registry_m3"))

    def scheme_manager(self) -> SchemeManager:
        if "schemes_m3" not in self._cache:
            from ych.core.m3_dedup.scheme_manager import SchemeManager

            self._cache["schemes_m3"] = SchemeManager(
                self.technique_registry(), self.daos().schemes,
            )
        return cast("SchemeManager", self._cache_cast("schemes_m3"))

    def candidate_cache(self) -> CandidateCache:
        if "candidate_cache" not in self._cache:
            from ych.core.m3_dedup.candidate_cache import CandidateCache

            ttl_days = float(
                int(self.config().get_typed("candidate_cache_ttl_days", int))
            )

            def downloader(meta: object, dest: object,
                           token: object) -> Path:
                plugin = self.plugin_manager().get(meta.plugin_id)   # type: ignore[attr-defined]
                if plugin is None:
                    raise RuntimeError(f"未注册插件：{meta.plugin_id}")   # type: ignore[attr-defined]
                state = plugin.download(
                    meta, Path(str(dest)), None, None, token,   # type: ignore[arg-type]
                )
                final = getattr(state, "temp_path", "") or str(dest)
                return Path(final)

            root = user_data_dir() / "compare_cache"
            self._cache["candidate_cache"] = CandidateCache(
                downloader, root=root, ttl_days=ttl_days,
            )
        return cast("CandidateCache", self._cache_cast("candidate_cache"))

    def candidate_searcher(self) -> CandidateSearcher:
        if "searcher_m3" not in self._cache:
            from ych.core.m3_dedup.candidate_searcher import CandidateSearcher

            self._cache["searcher_m3"] = CandidateSearcher(
                feature_extract=self.feature_extract_service().extract,
                calculator=self.similarity(),
                builder=self.report_builder(),
                plugin_manager=cast(Any, self.plugin_manager()),
                cache=self.candidate_cache(),
                workdirs=cast(Any, self.workdirs()),
                config=self.config(),
                prober=self.prober(),
            )
        return cast("CandidateSearcher", self._cache_cast("searcher_m3"))

    def dedup_pipeline(self) -> DedupPipeline:
        if "pipeline_m3" not in self._cache:
            from ych.core.m3_dedup.dedup_pipeline import DedupPipeline

            self._cache["pipeline_m3"] = DedupPipeline(
                runner=self.ffmpeg_runner(),
                prober=self.prober(),
                archive=self.archive(),
                workdirs=self.workdirs(),
                registry=self.technique_registry(),
                feature_extract=self.feature_extract_service().extract,
                calculator=self.similarity(),
                reports=self.daos().reports,
            )
        return cast("DedupPipeline", self._cache_cast("pipeline_m3"))

    # ---- 业务 handler 注册（app 启动时调用一次）----
    def register_task_handlers(self) -> None:
        from ych.core.m2_preprocess.preprocess_pipeline import handle_preprocess_task

        sched = self.scheduler()
        sched.register_handler("preprocess", lambda task: handle_preprocess_task(
            task, self.preprocess_pipeline(), self._fail_manager()))

        def handle_dedup(task: Any) -> Any:
            return self._handle_dedup(task)

        sched.register_handler("dedup", handle_dedup)

        def handle_compare(task: Any) -> Any:
            return self._handle_compare(task)

        sched.register_handler("compare", handle_compare)

    def _fail_manager(self) -> Any:
        if "fail_manager" not in self._cache:
            from ych.core.m4_scheduler.fail_record_manager import FailRecordManager

            self._cache["fail_manager"] = FailRecordManager(self.daos().fails)
        return cast(Any, self._cache_cast("fail_manager"))

    def _handle_dedup(self, task: Any) -> Any:
        from ych.core.m4_scheduler.task_scheduler import TaskResult

        payload: Any = task.payload
        items = list(payload.data.get("items") or [])
        outputs: list[str] = []
        failed = skipped = 0
        token = getattr(task, "token", None)
        before = after = None
        for raw in items:
            src = str(raw.get("src"))
            params = list(raw.get("technique_params") or [])
            try:
                result = self.dedup_pipeline().execute_item(
                    Path(src), params, None, token,
                )
                outputs.append(str(result.out_path))
                before, after = result.before_pct, result.after_pct
            except Exception as exc:
                code = getattr(exc, "code", "")
                if code == "TASK004" or isinstance(exc, __import__(
                        "ych.common.cancellation", fromlist=["SkippedSignal"]).SkippedSignal):
                    skipped += 1
                    continue
                failed += 1
                self._fail_manager().record(
                    task, exc if isinstance(exc, Exception) else RuntimeError(str(exc)))
        first = outputs[0] if len(outputs) == 1 else (outputs[0] if outputs else None)
        return TaskResult(
            summary={"outputs": outputs, "failed": failed, "skipped": skipped,
                     "before_pct": before, "after_pct": after},
            output_path=first,
        )

    def _handle_compare(self, task: Any) -> Any:
        from ych.core.m4_scheduler.task_scheduler import TaskResult

        payload: Any = task.payload
        srcs = [str(s) for s in (payload.data.get("srcs") or [])]
        mode = str(payload.data.get("mode") or "both")
        refs = [Path(str(p)) for p in (payload.data.get("ref_paths") or [])]
        keyword = payload.data.get("keyword")
        report = self.candidate_searcher().run(
            Path(srcs[0]) if srcs else Path("."),
            mode=mode,
            ref_paths=refs,
            keyword=str(keyword) if keyword else None,
            token=getattr(task, "token", None),
        )
        return TaskResult(summary={
            "overall_score": report.overall_score,
            "unavailable_platforms": report.unavailable_platforms,
        }, output_path=report.src_path)


def build_context() -> AppContext:
    ctx = AppContext()
    ctx.config()
    ctx.i18n()
    return ctx


def _quiet_qt(obj: QObject) -> QObject:   # pragma: no cover - 类型兜底占位
    return obj
