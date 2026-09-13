# 失败列表页（U4）：QTableView 四列 + 重新处理 / 清除
from __future__ import annotations

import logging
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

logger = logging.getLogger("ych.ui.u4")


class FailRecordModel(QAbstractTableModel):
    HEADERS: ClassVar[list[str]] = ["文件名", "失败原因", "错误码", "时间", "类型"]

    def __init__(self, rows: list[Any] | None = None) -> None:
        super().__init__()
        self._rows: list[Any] = list(rows or [])
        self.headers = list(self.HEADERS)

    def retranslate(self) -> None:
        """语言切换：重译表头并通知视图刷新。"""
        self.headers = [
            self.tr("文件名"), self.tr("失败原因"), self.tr("错误码"),
            self.tr("时间"), self.tr("类型"),
        ]
        self.headerDataChanged.emit(Qt.Orientation.Horizontal, 0,
                                    len(self.headers) - 1)

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
            type_label = {
                "preprocess": self.tr("预处理"),
                "dedup": self.tr("去重"),
                "compare": self.tr("分析"),
                "download": self.tr("下载"),
            }.get(str(r.task_type), str(r.task_type))
            vals = [r.file_name, r.fail_reason, r.error_code or "",
                    r.fail_time, type_label]
            return str(vals[col])
        if role == Qt.ItemDataRole.ToolTipRole and col == 1:
            return str(r.fail_reason)
        return None

    def headerData(self, section: int, orientation: Qt.Orientation,
                   role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if (orientation == Qt.Orientation.Horizontal
                and role == Qt.ItemDataRole.DisplayRole):
            return self.headers[section]
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
        self._model.retranslate()
        self._empty = attach_empty_state(
            self.table, self.tr("没有失败记录"),
            self.tr("处理失败的任务会集中在这里，可一键重新处理"),
            is_empty=lambda: self._model.rowCount() == 0,
        )
        root.addWidget(self.table, 1)

        row = QHBoxLayout()
        self.btn_reprocess = QPushButton(self.tr("重新处理"))
        self.btn_delete = QPushButton(self.tr("清除"))
        self.btn_delete.setObjectName("secondaryBtn")
        self.btn_refresh = QPushButton(self.tr("刷新"))
        self.btn_refresh.setObjectName("secondaryBtn")
        self.btn_reprocess.clicked.connect(self._reprocess)
        self.btn_delete.clicked.connect(self._delete_selected)
        self.btn_refresh.clicked.connect(self.refresh)
        row.addWidget(self.btn_reprocess)
        row.addWidget(self.btn_delete)
        row.addStretch(1)
        row.addWidget(self.btn_refresh)
        root.addLayout(row)

        self.refresh()

    # ---- 槽 ----
    def refresh(self) -> None:
        try:
            rows = self._fails.list_recent()
        except Exception as exc:
            logger.exception("失败记录读取失败")
            Toast.show_message(
                self, self.tr("读取失败记录出错：{msg}").format(msg=exc),
                error=True,
            )
            return
        self._model.set_rows(rows)
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
            Toast.show_message(self, self.tr("请先在列表中选中要重新处理的记录"))
            return
        done = 0
        failed: list[str] = []
        for rid in ids:
            try:
                self._scheduler.submit_from_fail_record(rid)
                done += 1
            except Exception as exc:
                # 单条重建失败不阻塞其余，但必须让用户知道哪条没提交
                logger.warning("失败记录 %s 重新提交出错：%s", rid, exc)
                failed.append(f"#{rid}: {exc}")
        if failed:
            Toast.show_message(
                self,
                self.tr("已重新提交 {n} 条，{m} 条失败：{detail}").format(
                    n=done, m=len(failed), detail="；".join(failed[:3]),
                ),
                error=True, timeout_ms=8000,
            )
        else:
            Toast.show_message(
                self, self.tr("已重新提交 {} 条任务").format(done),
            )
        self.refresh()

    def _delete_selected(self) -> None:
        ids = self._selected_ids()
        if not ids:
            Toast.show_message(self, self.tr("请先在列表中选中要删除的记录"))
            return
        answer = QMessageBox.question(
            self, self.tr("清除失败记录"),
            self.tr("确定清除选中的 {n} 条失败记录吗？\n"
                    "清除后如需再次处理，请回到对应工作台重新提交。").format(
                        n=len(ids)),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        deleted = 0
        for rid in ids:
            try:
                self._fails.delete(rid)
                deleted += 1
            except Exception:
                logger.exception("失败记录 %s 删除出错", rid)
        if deleted < len(ids):
            Toast.show_message(
                self,
                self.tr("已删除 {n} 条，{m} 条删除失败，请重试").format(
                    n=deleted, m=len(ids) - deleted,
                ),
                error=True,
            )
        else:
            Toast.show_message(
                self, self.tr("已删除 {} 条失败记录").format(deleted),
            )
        self.refresh()

    def retranslate(self) -> None:
        """语言切换：按钮/空状态/表头重翻译（表内容为数据保持原样）。"""
        self.btn_reprocess.setText(self.tr("重新处理"))
        self.btn_delete.setText(self.tr("清除"))
        self.btn_refresh.setText(self.tr("刷新"))
        self._empty.set_texts(
            self.tr("没有失败记录"),
            self.tr("处理失败的任务会集中在这里，可一键重新处理"),
        )
        self._model.retranslate()
