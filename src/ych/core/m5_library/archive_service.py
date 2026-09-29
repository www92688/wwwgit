# 归档命名服务（详设 10.2/10.3）：三级归档 + 序号命名 + 镜像输出路径
from __future__ import annotations

import logging
import re
import threading
from datetime import datetime
from pathlib import Path

from ych.common.cancellation import CancellationToken
from ych.common.fsutil import SafeFileOps, long_path
from ych.common.schemas import VideoMeta
from ych.core.m5_library.workdir_manager import WorkDirManager
from ych.services.s3_db.daos import AssetIndexDao, AssetRow, CategoryDao
from ych.services.s5_base.config_service import ConfigService

logger = logging.getLogger("ych.m5")


class ArchiveService:
    """下载产物归档 / cleaned 与 deduped 输出路径计算（M1/M2/M3 的落盘后端）。"""

    def __init__(
        self,
        workdirs: WorkDirManager,
        config: ConfigService,
        assets: AssetIndexDao,
        categories: CategoryDao,
    ) -> None:
        self._wd = workdirs
        self._config = config
        self._assets = assets
        self._categories = categories
        # 目录级命名锁：并发下载同关键词时串行化「取号→落位」，防止
        # next_filename 与 os.replace 之间的 TOCTOU 竞态互相覆盖成品
        self._name_locks: dict[str, threading.Lock] = {}
        self._name_locks_guard = threading.Lock()

    def _dir_lock(self, key: str) -> threading.Lock:
        with self._name_locks_guard:
            lock = self._name_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._name_locks[key] = lock
            return lock

    # ---- 命名 ----
    # 关键词直接进文件系统（目录/文件名）：URL 形态关键词（如抖音主页链接，
    # 含 : / ? &）不经处理在 Windows 上建目录必失败——入盘前统一净化
    _FS_UNSAFE = re.compile(r'[\\/:*?"<>|\r\n\t ]+')
    _URL_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://")
    _FS_KW_MAX = 40

    @classmethod
    def fs_keyword(cls, keyword: str) -> str:
        """关键词 → 文件系统安全形态（同一关键词恒等映射：跨日期归同一目录，
        序号计数才连续）。URL 取路径末段作可读标识（如抖音 sec_uid），
        其余替换非法字符；空结果与超长均有兜底。"""
        text = keyword.strip()
        if cls._URL_SCHEME.match(text):
            text = text.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1] or text
        cleaned = cls._FS_UNSAFE.sub("_", text).strip("._")
        return cleaned[: cls._FS_KW_MAX] or "unnamed"

    def next_filename(
        self,
        platform: str,
        keyword: str,
        date_str: str,
        ext: str = ".mp4",
    ) -> tuple[Path, str]:
        """`平台_关键词_三位序号.ext`；序号=max(磁盘现有，索引计数)+1，冲突递增兜底。"""
        keyword = self.fs_keyword(keyword)
        target_dir = self._category_dir_for(keyword) / date_str
        prefix = f"{platform}_{keyword}_"
        disk_max = 0
        if target_dir.exists():
            for existing in target_dir.glob(f"{prefix}*{ext}"):
                stem = existing.stem[len(prefix):]
                if stem.isdigit():
                    disk_max = max(disk_max, int(stem))
        like = str(long_path(target_dir)) + "\\" + prefix + "%"
        index_max = self._assets.count_named_like(like)
        seq = max(disk_max, index_max) + 1
        while True:
            name = f"{prefix}{seq:03d}{ext}"
            candidate = target_dir / name
            if not candidate.exists():
                return candidate, name
            seq += 1

    def _category_dir_for(self, keyword: str) -> Path:
        category = self._categories.category_of(keyword) or keyword
        return self._wd.workdir() / category / keyword

    # ---- 下载归档（详设 10.3 完整流程）----
    def archive_download(
        self,
        meta: VideoMeta,
        temp_file: Path,
        keyword: str,
        token: CancellationToken | None = None,
    ) -> Path:
        """resolve_category → 三级目录 → 序号命名 → 原子移动 → 只读 → 索引。"""
        keyword = self.fs_keyword(keyword)   # URL 关键词（抖音主页）转安全形态
        wd = self._wd.workdir()
        category = self._categories.category_of(keyword)
        if category is None:
            category = keyword          # 未命中：大类=关键词本身（详设 18.2-2）
            self._categories.map_keyword(keyword, category)

        date_str = datetime.now().strftime("%Y-%m-%d")
        final_dir = wd / category / keyword / date_str
        final_dir.mkdir(parents=True, exist_ok=True)

        # 取号与落位在同一把目录锁内完成（并发下载同目录不互相覆盖）
        with self._dir_lock(str(final_dir)):
            candidate, _name = self.next_filename(
                meta.plugin_id, keyword, date_str, ".mp4",
            )
            # os.replace 同卷原子移动（.part 已由 S4 fsync 落盘）
            SafeFileOps.atomic_write(candidate, lambda dst: _move(temp_file, dst))

        readonly_on = bool(self._config.get("readonly_protect_raw"))
        if readonly_on:
            try:
                SafeFileOps.protect_readonly(candidate, True)
            except OSError as exc:
                logger.warning("只读保护失败：%s", exc)

        size = candidate.stat().st_size
        self._assets.upsert_many([AssetRow(
            id=0, path=str(candidate), kind="raw",
            size_bytes=size, duration_s=meta.duration_s,
            width=meta.width, height=meta.height, mtime=candidate.stat().st_mtime,
            category=category, keyword=keyword, date_str=date_str, indexed_at="",
        )])
        logger.info("archived %s -> %s", temp_file.name, candidate.name)
        return candidate

    # ---- 输出镜像路径 ----
    def mirror_path_for_output(
        self,
        src: Path,
        suffix: str,
        out_root: Path | None = None,
    ) -> Path:
        """cleaned：同目录加后缀；deduped：out_root 下镜像 src 相对工作目录层级。

        例：src=<wd>/清洗类/地毯/2026-01-01/a.mp4, suffix=_deduped,
        out_root=<wd>/已去重 → <wd>/已去重/清洗类/地毯/2026-01-01/a_deduped.mp4
        """
        src = Path(src)
        out_name = src.stem + suffix + src.suffix
        if out_root is None:
            return src.with_name(out_name)
        try:
            rel = src.relative_to(self._wd.workdir())
        except ValueError as exc:
            from ych.common.errors import ERR_FILE_WORKDIR_INVALID, AppError

            raise AppError(ERR_FILE_WORKDIR_INVALID,
                           "素材不在工作目录内，无法镜像输出") from exc
        return Path(out_root) / rel.parent / out_name


def _move(src: Path, dst: Path) -> None:
    """atomic_write 的 writer 回调：把已完成临时文件搬到位。"""
    import os

    os.replace(long_path(Path(src)), long_path(Path(dst)))
