# 下载队列视图：item_updated / task_progress 信号驱动行刷新；DL010 Toast；
# 每行提供「取消」（仅进行中可用），经 cancel_requested(row_id) 上抛。
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QLabel,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ych.ui.u6_common.platform_labels import platform_label

_STATE_TEXT = {
    "pending": "等待中",
    "running": "下载中",
    "success": "已完成",
    "failed": "失败",
    "skipped": "已跳过",
    "interrupted": "已中断",
    "canceled": "已取消",
}

# 状态语义色：成功绿 / 失败红 / 进行中蓝 / 等待灰 / 跳过橙
_STATE_COLOR = {
    "pending": "#8b95a1",
    "running": "#4c6ef5",
    "success": "#2f9e6e",
    "failed": "#e03131",
    "skipped": "#e8890c",
    "interrupted": "#e03131",
    "canceled": "#8b95a1",
}

_TERMINAL_STATES = frozenset(
    {"success", "failed", "skipped", "interrupted", "canceled"},
)


class DownloadQueueView(QWidget):
    """行 = download_task 表行；row_id → (行号, 进度条) 映射。"""

    cancel_requested = Signal(int)     # db row_id

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        self._rows: dict[int, int] = {}
        self._bars: dict[int, QProgressBar] = {}
        self._cancel_btns: dict[int, QPushButton] = {}

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["平台", "视频", "状态", "进度", "操作"],
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        root.addWidget(self.table, 1)
        hint = QLabel("下载队列（断点续传，中断后可恢复；进行中的任务可取消）")
        hint.setObjectName("muted")
        root.addWidget(hint)

    # ---- 更新 ----
    def on_item_updated(self, row_id: int, state: str, progress: float,
                        msg: str) -> None:
        if row_id < 0:
            if state == "limit_reached":
                self._ensure_note_row(f"⚠ {msg}")
            return
        row = self._ensure_row(row_id)
        status_item = QTableWidgetItem(_STATE_TEXT.get(state, state))
        color = _STATE_COLOR.get(state)
        if color:
            status_item.setForeground(QColor(color))
        self.table.setItem(row, 2, status_item)
        bar = self._bars.get(row_id)
        if bar is not None:
            bar.setValue(int(max(0.0, min(progress, 1.0)) * 100))
        if state in _TERMINAL_STATES:
            btn = self._cancel_btns.get(row_id)
            if btn is not None:
                btn.setEnabled(False)
                btn.setToolTip("任务已结束")

    def add_row_info(self, row_id: int, platform: str, title: str) -> None:
        """入队时预登记展示信息。"""
        row = self._ensure_row(row_id)
        self.table.setItem(row, 0, QTableWidgetItem(platform_label(platform)))
        self.table.setItem(row, 1, QTableWidgetItem(title))
        if self.table.item(row, 2) is None:
            self.table.setItem(row, 2, QTableWidgetItem("等待中"))

    def _ensure_row(self, row_id: int) -> int:
        if row_id in self._rows:
            return self._rows[row_id]
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem("-"))
        self.table.setItem(row, 1, QTableWidgetItem("-"))
        bar = QProgressBar()
        bar.setValue(0)
        self.table.setCellWidget(row, 3, bar)
        self._bars[row_id] = bar
        btn = QPushButton("取消")
        btn.setObjectName("secondaryBtn")
        btn.clicked.connect(
            lambda _checked=False, rid=row_id: self.cancel_requested.emit(rid),
        )
        self.table.setCellWidget(row, 4, btn)
        self._cancel_btns[row_id] = btn
        self._rows[row_id] = row
        return row

    def _ensure_note_row(self, text: str) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        note = QTableWidgetItem(text)
        self.table.setItem(row, 0, note)
        self.table.setSpan(row, 0, 1, 5)
