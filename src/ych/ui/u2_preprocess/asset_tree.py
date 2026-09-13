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
        self._rows: list[Any] = []        # 最近一次数据（重翻译重建用）
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
            assert child is not None
            child.setCheckState(0, state)
            self._apply_to_children(child, state)

    def _sync_ancestors(self, item: QTreeWidgetItem) -> None:
        parent = item.parent()
        while parent is not None:
            states = []
            for i in range(parent.childCount()):
                child = parent.child(i)
                assert child is not None
                states.append(child.checkState(0))
            if all(s == Qt.CheckState.Checked for s in states):
                parent.setCheckState(0, Qt.CheckState.Checked)
            elif all(s == Qt.CheckState.Unchecked for s in states):
                parent.setCheckState(0, Qt.CheckState.Unchecked)
            else:
                parent.setCheckState(0, Qt.CheckState.PartiallyChecked)
            parent = parent.parent()

    # ---- 数据 ----
    def set_assets(self, rows: list[Any]) -> None:
        """rows: AssetRow 列表（path/category/keyword/date_str）。

        重建时保留原勾选（按完整路径匹配），供语言切换重翻译后恢复。
        """
        checked = set(self.checked_files())
        self._rows = list(rows)
        self.blockSignals(True)
        self.clear()
        tree: dict[str, dict[str, dict[str, list[Any]]]] = {}
        for r in rows:
            category = r.category or self.tr("未分类")
            keyword = r.keyword or self.tr("未命名")
            date = r.date_str or self.tr("未知日期")
            tree.setdefault(category, {}).setdefault(keyword, {}).setdefault(
                date, [],
            ).append(r)
        for category, kws in tree.items():
            n_cat = sum(
                len(paths)
                for dates in kws.values()
                for paths in dates.values()
            )
            cat_item = QTreeWidgetItem([f"{category}（{n_cat}）"])
            cat_item.setFlags(cat_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            cat_item.setCheckState(0, Qt.CheckState.Unchecked)
            for keyword, dates in kws.items():
                n_kw = sum(len(paths) for paths in dates.values())
                kw_item = QTreeWidgetItem([f"{keyword}（{n_kw}）"])
                kw_item.setFlags(kw_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                kw_item.setCheckState(0, Qt.CheckState.Unchecked)
                for _date, rws in dates.items():
                    for r in rws:
                        leaf = QTreeWidgetItem([Path(r.path).name])
                        leaf.setData(0, Qt.ItemDataRole.UserRole, r.path)
                        leaf.setFlags(
                            leaf.flags() | Qt.ItemFlag.ItemIsUserCheckable,
                        )
                        if str(r.path) in checked:
                            leaf.setCheckState(0, Qt.CheckState.Checked)
                        else:
                            leaf.setCheckState(0, Qt.CheckState.Unchecked)
                        leaf.setToolTip(0, self._leaf_tooltip(r))
                        kw_item.addChild(leaf)
                cat_item.addChild(kw_item)
            self.addTopLevelItem(cat_item)
        # 恢复勾选绕过了级联信号，这里统一把父级三态汇总正确
        for i in range(self.topLevelItemCount()):
            top = self.topLevelItem(i)
            if top is not None:
                self._recompute_parent_states(top)
        self.blockSignals(False)
        self.expandToDepth(0)

    def _recompute_parent_states(self, item: QTreeWidgetItem) -> None:
        """后序遍历：由叶子勾选态逐级汇总出父级三态（重建后校准）。"""
        states: list[Qt.CheckState] = []
        for j in range(item.childCount()):
            child = item.child(j)
            assert child is not None
            if child.childCount():
                self._recompute_parent_states(child)
            states.append(child.checkState(0))
        if not states:
            return
        if all(s == Qt.CheckState.Checked for s in states):
            item.setCheckState(0, Qt.CheckState.Checked)
        elif all(s == Qt.CheckState.Unchecked for s in states):
            item.setCheckState(0, Qt.CheckState.Unchecked)
        else:
            item.setCheckState(0, Qt.CheckState.PartiallyChecked)

    def retranslate(self) -> None:
        """语言切换：用缓存的行数据重建（勾选状态经 set_assets 保留）。"""
        if getattr(self, "_rows", None):
            self.set_assets(self._rows)

    def _leaf_tooltip(self, r: Any) -> str:
        """叶子 Tooltip：完整路径 + 可用的时长/分辨率信息。"""
        tip = str(r.path)
        duration = getattr(r, "duration_s", 0) or 0
        width = getattr(r, "width", 0) or 0
        height = getattr(r, "height", 0) or 0
        if duration:
            tip += "\n" + self.tr("时长 {d}s").format(d=f"{duration:.0f}")
            if width and height:
                tip += f" · {width}x{height}"
        return tip

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
