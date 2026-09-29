# 设置页（U5）：通用/网络/服务(AI+素材站)/高级 分组。
# 服务区为 CC Switch 式交互：列表页展示"用哪个"，添加/编辑跳转独立配置页。
from __future__ import annotations

import contextlib
import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Qt, QTimer, QUrl
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QDesktopServices,
    QIcon,
    QPainter,
    QPixmap,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ych.services.s4_net.http_client import HttpClient, split_proxy_address
from ych.services.s4_net.proxy_detect import detect_local_proxy
from ych.ui.u5_settings.ai_presets import AI_PRESETS
from ych.ui.u5_settings.net_check_dialog import NetCheckDialog
from ych.ui.u6_common.flow_layout import FlowLayout
from ych.ui.u6_common.llm_worker import LlmWorker

_GREEN, _ORANGE, _RED, _GRAY = "#16a34a", "#d97706", "#dc2626", "#6b7280"

logger = logging.getLogger("ych.ui.u5")

if TYPE_CHECKING:
    from PySide6.QtCore import Signal

    from ych.services.s2_ai.model_downloader import ModelSpec

    class _ConfigLike(QObject):
        changed: Signal

        def get(self, key: str) -> object: ...
        def set(self, key: str, value: object) -> None: ...
        def secret_get(self, key: str) -> str: ...
        def secret_set(self, key: str, value: str) -> None: ...
        def secret_delete(self, key: str) -> None: ...

    class _I18nLike(QObject):
        locale_changed: Signal

        def switch_locale(self, locale: str) -> None: ...
        def available_locales(self) -> list[str]: ...

    class _NetCheckerLike(QObject):
        def check(self, force: bool = False) -> object: ...

    class _AiGatewayLike(QObject):
        def list_models(
            self, service_id: str, base_url: str, api_key: str | None = None,
        ) -> list[str]: ...

    class _ModelDownloaderLike(QObject):
        progress: dict[str, float]

        def all_specs(self) -> list[ModelSpec]: ...

        def spec(self, key: str) -> ModelSpec: ...

        def exists(self, key: str) -> bool: ...

        def size_of(self, key: str) -> int: ...

        def path_of(self, key: str) -> Path: ...

        def download(self, key: str, token: object | None = None) -> Path: ...

        def import_file(self, key: str, src: Path) -> Path: ...

else:
    _ConfigLike = QObject
    _I18nLike = QObject
    _NetCheckerLike = QObject
    _AiGatewayLike = QObject
    _ModelDownloaderLike = QObject

# 素材站（keyring 账户名, 显示名）
_STOCK_SITES: tuple[tuple[str, str], ...] = (
    ("pexels", "Pexels"),
    ("pixabay", "Pixabay"),
)

_PAGE_LIST, _PAGE_AI_EDIT, _PAGE_KEY_EDIT = 0, 1, 2


def _glyph_icon(text: str) -> QIcon:
    """Unicode 字形 → 图标（免引入图标资源文件）。"""
    pm = QPixmap(18, 18)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setPen(QColor(_GRAY))
    font = painter.font()
    font.setPixelSize(13)
    painter.setFont(font)
    painter.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, text)
    painter.end()
    return QIcon(pm)


def _add_password_visibility_toggle(edit: QLineEdit, tooltip: str) -> QAction:
    """密码框尾部加「眼睛」动作：点击切换明文/密文，便于核对是否输错。

    QLineEdit 的尾部动作自带点击与悬停态；勾选态=明文（🙈），默认=密文（👁）。
    """
    action = QAction(_glyph_icon("👁"), tooltip, edit)
    action.setToolTip(tooltip)
    action.setCheckable(True)

    def _toggle(checked: bool) -> None:
        edit.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        )
        glyph = "🙈" if checked else "👁"
        action.setIcon(_glyph_icon(glyph))
        action.setToolTip(tooltip)

    action.toggled.connect(_toggle)
    edit.addAction(action, QLineEdit.ActionPosition.TrailingPosition)
    return action


