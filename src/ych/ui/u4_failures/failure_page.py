# 失败列表页（U4）：QTableView 四列 + 重新处理 / 清除
from __future__ import annotations

from typing import Any, ClassVar

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPersistentModelIndex, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ych.ui.u6_common.empty_state import attach_empty_state
from ych.ui.u6_common.toast import Toast

_EMPTY_INDEX = QModelIndex()

_TYPE_LABEL = {"preprocess": "预处理", "dedup": "去重", "compare": "分析",
               "download": "下载"}


class FailRecordModel(QAbstractTableModel):
    HEADERS: ClassVar[list[str]] = ["文件名", "失败原因", "错误码", "时间", "类型"]

    def __init__(self, rows: list[Any] | None = None) -> None:
        super().__init__()
        self._rows: list[Any] = list(rows or [])

    def set_rows(self, rows: list[Any]) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def row_at(self, index: QModelIndex) -> Any:
        return self._rows[index.row()]

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = _EMPTY_INDEX) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = _EMPTY_INDEX) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def data(self, index: QModelIndex | QPersistentModelIndex,
             role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if not index.isValid():
            return None
        r = self._rows[index.row()]
        col = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            vals = [r.file_name, r.fail_reason, r.error_code or "",
                    r.fail_time, _TYPE_LABEL.get(str(r.task_type), str(r.task_type))]
            return str(vals[col])
        if role == Qt.ItemDataRole.ToolTipRole and col == 1:
            return str(r.fail_reason)
        return None

    def headerData(self, section: int, orientation: Qt.Orientation,
                   role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if (orientation == Qt.Orientation.Horizontal
                and role == Qt.ItemDataRole.DisplayRole):
            return self.HEADERS[section]
        return None


class FailurePage(QWidget):
    """失败记录：一键重新处理（重建 payload 提交 M4）/ 清除选中 / 刷新。"""

    def __init__(
        self,
        fails_dao: Any,      # FailRecordDao
        scheduler: Any,      # TaskScheduler（submit_from_fail_record）
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._fails = fails_dao
        self._scheduler = scheduler
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        self._model = FailRecordModel()
        self.table = QTableView()
        self.table.setModel(self._model)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)
        header.setHighlightSections(False)
        self.table.setColumnWidth(0, 260)
        self.table.setColumnWidth(1, 300)
        self.table.setColumnWidth(2, 90)
        attach_empty_state(
            self.table, "没有失败记录",
            "处理失败的任务会集中在这里，可一键重新处理",
            is_empty=lambda: self._model.rowCount() == 0,
        )
        root.addWidget(self.table, 1)

        row = QHBoxLayout()
        btn_reprocess = QPushButton(self.tr("重新处理"))
        btn_delete = QPushButton(self.tr("清除"))
        btn_delete.setObjectName("secondaryBtn")
        btn_refresh = QPushButton(self.tr("刷新"))
        btn_refresh.setObjectName("secondaryBtn")
        btn_reprocess.clicked.connect(self._reprocess)
        btn_delete.clicked.connect(self._delete_selected)
        btn_refresh.clicked.connect(self.refresh)
        row.addWidget(btn_reprocess)
        row.addWidget(btn_delete)
        row.addStretch(1)
        row.addWidget(btn_refresh)
        root.addLayout(row)

        self.refresh()

    # ---- 槽 ----
    def refresh(self) -> None:
        self._model.set_rows(self._fails.list_recent())
        refresh_empty = getattr(self.table, "_refresh_empty_state", None)
        if refresh_empty is not None:
            refresh_empty()

    def _selected_ids(self) -> list[int]:
        ids: list[int] = []
        for idx in self.table.selectionModel().selectedRows():
            record = self._model.row_at(idx)
            ids.append(int(record.id))
        return ids

    def _reprocess(self) -> None:
        ids = self._selected_ids()
        if not ids:
            Toast.show_message(self, "请先在列表中选中要重新处理的记录")
            return
        done = 0
        for rid in ids:
            try:
                self._scheduler.submit_from_fail_record(rid)
                done += 1
            except Exception:
                continue   # 单条重建失败不阻塞其余（UI 层兜底）
        Toast.show_message(self, f"已重新提交 {done} 条任务")
        self.refresh()

    def _delete_selected(self) -> None:
        ids = self._selected_ids()
        if not ids:
            Toast.show_message(self, "请先在列表中选中要删除的记录")
            return
        answer = QMessageBox.question(
            self, "清除失败记录",
            f"确定清除选中的 {len(ids)} 条失败记录吗？\n"
            "清除后如需再次处理，请回到对应工作台重新提交。",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        for rid in ids:
            self._fails.delete(rid)
        Toast.show_message(self, f"已删除 {len(ids)} 条失败记录")
        self.refresh()
