# 工作目录管理（详设 10.2）：设定校验、目录布局、变更信号
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ych.common.errors import ERR_FILE_NO_WRITE_PERMISSION, ERR_FILE_WORKDIR_INVALID, AppError
from ych.services.s5_base.config_service import ConfigService

logger = logging.getLogger("ych.m5")

# 系统关键目录黑名单（禁止设为工作目录）
_FORBIDDEN_ROOTS = ("C:\\Windows", "C:\\Program Files", "C:\\Program Files (x86)")


class WorkDirManager(QObject):
    """素材工作目录生命周期；validate 返回 None 表示通过。"""

    workdir_changed = Signal(Path)

    def __init__(self, config: ConfigService) -> None:
        super().__init__()
        self._config = config
        saved = str(config.get("workdir") or "")
        self._workdir = Path(saved) if saved else None

    def validate(self, path: Path) -> AppError | None:
        """存在/可写/非系统关键目录校验。"""
        try:
            p = Path(path)
            if not p.exists() or not p.is_dir():
                return AppError(ERR_FILE_WORKDIR_INVALID,
                                f"工作目录不存在或不是文件夹：{p}")
            resolved = str(p.resolve()).rstrip("\\/")
            for root in _FORBIDDEN_ROOTS:
                if resolved.lower().startswith(root.lower()):
                    return AppError(ERR_FILE_WORKDIR_INVALID,
                                    f"不允许使用系统关键目录：{root}")
            probe = p / ".ych_write_probe"
            try:
                probe.write_bytes(b"")
                probe.unlink()
            except OSError as exc:
                return AppError(ERR_FILE_NO_WRITE_PERMISSION,
                                "工作目录没有写入权限", cause=exc)
            return None
        except OSError as exc:
            return AppError(ERR_FILE_WORKDIR_INVALID, "工作目录无效", cause=exc)

    def set_workdir(self, path: Path) -> None:
        """校验通过后设置工作目录并发射信号。"""
        err = self.validate(path)
        if err is not None:
            raise err
        self._workdir = Path(path)
        self.ensure_layout()
        self._config.set("workdir", str(self._workdir))
        logger.info("workdir set: %s", self._workdir)
        self.workdir_changed.emit(self._workdir)

    def ensure_layout(self) -> None:
        """创建 已去重/ 根目录（幂等）。"""
        wd = self.workdir()
        (wd / "已去重").mkdir(parents=True, exist_ok=True)
        (wd / ".downloading").mkdir(parents=True, exist_ok=True)

    def workdir(self) -> Path:
        """当前工作目录；未设置抛 FILE001。"""
        if self._workdir is None:
            raise AppError(ERR_FILE_WORKDIR_INVALID, "尚未设置素材工作目录")
        return self._workdir

    def is_set(self) -> bool:
        return self._workdir is not None
