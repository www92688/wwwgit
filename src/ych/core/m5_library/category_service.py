# 大类归纳与目录搬移（详设 10.2）：未命中关键词 → 大类=关键词本身
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from ych.core.m5_library.workdir_manager import WorkDirManager
from ych.services.s3_db.daos import CategoryDao

logger = logging.getLogger("ych.m5")


class CategoryService:
    """keyword_category 映射的读写 + 物理目录树搬移（先搬移成功再更新库）。"""

    def __init__(self, categories: CategoryDao, workdirs: WorkDirManager) -> None:
        self._dao = categories
        self._wd = workdirs

    def resolve_category(self, keyword: str) -> str:
        """查映射；未命中新建映射（大类=关键词本身）。"""
        category = self._dao.category_of(keyword)
        if category is None:
            category = keyword
            self._dao.map_keyword(keyword, category)
        return category

    def rename_category(self, old: str, new: str) -> None:
        """物理重命名目录树 + 两表更新（先物理后库，失败不落库）。"""
        wd = self._wd.workdir()
        src = wd / old
        dst = wd / new
        if src.exists():
            if dst.exists():
                raise FileExistsError(f"目标大类已存在：{dst}")
            shutil.move(str(src), str(dst))
        # 已去重镜像区同样重命名（存在时）
        mirror_src = wd / "已去重" / old
        mirror_dst = wd / "已去重" / new
        if mirror_src.exists() and not mirror_dst.exists():
            shutil.move(str(mirror_src), str(mirror_dst))
        self._dao.rename_category(old, new)
        logger.info("category renamed %s -> %s", old, new)

    def merge(self, src: str, dst: str) -> None:
        """把 src 大类的整棵目录树并入 dst，再更新两表。"""
        wd = self._wd.workdir()
        src_dir = wd / src
        dst_dir = wd / dst
        if src_dir.exists():
            dst_dir.mkdir(parents=True, exist_ok=True)
            for child in src_dir.iterdir():
                target = dst_dir / child.name
                if target.exists():
                    raise FileExistsError(f"合并冲突：{target}")
                shutil.move(str(child), str(target))
            src_dir.rmdir()
        mirror_src = wd / "已去重" / src
        mirror_dst = wd / "已去重" / dst
        if mirror_src.exists():
            mirror_dst.mkdir(parents=True, exist_ok=True)
            for child in mirror_src.iterdir():
                target = mirror_dst / child.name
                if not target.exists():
                    shutil.move(str(child), str(target))
            mirror_src.rmdir()
        self._dao.merge_categories(src, dst)
        logger.info("category merged %s -> %s", src, dst)

    def category_root(self, category: str) -> Path:
        return self._wd.workdir() / category
