# 文件定位/剪贴板等右键菜单公共动作
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QProcess, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication


def reveal_in_file_manager(path: str) -> None:
    """在文件管理器中定位文件：Windows 资源管理器选中该文件，
    其他平台打开所在目录（目录本身则直接打开）。"""
    p = Path(path)
    folder = p if p.is_dir() else p.parent
    if sys.platform == "win32" and p.is_file():
        # explorer /select 需要原生反斜杠路径
        QProcess.startDetached("explorer.exe", ["/select,", str(p)])
        return
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))


def copy_to_clipboard(text: str) -> None:
    QGuiApplication.clipboard().setText(text)