class SettingsPage(QWidget):
    """表单分组：通用(语言/工作目录)/网络(代理+外网检测)/服务/高级。"""

    def __init__(
        self,
        config: _ConfigLike,
        i18n: _I18nLike | None = None,
        net_checker: _NetCheckerLike | None = None,
        ai_gateway: _AiGatewayLike | None = None,
        http: HttpClient | None = None,
        model_downloader: _ModelDownloaderLike | None = None,
        workdirs: Any | None = None,           # WorkDirManager（改目录即时生效）
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._i18n = i18n
        self._net_checker = net_checker
        self._ai_gateway = ai_gateway
        self._http = http
        self._model_downloader = model_downloader
        self._workdirs = workdirs
        self._ai_workers: list[LlmWorker] = []
        self._editing_ai_id: str | None = None     # None=新增，否则为编辑
        self._editing_stock_pid: str = "pexels"
        # 外层滚动容器：窗口高度不足时整页滚动，而不是把表单压扁
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        page_scroll = QScrollArea()
        page_scroll.setWidgetResizable(True)
        page_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        content = QWidget()
        root = QVBoxLayout(content)

        # ---- 通用 ----
        self._group_general = general = QGroupBox(self.tr("通用"))
        form_g = QFormLayout(general)
        self._form_general = form_g
        self.lang_combo = QComboBox()
        locales = ["zh_CN"]
        if i18n is not None:
            for loc in i18n.available_locales():
                if loc not in locales:
                    locales.append(loc)
        self.lang_combo.addItems(locales)
        current = str(config.get("language") or "zh_CN")
        idx = self.lang_combo.findText(current)
        if idx >= 0:
            self.lang_combo.setCurrentIndex(idx)

        # 界面主题：跟随系统 / 浅色 / 深色（切换即时生效）
        self.theme_combo = QComboBox()
        for label, val in ((self.tr("跟随系统"), "system"),
                           (self.tr("浅色"), "light"),
                           (self.tr("深色"), "dark")):
            self.theme_combo.addItem(label, val)
        saved_theme = str(config.get("theme") or "system")
        t_idx = self.theme_combo.findData(saved_theme)
        if t_idx >= 0:
            self.theme_combo.setCurrentIndex(t_idx)
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)

        row_dir = QHBoxLayout()
        self.workdir_edit = QLineEdit(str(config.get("workdir") or ""))
        self.btn_pick_workdir = QPushButton(self.tr("选择…"))
        self.btn_pick_workdir.setObjectName("secondaryBtn")
        self.btn_pick_workdir.clicked.connect(self._pick_workdir)
        row_dir.addWidget(self.workdir_edit)
        row_dir.addWidget(self.btn_pick_workdir)
        form_g.addRow(self.tr("界面语言"), self.lang_combo)
        form_g.addRow(self.tr("界面主题"), self.theme_combo)
        self._workdir_row = row_dir
        form_g.addRow(self.tr("工作目录"), row_dir)
        root.addWidget(general)

        # ---- 网络 ----
        self._group_network = network = QGroupBox(self.tr("网络"))
        form_n = QFormLayout(network)
        self._form_network = form_n
        self.proxy_check = QCheckBox(self.tr("启用代理"))
        # 单框地址：可整段粘贴 "IP:端口" / "http://IP:端口"，失焦自动拆解
        # 归一化（此前 host/port 两个框，整段粘贴无处安放且端口留 0 导致
        # 代理被静默停用，表现为"填对了也不让用"）
        _saved_host = str(config.get("proxy_host") or "")
        _saved_port = config.get("proxy_port")
        _parsed = split_proxy_address(_saved_host)
        if _parsed is not None and _parsed[0]:
            _saved_host, _saved_port = _parsed[0], (
                _parsed[1] or (_saved_port if isinstance(_saved_port, int) else 0)
            )
        _saved_port = _saved_port if isinstance(_saved_port, int) else 0
        self.proxy_edit = QLineEdit(
            f"{_saved_host}:{_saved_port}" if _saved_host and _saved_port
            else _saved_host
        )
        row_proxy = QHBoxLayout()
        row_proxy.addWidget(self.proxy_edit)
        self.proxy_auto_btn = QPushButton(self.tr("自动检测"))
        self.proxy_auto_btn.setObjectName("secondaryBtn")
        self.proxy_auto_btn.setToolTip(
            self.tr("自动探测系统代理与常见本地端口（Clash/v2rayN 等），"
                    "验证可通外网后自动填入并启用"),
        )
        self.proxy_auto_btn.clicked.connect(self._auto_detect_proxy)
        row_proxy.addWidget(self.proxy_auto_btn)
        self.net_btn = QPushButton(self.tr("网络检测"))
        self.net_btn.setObjectName("secondaryBtn")
        self.net_btn.clicked.connect(self._open_net_check)
        form_n.addRow(self.proxy_check)
        self._proxy_row = row_proxy
        form_n.addRow(self.tr("代理地址"), row_proxy)
        self.proxy_hint = QLabel(
            self.tr("可整段粘贴代理地址，支持 IP:端口 或 http://IP:端口"
                    "（Clash 默认 127.0.0.1:7890，v2rayN 默认 10809）。"
                    "VPN 的订阅链接不是代理地址。"
                    "若 VPN 使用 TUN/系统代理模式，无需启用本项。"),
        )
        self.proxy_hint.setWordWrap(True)
        self.proxy_hint.setStyleSheet(f"color: {_GRAY};")
        form_n.addRow("", self.proxy_hint)
        self.proxy_status = QLabel("")
        self.proxy_status.setWordWrap(True)
        form_n.addRow("", self.proxy_status)
        form_n.addRow("", self.net_btn)
        root.addWidget(network)

        # ---- 服务（素材站 + AI，CC Switch 式列表/配置页）----
        self._group_services = services_box = QGroupBox(
            self.tr("服务（素材站与 AI）"),
        )
        services_lay = QVBoxLayout(services_box)
        self._stack = QStackedWidget()
        services_lay.addWidget(self._stack)
        self._stack.addWidget(self._build_service_list_page())
        self._stack.addWidget(self._build_ai_edit_page())
        self._stack.addWidget(self._build_stock_key_page())
        root.addWidget(services_box)

        # ---- AI 模型（状态 / 按需下载）----
        self._group_models = models_box = QGroupBox(
            self.tr("AI 模型（去水印 / 去字幕 / 重复度）"),
        )
        models_lay = QVBoxLayout(models_box)
        self.model_table = QTableWidget(0, 4)
        self.model_table.setHorizontalHeaderLabels([
            self.tr("模型文件"), self.tr("用途"), self.tr("状态"),
            self.tr("操作"),
        ])
        self.model_table.verticalHeader().setVisible(False)
        self.model_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch,
        )
        self.model_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.model_table.setMinimumHeight(160)
        models_lay.addWidget(self.model_table)
        models_row = QHBoxLayout()
        self.model_hint = QLabel("")
        self.model_hint.setWordWrap(True)
        self.model_hint.setStyleSheet(f"color: {_GRAY};")
        self.btn_open_models = QPushButton(self.tr("打开模型目录"))
        self.btn_open_models.setObjectName("secondaryBtn")
        self.btn_open_models.clicked.connect(self._open_models_dir)
        models_row.addWidget(self.model_hint, 1)
        models_row.addWidget(self.btn_open_models)
        models_lay.addLayout(models_row)
        root.addWidget(models_box)

        self._downloading_key: str | None = None
        self._importing_key: str | None = None
        self._worker_keys: dict[QObject, str] = {}
        self._model_dl_cells: dict[str, QPushButton] = {}
        self._model_workers: list[LlmWorker] = []
        self._model_token: Any | None = None
        self._model_timer = QTimer(self)
        self._model_timer.setInterval(400)
        self._model_timer.timeout.connect(self._refresh_model_rows)
        self._refresh_model_rows()

        # ---- 高级 ----
        self._group_advanced = advanced = QGroupBox(self.tr("高级"))
        form_a = QFormLayout(advanced)
        self._form_advanced = form_a
        self.download_conc = QLineEdit(str(config.get("download_concurrency")))
        self.process_conc = QLineEdit(str(config.get("process_concurrency")))
        self.max_retry = QLineEdit(str(config.get("max_retry")))
        self.readonly_check = QCheckBox(self.tr("原始素材只读保护"))
        self.readonly_check.setChecked(bool(config.get("readonly_protect_raw")))
        form_a.addRow(self.tr("下载并行数"), self.download_conc)
        form_a.addRow(self.tr("处理并行数"), self.process_conc)
        form_a.addRow(self.tr("失败重试次数"), self.max_retry)
        form_a.addRow("", self.readonly_check)
        root.addWidget(advanced)
        root.addStretch(1)
        page_scroll.setWidget(content)
        outer.addWidget(page_scroll)

        # ---- 双向绑定 ----
        self.lang_combo.currentTextChanged.connect(self._on_lang_changed)
        self.workdir_edit.editingFinished.connect(self._on_workdir_edited)
        self.proxy_check.toggled.connect(lambda v: config.set("proxy_enabled", v))
        self.proxy_edit.editingFinished.connect(self._commit_proxy_address)
        self.download_conc.editingFinished.connect(
            lambda: self._commit_int_setting(self.download_conc,
                                             "download_concurrency", 3)
        )
        self.process_conc.editingFinished.connect(
            lambda: self._commit_int_setting(self.process_conc,
                                             "process_concurrency", 2)
        )
        self.max_retry.editingFinished.connect(
            lambda: self._commit_int_setting(self.max_retry, "max_retry", 2)
        )
        self.readonly_check.toggled.connect(
            lambda v: config.set("readonly_protect_raw", v)
        )
        config.changed.connect(self._on_config_changed)
        self.proxy_check.setChecked(bool(config.get("proxy_enabled")))
        self.readonly_check.setChecked(bool(config.get("readonly_protect_raw")))

    # ================= AI 模型 =================
    def _refresh_model_rows(self) -> None:
        """按清单刷新模型状态表；下载中由 QTimer 周期调用。"""
        if self._model_downloader is None:
            return
        dl = self._model_downloader
        specs = dl.all_specs()
        if self.model_table.rowCount() != len(specs):
            self.model_table.setRowCount(len(specs))
        for row, spec in enumerate(specs):
            self.model_table.setItem(
                row, 0, QTableWidgetItem(str(spec.file)),
            )
            self.model_table.setItem(
                row, 1, QTableWidgetItem(str(spec.desc)),
            )
            ratio = dl.progress.get(str(spec.key))
            if dl.exists(str(spec.key)):
                status = self.tr("✓ 已就绪（{mb} MB）").format(
                    mb=f"{dl.size_of(str(spec.key)) / 1048576:.1f}",
                )
                color = _GREEN
            elif self._importing_key == str(spec.key):
                status = self.tr("导入中：复制并校验模型契约…")
                color = "#2563eb"
            elif ratio is not None:
                status = self.tr("下载中 {pct}%").format(pct=int(ratio * 100))
                color = "#2563eb"
            elif not spec.urls:
                status = self.tr("缺失（无公开下载源；点「导入…」选择模型文件）")
                color = _GRAY
            else:
                status = self.tr("未下载")
                color = _ORANGE
            s_item = QTableWidgetItem(status)
            s_item.setForeground(QBrush(QColor(color)))
            self.model_table.setItem(row, 2, s_item)
            if str(spec.key) not in self._model_dl_cells:
                btn = QPushButton(self.tr("下载"))
                btn.setObjectName("secondaryBtn")
                btn.clicked.connect(
                    lambda _checked=False, k=str(spec.key):
                        self._on_model_button(k),
                )
                self.model_table.setCellWidget(row, 3, btn)
                self._model_dl_cells[str(spec.key)] = btn
            btn = self._model_dl_cells[str(spec.key)]
            if self._downloading_key == str(spec.key):
                btn.setText(self.tr("取消"))
                btn.setEnabled(True)
            elif not spec.urls:
                # 无下载源 ≠ 不能添加：提供手动导入（复制+契约校验）
                btn.setText(self.tr("导入…"))
                btn.setEnabled(
                    self._downloading_key is None and self._importing_key is None
                )
            else:
                btn.setText(self.tr("下载"))
                btn.setEnabled(
                    self._downloading_key is None and self._importing_key is None
                )

    def _on_model_button(self, key: str) -> None:
        """下载中点同一行 = 取消（保留断点）；无源模型 = 导入；其余发起下载。"""
        if self._downloading_key == key and self._model_token is not None:
            self._model_token.cancel()
            self.model_hint.setText(self.tr("已请求取消，将保留已下载断点…"))
            return
        dl = self._model_downloader
        if dl is not None and not dl.spec(key).urls:
            self._import_model(key)
            return
        self._download_model(key)

    def _download_model(self, key: str) -> None:
        dl = self._model_downloader
        if dl is None:
            self.model_hint.setText(self.tr("模型下载组件未装配"))
            return
        if self._downloading_key is not None:
            self.model_hint.setText(self.tr("已有模型在下载中，请先等待或取消"))
            return
        if self._importing_key is not None:
            self.model_hint.setText(self.tr("已有模型在导入中，请先等待完成"))
            return
        from ych.common.cancellation import CancellationToken

        token = CancellationToken()
        self._model_token = token
        self._downloading_key = key
        self.model_hint.setText(
            self.tr("开始下载，走「网络」分组里配置的代理（若有）…"),
        )
        self._refresh_model_rows()
        worker = LlmWorker(lambda: dl.download(key, token))
        self._track_worker(self._model_workers, worker)
        self._worker_keys[worker] = key
        worker.done.connect(self._on_model_done)        # 绑定方法→回 UI 线程
        worker.failed.connect(self._on_model_failed)
        worker.finished.connect(worker.deleteLater)
        worker.start()
        self._model_timer.start()

    def _on_model_done(self, _result: object) -> None:
        dl = self._model_downloader
        key = self._worker_keys.pop(self.sender(), self._downloading_key or "")
        self._downloading_key = None
        self._model_token = None
        self._model_timer.stop()
        self._refresh_model_rows()
        if dl is not None:
            name = Path(str(dl.path_of(key))).name
            self.model_hint.setText(
                self.tr(f"{name} 下载完成并通过契约校验，即刻可用（无需重启）。"),
            )

    def _on_model_failed(self, msg: str) -> None:
        key = self._worker_keys.pop(self.sender(), self._downloading_key or "?")
        self._downloading_key = None
        self._model_token = None
        self._model_timer.stop()
        self._refresh_model_rows()
        self.model_hint.setText(
            self.tr(f"{key} 下载失败：{msg}（可检查网络/代理后重试）"),
        )

    # ---- 无下载源模型的手动导入（选文件 → 复制 → 契约校验） ----
    def _import_model(self, key: str) -> None:
        dl = self._model_downloader
        if dl is None:
            self.model_hint.setText(self.tr("模型下载组件未装配"))
            return
        if self._downloading_key is not None or self._importing_key is not None:
            self.model_hint.setText(
                self.tr("已有模型在下载/导入中，请先等待完成"),
            )
            return
        src, _f = QFileDialog.getOpenFileName(
            self, self.tr("选择模型文件"),
            "", self.tr("ONNX 模型 (*.onnx);;所有文件 (*.*)"),
        )
        if not src:
            return
        self._importing_key = key
        self.model_hint.setText(self.tr("导入中：复制并校验模型契约…"))
        self._refresh_model_rows()

        def _task() -> object:
            assert dl is not None
            return dl.import_file(key, Path(src))

        worker = LlmWorker(_task)
        self._track_worker(self._model_workers, worker)
        self._worker_keys[worker] = key
        worker.done.connect(self._on_import_done)
        worker.failed.connect(self._on_import_failed)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_import_done(self, _result: object) -> None:
        key = self._worker_keys.pop(self.sender(), self._importing_key or "")
        self._importing_key = None
        self._refresh_model_rows()
        dl = self._model_downloader
        if dl is not None:
            name = Path(str(dl.path_of(key))).name
            self.model_hint.setText(
                self.tr("{name} 导入成功并通过契约校验，即刻可用。").format(name=name),
            )

    def _on_import_failed(self, msg: str) -> None:
        self._worker_keys.pop(self.sender(), None)
        self._importing_key = None
        self._refresh_model_rows()
        self.model_hint.setText(
            self.tr("导入失败：{msg}（文件需与模型用途的输入/输出契约一致）").format(
                msg=msg,
            ),
        )

    def _open_models_dir(self) -> None:
        if self._model_downloader is None:
            return
        model_dir = self._model_downloader.path_of("subtitle").parent
        model_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(model_dir)))

    # ================= 服务列表页 =================
    def _build_service_list_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        self.service_list = QListWidget()
        self.service_list.itemDoubleClicked.connect(
            lambda _item: self._open_selected_editor()
        )
        self.service_list.itemSelectionChanged.connect(
            self._refresh_list_buttons
        )
        lay.addWidget(self.service_list, 1)

        btn_row = QHBoxLayout()
        self.btn_add_service = QPushButton(self.tr("添加 AI 服务"))
        self.btn_add_service.clicked.connect(self._open_ai_add)
        self.btn_edit_service = QPushButton(self.tr("编辑"))
        self.btn_edit_service.setObjectName("secondaryBtn")
        self.btn_edit_service.clicked.connect(self._open_selected_editor)
        self.btn_default_service = QPushButton(self.tr("设为默认"))
        self.btn_default_service.setObjectName("secondaryBtn")
        self.btn_default_service.setToolTip(
            self.tr("将选中的 AI 服务设为默认（素材站无默认概念）"),
        )
        self.btn_default_service.clicked.connect(self._set_default_selected)
        self.btn_del_service = QPushButton(self.tr("删除"))
        self.btn_del_service.setObjectName("secondaryBtn")
        self.btn_del_service.setToolTip(
            self.tr("删除选中的 AI 服务（素材站为内置项不可删除，"
                    "如需停用可在编辑页清空其 Key）"),
        )
        self.btn_del_service.clicked.connect(self._delete_selected)
        for b in (self.btn_add_service, self.btn_edit_service,
                  self.btn_default_service, self.btn_del_service):
            btn_row.addWidget(b)
        btn_row.addStretch(1)
        lay.addLayout(btn_row)
        self._rebuild_service_list()
        return page

    def _ai_services_raw(self) -> dict[str, dict[str, object]]:
        services = self._config.get("ai_services")
        if not isinstance(services, dict):
            return {}
        return {
            str(k): dict(v) if isinstance(v, dict) else {}
            for k, v in services.items()
        }

    def _save_ai_services(self, services: dict[str, dict[str, object]]) -> None:
        self._config.set("ai_services", services)

    def _rebuild_service_list(
        self, select_service: tuple[str, str] | None = None,
    ) -> None:
        """重建列表；select_service 指定要恢复选中的 (kind, id)。

        未指定时自动选中第一个可选行——避免"行没选中、按钮全灰"的困惑。
        """
        self.service_list.clear()
        header_stock = QListWidgetItem(self.tr("素材站（填官方 Key 即启用）"))
        header_stock.setFlags(Qt.ItemFlag.NoItemFlags)
        header_stock.setForeground(QBrush(QColor(_GRAY)))
        header_stock.setToolTip(
            self.tr(
                "素材站由应用内置接口适配（目前 Pexels / Pixabay）。\n"
                "各素材站接口互不相同，新站点需要专门编写适配插件，\n"
                "无法像 AI 服务那样仅凭「网址 + Key」添加。",
            )
        )
        self.service_list.addItem(header_stock)
        for pid, label in _STOCK_SITES:
            has_key = bool(self._config.get(f"{pid}_api_key"))
            state = self.tr("已配置") if has_key else self.tr("未配置")
            item = QListWidgetItem(f"{label}　{'✓ ' if has_key else ''}{state}")
            item.setData(Qt.ItemDataRole.UserRole, ("stock", pid))
            if not has_key:
                item.setForeground(QBrush(QColor(_GRAY)))
            self.service_list.addItem(item)

        header_ai = QListWidgetItem(self.tr("AI 服务（关键词扩展等）"))
        header_ai.setFlags(Qt.ItemFlag.NoItemFlags)
        header_ai.setForeground(QBrush(QColor(_GRAY)))
        self.service_list.addItem(header_ai)
        default_id = str(self._config.get("ai_default_service") or "")
        for service_id, svc in self._ai_services_raw().items():
            name = str(svc.get("name") or self.tr("未命名"))
            model = str(svc.get("model") or self.tr("未选模型"))
            star = self.tr("★默认　") if service_id == default_id else ""
            item = QListWidgetItem(f"{star}{name}（{model}）")
            item.setData(Qt.ItemDataRole.UserRole, ("ai", service_id))
            self.service_list.addItem(item)

        target = self._find_item_row(select_service)
        if target is not None:
            self.service_list.setCurrentItem(target)
        else:
            for i in range(self.service_list.count()):
                it = self.service_list.item(i)
                if it.flags() & Qt.ItemFlag.ItemIsSelectable:
                    self.service_list.setCurrentItem(it)
                    break
        self._refresh_list_buttons()

    def _find_item_row(
        self, ref: tuple[str, str] | None,
    ) -> QListWidgetItem | None:
        if ref is None:
            return None
        for i in range(self.service_list.count()):
            it = self.service_list.item(i)
            data = it.data(Qt.ItemDataRole.UserRole)
            if isinstance(data, (tuple, list)) and tuple(data) == ref:
                return it
        return None

    def _selected_service(self) -> tuple[str, str] | None:
        item = self.service_list.currentItem()
        if item is None:
            return None
        data = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(data, (tuple, list)) and len(data) == 2:
            return str(data[0]), str(data[1])
        return None

    def _refresh_list_buttons(self) -> None:
        sel = self._selected_service()
        is_ai = sel is not None and sel[0] == "ai"
        self.btn_edit_service.setEnabled(sel is not None)
        self.btn_default_service.setEnabled(is_ai)
        self.btn_del_service.setEnabled(is_ai)

    def _open_ai_add(self) -> None:
        self._editing_ai_id = None
        self._ai_edit_title_src = "添加 AI 服务"
        self.ai_edit_title.setText(self.tr(self._ai_edit_title_src))
        self.ai_name_edit.setText("")
        self.ai_base_edit.setText("")
        self.ai_key_edit.clear()
        self._ai_key_ph_src = "未配置"
        self.ai_key_edit.setPlaceholderText(self.tr(self._ai_key_ph_src))
        self.ai_model_combo.clear()
        self._stack.setCurrentIndex(_PAGE_AI_EDIT)

    def _open_selected_editor(self) -> None:
        sel = self._selected_service()
        if sel is None:
            return
        kind, key = sel
        if kind == "stock":
            self._editing_stock_pid = key
            label = dict(_STOCK_SITES)[key]
            self.stock_key_title.setText(f"{label} Key")
            self.stock_key_edit.clear()
            has_key = bool(self._config.get(f"{key}_api_key"))
            self._stock_key_ph_src = ("已配置（留空并保存=清除）" if has_key
                                      else "未配置")
            self.stock_key_edit.setPlaceholderText(
                self.tr(self._stock_key_ph_src)
            )
            self._stack.setCurrentIndex(_PAGE_KEY_EDIT)
        else:
            services = self._ai_services_raw()
            svc = services.get(key)
            if svc is None:
                return
            self._editing_ai_id = key
            self._ai_edit_title_src = "编辑 AI 服务"
            self.ai_edit_title.setText(self.tr(self._ai_edit_title_src))
            self.ai_name_edit.setText(str(svc.get("name") or ""))
            self.ai_base_edit.setText(str(svc.get("base_url") or ""))
            self.ai_key_edit.clear()
            self._ai_key_ph_src = ("已配置（留空=不修改）" if svc.get("has_key")
                                   else "未配置")
            self.ai_key_edit.setPlaceholderText(self.tr(self._ai_key_ph_src))
            self.ai_model_combo.setCurrentText(str(svc.get("model") or ""))
            self._stack.setCurrentIndex(_PAGE_AI_EDIT)

    def _set_default_selected(self) -> None:
        sel = self._selected_service()
        if sel is not None and sel[0] == "ai":
            self._config.set("ai_default_service", sel[1])
            self._rebuild_service_list(select_service=sel)

    def _delete_selected(self) -> None:
        sel = self._selected_service()
        if sel is None or sel[0] != "ai":
            return
        service_id = sel[1]
        answer = QMessageBox.question(
            self, self.tr("删除服务"),
            self.tr("确定删除该 AI 服务？其密钥将一并从凭据管理器清除。"),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        services = self._ai_services_raw()
        services.pop(service_id, None)
        self._save_ai_services(services)
        if str(self._config.get("ai_default_service") or "") == service_id:
            self._config.set("ai_default_service", "")
        if hasattr(self._config, "secret_delete"):
            self._config.secret_delete(f"ai:{service_id}")
        self._rebuild_service_list()

    # ================= AI 服务配置页 =================
    def _build_ai_edit_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)

        header = QHBoxLayout()
        btn_back = QPushButton(self.tr("← 返回"))
        btn_back.setObjectName("secondaryBtn")
        self.btn_back_ai = btn_back
        btn_back.clicked.connect(
            lambda: self._stack.setCurrentIndex(_PAGE_LIST)
        )
        self._ai_edit_title_src = "添加 AI 服务"
        self.ai_edit_title = QLabel(self.tr(self._ai_edit_title_src))
        header.addWidget(btn_back)
        header.addStretch(1)
        header.addWidget(self.ai_edit_title)
        header.addStretch(1)
        lay.addLayout(header)

        preset_row = QHBoxLayout()
        self._preset_provider_label = QLabel(self.tr("预设供应商"))
        preset_row.addWidget(self._preset_provider_label)
        preset_row.addStretch(1)
        lay.addLayout(preset_row)

        preset_flow = FlowLayout()
        self._preset_chips: list[tuple[QPushButton, str]] = []
        for preset_name, preset_url in AI_PRESETS:
            chip = QPushButton(self.tr(preset_name))
            chip.setObjectName("secondaryBtn")
            chip.setToolTip(preset_url)
            chip.clicked.connect(
                lambda checked=False, n=preset_name, u=preset_url:
                    self._apply_preset(n, u)
            )
            preset_flow.addWidget(chip)
            self._preset_chips.append((chip, preset_name))
        preset_host = QWidget()
        preset_host.setLayout(preset_flow)
        lay.addWidget(preset_host)
        lay.addSpacing(8)

        form = QFormLayout()
        self.ai_name_edit = QLineEdit()
        self.ai_name_edit.setPlaceholderText(self.tr("如：我的中转站 / OpenRouter"))
        self.ai_base_edit = QLineEdit()
        self.ai_base_edit.setPlaceholderText(
            self.tr("https://api.example.com（/v1、/v3、/v4 等版本后缀按原样使用）")
        )
        self.ai_key_edit = QLineEdit()
        self._ai_key_ph_src = ""   # 当前占位提示源串（语言切换重翻译用）
        self.ai_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._ai_key_toggle = _add_password_visibility_toggle(
            self.ai_key_edit, self.tr("显示 / 隐藏 API Key"),
        )
        form.addRow(self.tr("名称"), self.ai_name_edit)
        form.addRow(self.tr("接口地址"), self.ai_base_edit)
        form.addRow("API Key", self.ai_key_edit)
        self._form_ai = form

        model_row = QHBoxLayout()
        self.ai_model_combo = QComboBox()
        self.ai_model_combo.setEditable(True)
        self.ai_model_combo.setPlaceholderText(
            self.tr("点「拉取模型」或直接填写模型名")
        )
        self.ai_fetch_btn = QPushButton(self.tr("拉取模型"))
        self.ai_fetch_btn.setObjectName("secondaryBtn")
        self.ai_fetch_btn.clicked.connect(self._fetch_ai_models)
        model_row.addWidget(self.ai_model_combo, 1)
        model_row.addWidget(self.ai_fetch_btn)
        self._ai_model_label = QLabel(self.tr("模型"))
        form.addRow(self._ai_model_label, model_row)
        lay.addLayout(form)

        save_row = QHBoxLayout()
        btn_save = QPushButton(self.tr("保存"))
        self.btn_save_ai = btn_save
        btn_save.clicked.connect(self._save_ai_edit)
        save_row.addStretch(1)
        save_row.addWidget(btn_save)
        lay.addLayout(save_row)
        return page

    def _apply_preset(self, name: str, base_url: str) -> None:
        self.ai_name_edit.setText(name)
        self.ai_base_edit.setText(base_url)

    def _track_worker(self, bucket: list[LlmWorker], worker: LlmWorker) -> None:
        """持引用防 GC 断连；finished 后移除（长会话引用泄漏 + 误用已删对象）。"""
        bucket.append(worker)

        def _cleanup() -> None:
            with contextlib.suppress(ValueError):
                bucket.remove(worker)

        worker.finished.connect(_cleanup)

    def _save_ai_edit(self) -> None:
        base_url = self.ai_base_edit.text().strip()
        if not base_url:
            QMessageBox.warning(self, self.tr("无法保存"), self.tr("请填写接口地址"))
            return
        services = self._ai_services_raw()
        service_id = self._editing_ai_id
        if service_id is None or service_id not in services:
            service_id = uuid.uuid4().hex[:12]
            services[service_id] = {}
        name = self.ai_name_edit.text().strip() or "未命名服务"
        services[service_id]["name"] = name
        services[service_id]["base_url"] = base_url
        services[service_id]["model"] = self.ai_model_combo.currentText().strip()
        key = self.ai_key_edit.text().strip()
        if key:
            try:
                self._config.secret_set(f"ai:{service_id}", key)
            except Exception as exc:
                # keyring 后端缺失/被锁：提示而非未捕获异常卡在编辑页
                logger.exception("API Key 写入系统凭据库失败")
                QMessageBox.warning(
                    self, self.tr("无法保存"),
                    self.tr("API Key 写入系统凭据库失败：{msg}").format(msg=exc),
                )
                return
            services[service_id]["has_key"] = True
        elif not services[service_id].get("has_key"):
            services[service_id]["has_key"] = False
        self._save_ai_services(services)
        self._stack.setCurrentIndex(_PAGE_LIST)
        self._rebuild_service_list(select_service=("ai", service_id))

    def _fetch_ai_models(self) -> None:
        base_url = self.ai_base_edit.text().strip()
        if not base_url:
            QMessageBox.warning(self, self.tr("无法拉取"), self.tr("请先填写接口地址"))
            return
        if self._ai_gateway is None:
            QMessageBox.warning(self, self.tr("无法拉取"), self.tr("AI 服务能力未装配"))
            return
        typed_key = self.ai_key_edit.text().strip() or None
        self.ai_fetch_btn.setEnabled(False)
        self.ai_fetch_btn.setText(self.tr("拉取中…"))

        def _task() -> list[str]:
            assert self._ai_gateway is not None
            return list(self._ai_gateway.list_models(
                self._editing_ai_id or "", base_url, typed_key,
            ))

        worker = LlmWorker(_task)
        self._track_worker(self._ai_workers, worker)
        # 必须连绑定方法：局部闭包会在 LlmWorker 线程里直连执行，
        # 造成后台线程操作 UI（随机崩溃/按钮永久卡在"拉取中"）
        worker.done.connect(self._on_ai_models_done)
        worker.failed.connect(self._on_ai_models_failed)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_ai_models_done(self, models: object) -> None:
        self.ai_fetch_btn.setEnabled(True)
        self.ai_fetch_btn.setText(self.tr("拉取模型"))
        if isinstance(models, list) and models:
            current = self.ai_model_combo.currentText()
            self.ai_model_combo.clear()
            self.ai_model_combo.addItems([str(m) for m in models])
            idx = self.ai_model_combo.findText(current)
            self.ai_model_combo.setCurrentIndex(idx if idx >= 0 else 0)
        else:
            QMessageBox.information(
                self, self.tr("未获取到模型"),
                self.tr("服务未返回模型列表：可检查地址与 Key，"
                        "或直接在模型框手动填写模型名。"),
            )

    def _on_ai_models_failed(self, msg: str) -> None:
        self.ai_fetch_btn.setEnabled(True)
        self.ai_fetch_btn.setText(self.tr("拉取模型"))
        QMessageBox.warning(self, self.tr("拉取失败"), msg)

    # ================= 素材站 Key 配置页 =================
    def _build_stock_key_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)

        header = QHBoxLayout()
        btn_back = QPushButton(self.tr("← 返回"))
        btn_back.setObjectName("secondaryBtn")
        self.btn_back_stock = btn_back
        btn_back.clicked.connect(
            lambda: self._stack.setCurrentIndex(_PAGE_LIST)
        )
        self.stock_key_title = QLabel("Pexels Key")
        header.addWidget(btn_back)
        header.addStretch(1)
        header.addWidget(self.stock_key_title)
        header.addStretch(1)
        lay.addLayout(header)

        form = QFormLayout()
        self.stock_key_edit = QLineEdit()
        self._stock_key_ph_src = ""
        self.stock_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._stock_key_toggle = _add_password_visibility_toggle(
            self.stock_key_edit, self.tr("显示 / 隐藏 API Key"),
        )
        form.addRow("API Key", self.stock_key_edit)
        lay.addLayout(form)
        note = QLabel(self.tr("密钥存储在系统凭据管理器，不上传、不入库。"))
        self._stock_note_label = note
        lay.addWidget(note)

        save_row = QHBoxLayout()
        btn_save = QPushButton(self.tr("保存"))
        self.btn_save_stock = btn_save
        btn_save.clicked.connect(self._save_stock_key)
        save_row.addStretch(1)
        save_row.addWidget(btn_save)
        lay.addLayout(save_row)
        lay.addStretch(1)
        return page

    def _save_stock_key(self) -> None:
        pid = self._editing_stock_pid
        value = self.stock_key_edit.text().strip()
        try:
            if value:
                self._config.secret_set(pid, value)   # 写 keyring 并置位标记
            else:
                if hasattr(self._config, "secret_delete"):
                    self._config.secret_delete(pid)
                self._config.set(f"{pid}_api_key", False)
        except Exception as exc:
            logger.exception("素材站 Key 写入系统凭据库失败")
            QMessageBox.warning(
                self, self.tr("无法保存"),
                self.tr("Key 写入系统凭据库失败：{msg}").format(msg=exc),
            )
            return
        self._stack.setCurrentIndex(_PAGE_LIST)
        self._rebuild_service_list(select_service=("stock", pid))

    # ================= 其余分组 =================
    def _on_workdir_edited(self) -> None:
        """手输工作目录：与「浏览」同路径走 WorkDirManager（校验+即时生效
        +素材列表刷新）。此前直写 config 导致本会话不生效且无任何反馈。"""
        text = self.workdir_edit.text().strip()
        if not text:
            return
        current = str(self._config.get("workdir") or "")
        if text == current:
            return   # 失焦触发的未变更编辑，无需处理
        if self._workdirs is None:
            self._config.set("workdir", text)   # 管理器未装配时退化为直写
            return
        try:
            self._workdirs.set_workdir(Path(text))
        except Exception as exc:
            QMessageBox.warning(
                self, self.tr("目录不可用"),
                self.tr("该目录无法作为工作目录：\n{err}").format(err=exc),
            )
            # 回显为当前生效目录，避免界面与实际状态不一致
            self.workdir_edit.setText(current)
            return
        self.workdir_edit.setText(text)

    def _pick_workdir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, self.tr("选择素材工作目录"),
        )
        if not chosen:
            return
        path = Path(chosen)
        if self._workdirs is not None:
            # 走 WorkDirManager：校验 + 立即生效 + workdir_changed 触发
            # 素材列表刷新与增量扫描（此前只写配置，本会话不生效）
            try:
                self._workdirs.set_workdir(path)
            except Exception as exc:
                QMessageBox.warning(
                    self, self.tr("目录不可用"),
                    self.tr("该目录无法作为工作目录：\n{err}").format(err=exc),
                )
                return
        else:
            self._config.set("workdir", str(path))
        self.workdir_edit.setText(str(path))

    def _commit_proxy_address(self) -> None:
        """提交代理地址：宽容解析 → 归一化回显 → 即时状态反馈。

        无法解析时红色提示并回显原值，绝不静默丢弃（此前端口留 0 会
        让代理被静默停用，用户视角即"填了正确的也不让用"）。
        """
        text = self.proxy_edit.text().strip()
        parsed = split_proxy_address(text)
        if parsed is None:
            self.proxy_status.setText(
                self.tr("无法识别的代理地址：{text}"
                        "（示例：127.0.0.1:7890 或 http://127.0.0.1:7890）").format(
                            text=text,
                        ),
            )
            self.proxy_status.setStyleSheet(f"color: {_RED};")
            return
        host, port = parsed
        if not host:
            # 清空输入 = 清除代理配置
            self._config.set("proxy_host", "")
            self._config.set("proxy_port", 0)
            self.proxy_edit.setText("")
            self.proxy_status.setText(self.tr("已清空代理地址。"))
            self.proxy_status.setStyleSheet(f"color: {_GRAY};")
            return
        if port <= 0:
            self.proxy_status.setText(
                self.tr("代理地址缺少端口：请写成 IP:端口（如 127.0.0.1:7890）。"),
            )
            self.proxy_status.setStyleSheet(f"color: {_RED};")
            return
        self._config.set("proxy_host", host)
        self._config.set("proxy_port", port)
        self.proxy_edit.setText(f"{host}:{port}")
        state = (self.tr("已启用") if self.proxy_check.isChecked()
                 else self.tr("已保存，勾选「启用代理」后生效"))
        self.proxy_status.setText(
            self.tr("✓ {state}：http://{host}:{port}").format(
                state=state, host=host, port=port,
            ),
        )
        self.proxy_status.setStyleSheet(
            f"color: {_GREEN};" if self.proxy_check.isChecked()
            else f"color: {_GRAY};"
        )

    def _commit_int_setting(self, edit: QLineEdit, key: str, fallback: int) -> None:
        """整型设置提交：非法输入回退默认值并回显规范化结果。"""
        try:
            value = int(edit.text())
        except ValueError:
            value = fallback
        self._config.set(key, max(0, value))
        edit.setText(str(max(0, value)))

    def _open_net_check(self) -> None:
        """打开网络检测面板（网站延迟 + IP 信息，全部后台线程，不阻塞界面）。"""
        if self._http is None:
            QMessageBox.warning(
                self, self.tr("网络检测"), self.tr("网络能力未装配，无法检测。"),
            )
            return
        dlg = NetCheckDialog(self._http, parent=self)
        dlg.exec()

    # ---- 代理自动检测 ----
    def _auto_detect_proxy(self) -> None:
        """后台探测系统代理/常见本地端口，验证可用后自动填入并启用。"""
        self.proxy_auto_btn.setEnabled(False)
        self.proxy_status.setText(self.tr("检测中：正在探测系统代理与常见端口…"))
        self.proxy_status.setStyleSheet(f"color: {_GRAY};")
        worker = LlmWorker(detect_local_proxy)
        self._track_worker(self._ai_workers, worker)
        worker.done.connect(self._on_proxy_detected)
        worker.failed.connect(self._on_proxy_detect_failed)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_proxy_detected(self, result: object) -> None:
        self.proxy_auto_btn.setEnabled(True)
        if not (isinstance(result, tuple) and len(result) == 2):
            self.proxy_status.setText(
                self.tr("未检测到可用代理：系统代理未开启，常见端口也无响应。"
                        "若你的 VPN 支持 TUN/系统代理模式，无需启用本项即可直接使用。"),
            )
            self.proxy_status.setStyleSheet(f"color: {_ORANGE};")
            return
        host, port = str(result[0]), int(result[1])
        self._config.set("proxy_host", host)
        self._config.set("proxy_port", port)
        self.proxy_edit.setText(f"{host}:{port}")
        self.proxy_check.setChecked(True)   # toggled → 持久化 proxy_enabled
        self.proxy_status.setText(
            self.tr(f"✓ 已自动配置并启用：{host}:{port}（已验证可访问外网）"),
        )
        self.proxy_status.setStyleSheet(f"color: {_GREEN};")

    def _on_proxy_detect_failed(self, msg: str) -> None:
        self.proxy_auto_btn.setEnabled(True)
        self.proxy_status.setText(self.tr(f"检测失败：{msg}"))
        self.proxy_status.setStyleSheet(f"color: {_RED};")

    def _on_lang_changed(self, locale: str) -> None:
        self._config.set("language", locale)
        if self._i18n is not None:
            self._i18n.switch_locale(locale)

    def _on_theme_changed(self, index: int) -> None:
        """切换主题：持久化并即时生效（无需重启）。"""
        from typing import cast

        from PySide6.QtWidgets import QApplication

        from ych.ui.u6_common.theme import apply_theme

        mode = self.theme_combo.itemData(index)
        if not isinstance(mode, str):
            return
        self._config.set("theme", mode)
        app = cast(QApplication, QApplication.instance())
        apply_theme(app, mode)

    def _on_config_changed(self, key: str, value: object) -> None:
        """config.changed → 控件回填（避免回环：仅当值不同才写控件）。"""
        if key in ("proxy_host", "proxy_port"):
            # 单框展示：由 host+port 组合出归一化地址（自动检测等外部
            # 写入方也会走到这里）
            host = str(self._config.get("proxy_host") or "")
            port_raw = self._config.get("proxy_port")
            port = port_raw if isinstance(port_raw, int) else 0
            text = f"{host}:{port}" if host and port else host
            if self.proxy_edit.text() != text:
                self.proxy_edit.setText(text)
            return
        mapping = {
            "workdir": (self.workdir_edit, "text"),
            "download_concurrency": (self.download_conc, "text"),
            "process_concurrency": (self.process_conc, "text"),
            "max_retry": (self.max_retry, "text"),
        }
        entry = mapping.get(key)
        if entry is not None:
            widget, attr = entry
            if getattr(widget, attr)() != str(value):
                widget.setText(str(value))
        elif key == "proxy_enabled":
            if self.proxy_check.isChecked() != bool(value):
                self.proxy_check.setChecked(bool(value))
        elif key == "readonly_protect_raw":
            if self.readonly_check.isChecked() != bool(value):
                self.readonly_check.setChecked(bool(value))
        elif key == "theme":
            if self.theme_combo.currentData() != str(value):
                idx = self.theme_combo.findData(str(value))
                if idx >= 0:
                    self.theme_combo.setCurrentIndex(idx)
        elif key == "language":
            if self.lang_combo.currentText() != str(value):
                idx = self.lang_combo.findText(str(value))
                if idx >= 0:
                    self.lang_combo.setCurrentIndex(idx)

    def retranslate(self) -> None:
        """语言切换：分组标题/表单标签/按钮/提示重翻译；列表与模型表重建。"""
        self._group_general.setTitle(self.tr("通用"))
        self._group_network.setTitle(self.tr("网络"))
        self._group_services.setTitle(self.tr("服务（素材站与 AI）"))
        self._group_models.setTitle(self.tr("AI 模型（去水印 / 去字幕 / 重复度）"))
        self._group_advanced.setTitle(self.tr("高级"))
        for form, pairs in (
            (self._form_general, ((self.lang_combo, "界面语言"),
                                  (self.theme_combo, "界面主题"),
                                  (self._workdir_row, "工作目录"))),
            (self._form_network, ((self.proxy_check, ""),
                                  (self._proxy_row, "代理地址"))),
            (self._form_advanced, ((self.download_conc, "下载并行数"),
                                   (self.process_conc, "处理并行数"),
                                   (self.max_retry, "失败重试次数"))),
        ):
            for field, label_src in pairs:
                if not label_src:
                    continue
                label = form.labelForField(field)
                if isinstance(label, QLabel):
                    label.setText(self.tr(label_src))
        for i, (label_src, _val) in enumerate(
            (("跟随系统", "system"), ("浅色", "light"), ("深色", "dark")),
        ):
            self.theme_combo.setItemText(i, self.tr(label_src))
        self.btn_pick_workdir.setText(self.tr("选择…"))
        self.proxy_check.setText(self.tr("启用代理"))
        self.proxy_auto_btn.setText(self.tr("自动检测"))
        self.proxy_auto_btn.setToolTip(
            self.tr("自动探测系统代理与常见本地端口（Clash/v2rayN 等），"
                    "验证可通外网后自动填入并启用"),
        )
        self.proxy_hint.setText(
            self.tr("可整段粘贴代理地址，支持 IP:端口 或 http://IP:端口"
                    "（Clash 默认 127.0.0.1:7890，v2rayN 默认 10809）。"
                    "VPN 的订阅链接不是代理地址。"
                    "若 VPN 使用 TUN/系统代理模式，无需启用本项。"),
        )
        self.net_btn.setText(self.tr("网络检测"))
        self.readonly_check.setText(self.tr("原始素材只读保护"))
        self.btn_open_models.setText(self.tr("打开模型目录"))
        self.model_table.setHorizontalHeaderLabels([
            self.tr("模型文件"), self.tr("用途"), self.tr("状态"),
            self.tr("操作"),
        ])
        self._rebuild_service_list()
        self._refresh_model_rows()
        self.btn_add_service.setText(self.tr("添加 AI 服务"))
        self.btn_edit_service.setText(self.tr("编辑"))
        self.btn_default_service.setText(self.tr("设为默认"))
        self.btn_del_service.setText(self.tr("删除"))
        for field, label_src in ((self.ai_name_edit, "名称"),
                                 (self.ai_base_edit, "接口地址")):
            label = self._form_ai.labelForField(field)
            if isinstance(label, QLabel):
                label.setText(self.tr(label_src))
        self._ai_model_label.setText(self.tr("模型"))
        self.btn_back_ai.setText(self.tr("← 返回"))
        self.btn_back_stock.setText(self.tr("← 返回"))
        self.btn_save_ai.setText(self.tr("保存"))
        self.btn_save_stock.setText(self.tr("保存"))
        self.ai_fetch_btn.setText(self.tr("拉取模型"))
        self._preset_provider_label.setText(self.tr("预设供应商"))
        for chip, preset_name in self._preset_chips:
            chip.setText(self.tr(preset_name))
        self.ai_edit_title.setText(self.tr(self._ai_edit_title_src))
        self._stock_note_label.setText(
            self.tr("密钥存储在系统凭据管理器，不上传、不入库。"),
        )
        self.ai_name_edit.setPlaceholderText(
            self.tr("如：我的中转站 / OpenRouter"))
        self.ai_base_edit.setPlaceholderText(
            self.tr("https://api.example.com（/v1、/v3、/v4 等版本后缀按原样使用）")
        )
        self.ai_model_combo.setPlaceholderText(
            self.tr("点「拉取模型」或直接填写模型名"))
        if self._ai_key_ph_src:
            self.ai_key_edit.setPlaceholderText(self.tr(self._ai_key_ph_src))
        if self._stock_key_ph_src:
            self.stock_key_edit.setPlaceholderText(
                self.tr(self._stock_key_ph_src))
        self._ai_key_toggle.setToolTip(self.tr("显示 / 隐藏 API Key"))
        self._stock_key_toggle.setToolTip(self.tr("显示 / 隐藏 API Key"))
