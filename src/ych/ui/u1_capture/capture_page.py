# 采集工作台（U1）：关键词栏 / 平台分区 / 筛选 / 结果列表 / 下载队列
from __future__ import annotations

from typing import Any, Protocol

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ych.ui.u1_capture.download_queue_view import DownloadQueueView
from ych.ui.u1_capture.filter_panel import FilterPanel
from ych.ui.u1_capture.result_list import ResultList

_CN_PLATFORMS = ("douyin", "kuaishou", "bilibili", "xiaohongshu")
_GLOBAL_PLATFORMS = ("tiktok", "youtube")
_STOCK_PLATFORMS = ("pexels", "pixabay")


class _CoordinatorLike(Protocol):
    def search_multi(
        self, keywords: list[str], filters: Any,
        per_platform_limit: int, token: Any | None = None,
    ) -> None: ...


class _DownloadManagerLike(Protocol):
    def enqueue_downloads(
        self, metas: list[Any], keyword: str, limit: int,
    ) -> int: ...

    item_updated: Any


class CapturePage(QWidget):
    """搜索→筛选→下载 三步工作台。"""

    foreign_switch_requested = Signal()      # UI 想开国外总开关时先检测

    def __init__(
        self,
        coordinator: _CoordinatorLike | None = None,
        download_manager: _DownloadManagerLike | None = None,
        history: Any | None = None,          # HistoryService.suggestions
        config: Any | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._coordinator = coordinator
        self._dm = download_manager
        self._history = history
        self._config = config

        root = QVBoxLayout(self)

        # ---- 顶部：关键词 + 历史词 + 搜索 ----
        top = QHBoxLayout()
        self.history_combo = QComboBox()
        self.history_combo.addItem(self.tr("历史搜索词"))
        if history is not None:
            for kw in history.suggestions(limit=10):
                self.history_combo.addItem(kw)
        self.keyword_edit = QLineEdit()
        self.keyword_edit.setPlaceholderText(
            self.tr("输入关键词，多个用逗号分隔")
        )
        btn_search = QPushButton(self.tr("搜索"))
        btn_search.clicked.connect(self._on_search)
        top.addWidget(QLabel(self.tr("关键词")))
        top.addWidget(self.keyword_edit, 1)
        top.addWidget(self.history_combo)
        top.addWidget(btn_search)
        root.addLayout(top)

        # ---- 中部：平台分区 + 筛选 ----
        middle = QHBoxLayout()
        middle.addWidget(self._build_platform_box())
        self.filter_panel = FilterPanel()
        middle.addWidget(self.filter_panel)
        root.addLayout(middle)

        # ---- 结果列表 ----
        self.result_list = ResultList()
        self.result_list.download_requested.connect(self._on_download)
        root.addWidget(self.result_list, 1)

        # ---- 底部：下载数量上限 + 队列视图 ----
        bottom = QHBoxLayout()
        bottom.addWidget(QLabel(self.tr("单次下载数量上限")))
        self.limit_spin = QSpinBox()
        self.limit_spin.setRange(1, 200)
        default_limit = int(config.get_typed("download_limit", int)) if (
            config is not None and hasattr(config, "get_typed")
        ) else 20
        self.limit_spin.setValue(default_limit)
        bottom.addWidget(self.limit_spin)
        bottom.addStretch(1)
        root.addLayout(bottom)

        self.queue_view = DownloadQueueView()
        root.addWidget(self.queue_view, 1)

        # 历史词选择回填输入框
        self.history_combo.activated.connect(self._on_history_activated)

    # ---- 构建 ----
    def _build_platform_box(self) -> QGroupBox:
        box = QGroupBox(self.tr("采集平台"))
        layout = QVBoxLayout(box)
        self.cn_checks: dict[str, QCheckBox] = {}
        self.global_checks: dict[str, QCheckBox] = {}

        cn_group = QWidget()
        cn_layout = QVBoxLayout(cn_group)
        cn_layout.setContentsMargins(0, 0, 0, 0)
        for pid in _CN_PLATFORMS:
            cb = QCheckBox(f"国内·{pid}")
            cb.setChecked(True)
            cn_layout.addWidget(cb)
            self.cn_checks[pid] = cb
        layout.addWidget(cn_group)

        self.foreign_master = QCheckBox("国外平台（含 Pexels/Pixabay）")
        self.foreign_master.toggled.connect(self._on_foreign_toggled)
        layout.addWidget(self.foreign_master)
        global_group = QWidget()
        global_layout = QVBoxLayout(global_group)
        global_layout.setContentsMargins(0, 0, 0, 0)
        for pid in _GLOBAL_PLATFORMS:
            cb = QCheckBox(pid)
            global_layout.addWidget(cb)
            self.global_checks[pid] = cb
        for pid in _STOCK_PLATFORMS:
            cb = QCheckBox(f"{pid}（免费素材站）")
            global_layout.addWidget(cb)
            self.global_checks[pid] = cb
        layout.addWidget(global_group)
        return box

    # ---- 槽 ----
    def _on_history_activated(self, index: int) -> None:
        text = self.history_combo.itemText(index)
        if index > 0 and text:
            self.keyword_edit.setText(text)

    def _on_search(self) -> None:
        raw = self.keyword_edit.text().strip()
        if not raw or self._coordinator is None:
            return
        keywords = [k.strip() for k in raw.replace("，", ",").split(",") if k.strip()]
        self._coordinator.search_multi(keywords, self.filter_panel.to_filters(), 30)
        if self._history is not None and keywords:
            self._history.record(keywords[0], [])

    def _on_foreign_toggled(self, checked: bool) -> None:
        """打开国外总开关前先做外网能力检测；不可达则开关回弹。

        实际检测由外部接线（AppContext 注入 checker）通过
        foreign_switch_requested 信号完成；本页只负责回弹。
        """
        for cb in self.global_checks.values():
            cb.setEnabled(checked)
        if not checked:
            return
        if self._config is not None:
            allowed = bool(self._config.get("foreign_platforms_enabled"))
            self.foreign_master.setChecked(checked and allowed)

    def confirm_foreign_enable(self, reachable: bool) -> None:
        """外部检测回调：reachable=False 时回弹并提示。"""
        from PySide6.QtWidgets import QMessageBox

        if not reachable:
            self.foreign_master.setChecked(False)
            QMessageBox.warning(
                self, "外网不可达",
                "当前网络环境无法访问外网素材站，请检查代理设置",
            )

    def on_search_finished(self, result_set: Any) -> None:
        self.result_list.set_results(
            list(result_set.items), result_set.keyword,
            list(result_set.unavailable_platforms),
        )

    def _on_download(self, metas: list[Any], keyword: str) -> None:
        if self._dm is None:
            return
        self._dm.enqueue_downloads(metas, keyword, int(self.limit_spin.value()))
