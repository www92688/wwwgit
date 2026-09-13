# 搜索协调器（详设 12.2）：多平台并发隔离、结果合并筛选、历史记录
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, Signal

from ych.common.cancellation import CancellationToken
from ych.common.schemas import Region, SearchFilters, VideoMeta
from ych.core.m1_capture.history_service import HistoryService
from ych.core.m1_capture.plugin_manager import PluginManager
from ych.core.m1_capture.result_filter import ResultFilter
from ych.services.s5_base.config_service import ConfigService

logger = logging.getLogger("ych.m1")


@dataclass
class SearchResultSet:
    keyword: str
    items: list[VideoMeta] = field(default_factory=list)
    unavailable_platforms: list[tuple[str, str]] = field(default_factory=list)


class SearchCoordinator(QObject):
    """search_multi 异步执行；单平台异常隔离，不拖垮整轮搜索。"""

    search_finished = Signal(object)      # SearchResultSet
    search_failed = Signal(str, str)      # keyword, message

    def __init__(
        self,
        manager: PluginManager,
        history: HistoryService,
        config: ConfigService,
    ) -> None:
        super().__init__()
        self._manager = manager
        self._history = history
        self._config = config
        self._filter = ResultFilter()
        self._worker: threading.Thread | None = None

    def search_multi(
        self,
        keywords: list[str],
        filters: SearchFilters,
        per_platform_limit: int = 30,
        token: CancellationToken | None = None,
        region: Region | None = None,
        platform_ids: list[str] | None = None,
    ) -> None:
        """异步入口：立即返回，结果经 search_finished/search_failed 通知。

        platform_ids 非空时只搜索勾选的平台（None=全部启用平台）。
        """
        if self._worker is not None and self._worker.is_alive():
            # 上一轮搜索线程还在跑：拒绝重入（UI 层有 _searching 防抖，
            # 此为核心层兜底——否则两轮线程并发 emit，结果会交错污染列表）
            logger.warning("上一轮搜索尚未结束，本次 search_multi 调用被忽略")
            return
        regions: list[Region] = [region] if region is not None else ["cn", "global"]
        worker = threading.Thread(
            target=self._run,
            args=(list(keywords), filters, per_platform_limit, token, regions,
                  platform_ids),
            daemon=True,
            name="ych-search-coordinator",
        )
        self._worker = worker
        worker.start()

    # ---- 工作线程体 ----
    def _run(
        self,
        keywords: list[str],
        filters: SearchFilters,
        limit: int,
        token: CancellationToken | None,
        regions: list[Region],
        platform_ids: list[str] | None = None,
    ) -> None:
        for kw in keywords:
            if token is not None and token.cancelled:
                logger.info("搜索在关键词「%s」前被取消", kw)
                return
            try:
                result = self._search_once(
                    kw, filters, limit, token, regions, platform_ids,
                )
            except Exception as exc:  # 整关键词级兜底（正常不达）
                logger.exception("搜索关键词「%s」失败", kw)
                self.search_failed.emit(kw, str(exc))
                continue
            self.search_finished.emit(result)

    def _search_once(
        self,
        keyword: str,
        filters: SearchFilters,
        limit: int,
        token: CancellationToken | None,
        regions: list[Region],
        platform_ids: list[str] | None = None,
    ) -> SearchResultSet:
        items: list[VideoMeta] = []
        unavailable: list[tuple[str, str]] = []
        used_ids: list[str] = []
        plugins = [
            p for r in regions for p in self._manager.enabled(r)
            if platform_ids is None or p.id in platform_ids
        ]
        for p in plugins:
            ok, reason = self._manager.availability(p)
            if not ok:
                unavailable.append((p.id, reason))
                continue
            used_ids.append(p.id)
            try:
                found = p.search(keyword, filters, limit, token)
            except Exception as exc:  # 单平台异常隔离（PLG 域错误）
                logger.warning("平台 %s 搜索失败：%s", p.id, exc)
                code = getattr(exc, "code", "")
                unavailable.append((p.id, str(code or exc)))
                continue
            items.extend(found)
        merged = self._filter.apply(items, filters)
        if used_ids:
            self._history.record(keyword, used_ids)
        return SearchResultSet(
            keyword=keyword, items=merged, unavailable_platforms=unavailable
        )
