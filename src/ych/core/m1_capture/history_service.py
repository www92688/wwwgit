# 历史搜索词服务（详设 12.2）：SearchHistoryDao 薄封装
from __future__ import annotations

from ych.services.s3_db.daos import SearchHistoryDao


class HistoryService:
    """SearchCoordinator 写入、U1 下拉框读取。"""

    def __init__(self, dao: SearchHistoryDao) -> None:
        self._dao = dao

    def record(self, keyword: str, platform_ids: list[str]) -> None:
        self._dao.add(keyword, platform_ids)

    def suggestions(self, prefix: str = "", limit: int = 20) -> list[str]:
        """按最近使用排序的前缀匹配建议（去重由 DAO GROUP BY 保证）。"""
        kws = self._dao.distinct_keywords(max(limit * 2, limit))
        if not prefix:
            return [k for k, _ts in kws[:limit]]
        return [k for k, _ts in kws if k.startswith(prefix)][:limit]
