# 素材树（U2）：M5 索引数据源；分类/关键词/日期三级勾选
from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem, QWidget


class AssetTree(QTreeWidget):
    """set_assets(rows) → 三级树（大类/关键词/日期）；checked_files() 汇总。"""

    selection_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setHeaderHidden(True)
        self._cascading = False           # 程序化改勾选态期间抑制递归
        self.itemChanged.connect(self._on_item_changed)

    # ---- 勾选级联：父→子全选/全不选，子→父三态汇总 ----
    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._cascading or column != 0:
            return
        self._cascading = True
        try:
            self._apply_to_children(item, item.checkState(0))
            self._sync_ancestors(item)
        finally:
            self._cascading = False
        self.selection_changed.emit()

    def _apply_to_children(
        self, item: QTreeWidgetItem, state: Qt.CheckState,
    ) -> None:
        for i in range(item.childCount()):
            child = item.child(i)
            child.setCheckState(0, state)
            self._apply_to_children(child, state)

    def _sync_ancestors(self, item: QTreeWidgetItem) -> None:
        parent = item.parent()
        while parent is not None:
            states = [
                parent.child(i).checkState(0)
                for i in range(parent.childCount())
            ]
            if all(s == Qt.CheckState.Checked for s in states):
                parent.setCheckState(0, Qt.CheckState.Checked)
            elif all(s == Qt.CheckState.Unchecked for s in states):
                parent.setCheckState(0, Qt.CheckState.Unchecked)
            else:
                parent.setCheckState(0, Qt.CheckState.PartiallyChecked)
            parent = parent.parent()

    # ---- 数据 ----
    def set_assets(self, rows: list[Any]) -> None:
        """rows: AssetRow 列表（path/category/keyword/date_str）。"""
        self.blockSignals(True)
        self.clear()
        tree: dict[str, dict[str, dict[str, list[str]]]] = {}
        for r in rows:
            category = r.category or "未分类"
            keyword = r.keyword or "未命名"
            date = r.date_str or "未知日期"
            tree.setdefault(category, {}).setdefault(keyword, {}).setdefault(
                date, []
            ).append(r.path)
        for category, kws in tree.items():
            cat_item = QTreeWidgetItem([category])
            cat_item.setFlags(cat_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            cat_item.setCheckState(0, Qt.CheckState.Unchecked)
            for keyword, dates in kws.items():
                kw_item = QTreeWidgetItem([keyword])
                kw_item.setFlags(kw_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                kw_item.setCheckState(0, Qt.CheckState.Unchecked)
                for _date, paths in dates.items():
                    for p in paths:
                        leaf = QTreeWidgetItem([Path(p).name])
                        leaf.setData(0, Qt.ItemDataRole.UserRole, p)
                        leaf.setFlags(leaf.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                        leaf.setCheckState(0, Qt.CheckState.Unchecked)
                        kw_item.addChild(leaf)
                cat_item.addChild(kw_item)
            self.addTopLevelItem(cat_item)
        self.blockSignals(False)
        self.expandToDepth(0)

    # ---- 汇总 ----
    def checked_files(self) -> list[str]:
        out: list[str] = []

        def _collect(item: QTreeWidgetItem | None) -> None:
            if item is None:
                return
            state = item.checkState(0)
            if item.childCount() == 0:
                if state == Qt.CheckState.Checked:
                    p = item.data(0, Qt.ItemDataRole.UserRole)
                    if p:
                        out.append(str(p))
                return
            for i in range(item.childCount()):
                _collect(item.child(i))

        for top_i in range(self.topLevelItemCount()):
            _collect(self.topLevelItem(top_i))
        return out

    def select_all(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked

        def _apply(item: QTreeWidgetItem | None) -> None:
            if item is None:
                return
            item.setCheckState(0, state)
            for i in range(item.childCount()):
                _apply(item.child(i))

        self.blockSignals(not checked)   # 全选操作后发一次变更即可
        for top_i in range(self.topLevelItemCount()):
            _apply(self.topLevelItem(top_i))
        self.blockSignals(False)
        self.selection_changed.emit()
