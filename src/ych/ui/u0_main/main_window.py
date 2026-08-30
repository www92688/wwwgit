# 主窗口（U0）：左侧导航 + 页面栈 + 工作目录引导 + 使用说明
from __future__ import annotations

import logging
from typing import Any, Protocol

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger("ych.ui")

_NAV_KEYS = [
    ("采集工作台", "Capture"), ("预处理工作台", "Preprocess"),
    ("去重工作台", "Dedup"), ("失败列表", "Failures"), ("设置", "Settings"),
]


class _WorkDirsLike(Protocol):
    def is_set(self) -> bool: ...

    def set_workdir(self, path) -> None: ...   # type: ignore[no-untyped-def]


class HelpDialog(QDialog):
    """使用说明：三大工作台三步操作指引。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("使用说明")
        text = QTextBrowser()
        text.setHtml(
            "<h3>采集（输词 → 搜 → 下）</h3>"
            "<p>1. 顶部输入关键词（逗号分隔可批量）→ 2. 点击「搜索」→ "
            "3. 勾选结果点击「下载选中」。下载数量上限在列表下方设置。</p>"
            "<h3>预处理（勾素材 → 选项 → 开始）</h3>"
            "<p>1. 左侧勾选素材 → 2. 右侧选择处理项（去水印/去字幕/裁剪/比例/去原声，"
            "手动模式可在画布框选区域）→ 3. 点击「开始处理」。</p>"
            "<h3>去重（选素材 → 选方案 → 开始）</h3>"
            "<p>1. 左侧勾选素材 → 2. 先「分析重复度」获得推荐档位，"
            "选择轻/中/重度或自定义参数 → 3. 点击「开始去重」。"
            "输出保存在 已去重/ 目录并展示前后重复度对比。</p>"
            "<h3>失败列表</h3><p>失败任务可一键重新处理；网络/密钥问题请先到设置页检查。</p>"
        )
        layout = QVBoxLayout(self)
        layout.addWidget(text)
        btn_close = QPushButton("关闭")
        btn_close.setObjectName("secondaryBtn")
        btn_close.clicked.connect(self.accept)
        layout.addWidget(btn_close)
        self.resize(560, 480)


class MainWindow(QMainWindow):
    """导航 + 页面栈；首次启动引导设置工作目录。"""

    def __init__(self, ctx: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self.setWindowTitle(self.tr("源重构 — 素材采集与智能去重"))
        self.resize(1180, 760)

        central = QWidget()
        root = QHBoxLayout(central)

        from PySide6.QtWidgets import QVBoxLayout as _V

        nav_box = QWidget()
        nav_layout = _V(nav_box)
        nav_layout.setContentsMargins(0, 0, 0, 0)
        self.nav_list = QListWidget()
        self.nav_list.setObjectName("navList")
        for item, _key in _NAV_KEYS:
            self.nav_list.addItem(self.tr(item))
        btn_help = QPushButton(self.tr("使用说明"))
        btn_help.setObjectName("secondaryBtn")
        btn_help.clicked.connect(self._show_help)
        nav_layout.addWidget(self.nav_list, 1)
        nav_layout.addWidget(btn_help)
        root.addWidget(nav_box)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self.nav_list.currentRowChanged.connect(self._on_nav_changed)
        self.nav_list.setCurrentRow(0)

    # ---- 页面装配 ----
    def add_page(self, widget: QWidget) -> None:
        self.stack.addWidget(widget)

    def _on_nav_changed(self, index: int) -> None:
        if 0 <= index < self.stack.count():
            self.stack.setCurrentIndex(index)

    # ---- 工作目录向导 ----
    def ensure_workdir(self) -> bool:
        """无工作目录时强制引导；返回是否就绪。"""
        workdirs = self._ctx.workdirs()
        if workdirs.is_set():
            return True
        while True:
            QMessageBox.information(
                self, "初始设置",
                "开始使用前，请先选择素材保存的工作目录。\n"
                "建议选择空间充足的磁盘分区。",
            )
            from PySide6.QtWidgets import QFileDialog

            chosen = QFileDialog.getExistingDirectory(
                self, "选择素材工作目录", "",
            )
            if not chosen:
                return False
            try:
                workdirs.set_workdir(chosen)
                return True
            except Exception as exc:
                logger.warning("工作目录无效：%s", exc)
                QMessageBox.warning(
                    self, "目录不可用",
                    f"该目录无法作为工作目录：\n{exc}",
                )

    def _show_help(self) -> None:
        HelpDialog(self).exec()
