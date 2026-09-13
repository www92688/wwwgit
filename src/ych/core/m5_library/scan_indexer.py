# 素材扫描索引（详设 10.2）：增量扫描 + kind 分类（纯函数）
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Literal

from PySide6.QtCore import QObject, Signal

from ych.common.schemas import SUPPORTED_EXTENSIONS
from ych.core.m5_library.workdir_manager import WorkDirManager
from ych.services.s1_media.probe_service import ProbeService
from ych.services.s3_db.daos import AssetIndexDao, AssetRow

logger = logging.getLogger("ych.m5")


def classify_kind(name: str, rel_dir: str) -> Literal["raw", "cleaned", "deduped"]:
    """名称含 _cleaned→cleaned；位于 已去重/ 下或含 _deduped→deduped；否则 raw。"""
    if "_cleaned" in name:
        return "cleaned"
    parts = rel_dir.replace("\\", "/").split("/")
    if "已去重" in parts or "_deduped" in name:
        return "deduped"
    return "raw"


class ScanIndexer(QObject):
    """工作目录增量扫描；全程设计为工作线程执行（由调用方经 M4 提交）。"""

    scan_finished = Signal(int)   # 本次新增条数

    def __init__(
        self,
        workdirs: WorkDirManager,
        assets: AssetIndexDao,
        prober: ProbeService,
    ) -> None:
        super().__init__()
        self._wd = workdirs
        self._assets = assets
        self._prober = prober

    def incremental_scan(self) -> int:
        """os.walk → 与 all_paths 差集 → 懒 probe → upsert；失效行删除。"""
        wd = self._wd.workdir()
        known = self._assets.all_paths()
        seen: set[str] = set()
        added = 0
        rows: list[AssetRow] = []
        walk_errors: list[str] = []

        def _on_walk_error(err: OSError) -> None:
            # 无权限目录：记录并跳过（该子树不参与本轮 stale 判定）
            walk_errors.append(str(err))
            logger.warning("扫描跳过不可访问目录：%s", err)

        for root, _dirs, files in os.walk(wd, onerror=_on_walk_error):
            for fname in files:
                if Path(fname).suffix.lower() not in SUPPORTED_EXTENSIONS:
                    continue
                full = str(Path(root) / fname)
                seen.add(full)
                if full in known:
                    continue
                rel = Path(root).relative_to(wd)
                kind = classify_kind(fname, str(rel))
                category, keyword, date_str = self._split_rel(rel)
                duration_s: float | None = None
                width: int | None = None
                height: int | None = None
                try:
                    info = self._prober.probe(Path(full))   # 懒探测：时长/分辨率
                    duration_s, width, height = (
                        info.duration_s, info.width, info.height
                    )
                except Exception as exc:
                    logger.warning("probe 失败 %s：%s", full, exc)
                try:
                    stat = os.stat(full)
                except OSError as exc:
                    # walk 与 stat 之间文件被删/权限收紧：跳过该文件，
                    # 不让单文件竞态中止整轮扫描（否则 scan_finished 不发，
                    # UI 列表停留在旧态）
                    logger.warning("stat 失败 %s：%s", full, exc)
                    continue
                rows.append(AssetRow(
                    id=0, path=full, kind=kind,
                    size_bytes=stat.st_size, duration_s=duration_s,
                    width=width, height=height, mtime=stat.st_mtime,
                    category=category, keyword=keyword, date_str=date_str,
                    indexed_at="",
                ))
                added += 1
                # 分批落库，避免大目录一次性占用内存
                if len(rows) >= 200:
                    self._assets.upsert_many(rows)
                    rows.clear()

        if rows:
            self._assets.upsert_many(rows)
        stale = known - seen
        if stale and walk_errors:
            # 有不可访问子树时不能把其中的文件当失效删除（权限恢复前列表会"丢文件"）
            logger.warning("存在 %d 个不可访问目录，本轮跳过失效清理（%d 条）",
                           len(walk_errors), len(stale))
        elif stale:
            self._assets.delete_paths(stale)
        if added:
            logger.info("scan added %d assets", added)
        self.scan_finished.emit(added)
        return added

    @staticmethod
    def _split_rel(rel: Path) -> tuple[str | None, str | None, str | None]:
        """rel 形如 清洗类/地毯/2026-01-01[/xxx.mp4] → (大类， 关键词， 日期)。"""
        parts = rel.parts
        if "已去重" in parts:
            parts = tuple(p for p in parts if p != "已去重")
        if len(parts) >= 3 and not Path(parts[2]).suffix:
            return parts[0], parts[1], parts[2]
        if len(parts) == 2 and not Path(parts[1]).suffix:
            return parts[0], parts[1], None
        return (parts[0], None, None) if parts else (None, None, None)
