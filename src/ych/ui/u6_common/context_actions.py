# 文件定位/剪贴板等右键菜单公共动作
from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtCore import QProcess, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication

logger = logging.getLogger("ych.ui.u6")


def reveal_in_file_manager(path: str) -> bool:
    """在文件管理器中定位文件：Windows 资源管理器选中该文件，
    其他平台打开所在目录（目录本身则直接打开）。

    返回是否成功发起打开；失败时调用方负责提示（点击必有响应约定）。
    """
    p = Path(path)
    folder = p if p.is_dir() else p.parent
    if sys.platform == "win32" and p.is_file():
        # explorer /select 需要原生反斜杠路径；startDetached 返回 (bool, pid)
        result = QProcess.startDetached("explorer.exe", ["/select,", str(p)])
        ok = bool(result[0] if isinstance(result, tuple) else result)
    else:
        ok = QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
    if not ok:
        logger.warning("文件定位失败：%s", path)
    return ok


def copy_to_clipboard(text: str) -> None:
    QGuiApplication.clipboard().setText(text)
