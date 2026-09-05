# 采集工作台（U1）：关键词栏 / 平台分区 / 筛选 / 结果列表 / 下载队列
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Protocol

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
        region: Any | None = None, platform_ids: list[str] | None = None,
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
        ai_gateway: Any | None = None,       # AiGateway.expand_keywords
        open_files: Callable[[], Path] | None = None,   # 查看文件→下载保存目录
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._coordinator = coordinator
        self._dm = download_manager
        self._history = history
        self._config = config
        self._ai_gateway = ai_gateway
        self._open_files = open_files
        self._ai_worker: Any | None = None

        root = QVBoxLayout(self)

        # ---- 顶部：关键词 + 历史词 + AI 扩展 + 搜索 ----
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
        self.btn_ai_expand = QPushButton(self.tr("AI 扩展"))
        self.btn_ai_expand.setToolTip(
            self.tr("用 AI 围绕第一个关键词扩展搜索词（需在设置页配置 AI 服务）")
        )
        self.btn_ai_expand.clicked.connect(self._on_ai_expand)
        top.addWidget(QLabel(self.tr("关键词")))
        top.addWidget(self.keyword_edit, 1)
        top.addWidget(self.history_combo)
        top.addWidget(self.btn_ai_expand)
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

        # ---- 底部：下载数量上限 + 查看文件 + 队列视图 ----
        bottom = QHBoxLayout()
        bottom.addWidget(QLabel(self.tr("单次下载数量上限")))
        self.limit_spin = QSpinBox()
        self.limit_spin.setRange(1, 200)
        default_limit = int(config.get_typed("download_limit", int)) if (
            config is not None and hasattr(config, "get_typed")
        ) else 20
        self.limit_spin.setValue(default_limit)
        bottom.addWidget(self.limit_spin)
        self.btn_open_files = QPushButton(self.tr("查看文件"))
        self.btn_open_files.setObjectName("secondaryBtn")
        self.btn_open_files.setToolTip(
            self.tr("打开下载文件的保存位置（工作目录）")
        )
        self.btn_open_files.clicked.connect(self._on_open_files)
        bottom.addWidget(self.btn_open_files)
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
        self.global_checks: dict[str, QCheckBox] = {}   # 受总开关约束
        self.stock_checks: dict[str, QCheckBox] = {}    # 素材站：始终可用

        cn_group = QWidget()
        cn_layout = QVBoxLayout(cn_group)
        cn_layout.setContentsMargins(0, 0, 0, 0)
        for pid in _CN_PLATFORMS:
            cb = QCheckBox(f"国内·{pid}")
            cb.setChecked(True)
            cn_layout.addWidget(cb)
            self.cn_checks[pid] = cb
        layout.addWidget(cn_group)

        self.foreign_master = QCheckBox("国外平台（TikTok / YouTube）")
        self.foreign_master.setToolTip(
            self.tr("开启前会自动检测外网可达性；TikTok/YouTube 目前为占位未开放"),
        )
        self.foreign_master.toggled.connect(self._on_foreign_toggled)
        layout.addWidget(self.foreign_master)
        global_group = QWidget()
        global_layout = QVBoxLayout(global_group)
        global_layout.setContentsMargins(0, 0, 0, 0)
        for pid in _GLOBAL_PLATFORMS:
            cb = QCheckBox(pid)
            cb.setEnabled(False)   # 总开关默认关：子项初始禁用
            global_layout.addWidget(cb)
            self.global_checks[pid] = cb
        layout.addWidget(global_group)

        stock_group = QWidget()
        stock_layout = QVBoxLayout(stock_group)
        stock_layout.setContentsMargins(0, 0, 0, 0)
        for pid in _STOCK_PLATFORMS:
            cb = QCheckBox(f"{pid}（免费素材站）")
            cb.setChecked(True)
            cb.setToolTip(self.tr("免费素材站可直连，不受国外总开关约束；"
                                  "需在设置页配置对应 Key"))
            stock_layout.addWidget(cb)
            self.stock_checks[pid] = cb
        layout.addWidget(stock_group)
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
        selected = [
            pid
            for group in (self.cn_checks, self.stock_checks, self.global_checks)
            for pid, cb in group.items()
            if cb.isChecked()
        ]
        self._coordinator.search_multi(
            keywords, self.filter_panel.to_filters(), 30, platform_ids=selected,
        )
        if self._history is not None and keywords:
            self._history.record(keywords[0], [])

    # ---- AI 关键词扩展 ----
    def _on_ai_expand(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        if self._ai_gateway is None or not self._ai_gateway.is_configured():
            QMessageBox.information(
                self,
                self.tr("AI 扩展"),
                self.tr(
                    "尚未配置 AI 服务：请到「设置 → AI 服务」添加服务"
                    "（填接口地址与 API Key，拉取模型后设为默认）。",
                ),
            )
            return
        raw = self.keyword_edit.text().strip()
        terms = [k for k in raw.replace("，", ",").split(",") if k.strip()]
        if not terms:
            QMessageBox.information(
                self, self.tr("AI 扩展"), self.tr("请先输入一个主关键词。"),
            )
            return
        if self._ai_worker is not None:
            return
        origin = terms[0].strip()
        self.btn_ai_expand.setEnabled(False)
        self.btn_ai_expand.setText(self.tr("扩展中…"))
        from ych.ui.u6_common.llm_worker import LlmWorker

        worker = LlmWorker(
            lambda: self._ai_gateway.expand_keywords(origin)   # type: ignore[union-attr]
        )
        self._ai_worker = worker
        worker.done.connect(self._on_ai_expand_done)
        worker.failed.connect(self._on_ai_expand_failed)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_ai_expand_done(self, result: object) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import (
            QDialog,
            QDialogButtonBox,
            QListWidget,
            QListWidgetItem,
            QMessageBox,
        )

        self.btn_ai_expand.setEnabled(True)
        self.btn_ai_expand.setText(self.tr("AI 扩展"))
        self._ai_worker = None
        words = [str(w) for w in result] if isinstance(result, list) else []
        if not words:
            QMessageBox.information(
                self,
                self.tr("AI 扩展"),
                self.tr("没有扩展出关键词，请换个主词或检查模型。"),
            )
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(self.tr("选择要追加的搜索词"))
        lay = QVBoxLayout(dlg)
        lst = QListWidget()
        for w in words:
            item = QListWidgetItem(w)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            lst.addItem(item)
        lay.addWidget(lst)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        lay.addWidget(buttons)
        dlg.resize(320, 420)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        picked = [
            lst.item(i).text()
            for i in range(lst.count())
            if lst.item(i).checkState() == Qt.CheckState.Checked
        ]
        self._apply_expanded_keywords(picked)

    def _on_ai_expand_failed(self, msg: str) -> None:
        from PySide6.QtWidgets import QMessageBox

        self.btn_ai_expand.setEnabled(True)
        self.btn_ai_expand.setText(self.tr("AI 扩展"))
        self._ai_worker = None
        QMessageBox.warning(self, self.tr("AI 扩展失败"), msg)

    def _apply_expanded_keywords(self, words: list[str]) -> None:
        """把勾选的扩展词追加到关键词框（与已有词去重合并）。"""
        existing = [
            k.strip()
            for k in self.keyword_edit.text().replace("，", ",").split(",")
            if k.strip()
        ]
        merged = list(existing)
        for w in words:
            w = w.strip()
            if w and w not in merged:
                merged.append(w)
        self.keyword_edit.setText("，".join(merged))

    def _on_foreign_toggled(self, checked: bool) -> None:
        """总开关仅约束 TikTok/YouTube；首次开启先经外网检测确认。

        foreign_platforms_enabled 未开启时回弹并发出
        foreign_switch_requested，由外部（app 装配）异步检测后调
        confirm_foreign_enable 完成开启。
        """
        for cb in self.global_checks.values():
            cb.setEnabled(checked)
        if not checked:
            return
        if self._config is not None and not bool(
            self._config.get("foreign_platforms_enabled")
        ):
            self.foreign_master.setChecked(False)
            self.foreign_switch_requested.emit()

    def confirm_foreign_enable(self, reachable: bool) -> None:
        """外部检测回调：可达→置位总开关并持久化；不可达→回弹并提示。"""
        from PySide6.QtWidgets import QMessageBox

        if not reachable:
            self.foreign_master.setChecked(False)
            QMessageBox.warning(
                self, "外网不可达",
                "当前网络环境无法访问外网平台，请检查 VPN/代理设置",
            )
            return
        if self._config is not None:
            self._config.set("foreign_platforms_enabled", True)
        self.foreign_master.setChecked(True)

    def on_search_finished(self, result_set: Any) -> None:
        self.result_list.set_results(
            list(result_set.items), result_set.keyword,
            list(result_set.unavailable_platforms),
        )

    def _on_download(self, metas: list[Any], keyword: str) -> None:
        if self._dm is None:
            return
        self._dm.enqueue_downloads(metas, keyword, int(self.limit_spin.value()))

    def _on_open_files(self) -> None:
        """打开下载保存位置（工作目录）；未设置时给出引导提示。"""
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtWidgets import QMessageBox

        if self._open_files is None:
            return
        try:
            path = Path(self._open_files())
        except Exception:
            QMessageBox.information(
                self,
                self.tr("查看文件"),
                self.tr("尚未设置素材工作目录：请先完成初始引导，"
                        "或到「设置」页选择保存位置。"),
            )
            return
        if not path.is_dir():
            QMessageBox.information(
                self, self.tr("查看文件"),
                self.tr("保存位置不存在：") + str(path),
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
