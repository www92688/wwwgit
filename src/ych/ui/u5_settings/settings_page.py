# 设置页（U5）：通用/网络/密钥/高级 分组；控件 ↔ ConfigService 双向绑定
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from PySide6.QtCore import Signal

    class _ConfigLike(QObject):
        changed: Signal

        def get(self, key: str) -> object: ...
        def set(self, key: str, value: object) -> None: ...
        def secret_get(self, key: str) -> str: ...
        def secret_set(self, key: str, value: str) -> None: ...

    class _I18nLike(QObject):
        locale_changed: Signal

        def switch_locale(self, locale: str) -> None: ...
        def available_locales(self) -> list[str]: ...

    class _NetCheckerLike(QObject):
        def check(self, force: bool = False) -> object: ...

else:
    _ConfigLike = QObject
    _I18nLike = QObject
    _NetCheckerLike = QObject


class SettingsPage(QWidget):
    """表单分组：通用(语言/工作目录)/网络(代理+外网检测)/密钥(keyring)/高级。"""

    def __init__(
        self,
        config: _ConfigLike,
        i18n: _I18nLike | None = None,
        net_checker: _NetCheckerLike | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._i18n = i18n
        self._net_checker = net_checker
        root = QVBoxLayout(self)

        # ---- 通用 ----
        general = QGroupBox(self.tr("通用"))
        form_g = QFormLayout(general)
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
        row_dir = QHBoxLayout()
        self.workdir_edit = QLineEdit(str(config.get("workdir") or ""))
        btn_pick = QPushButton(self.tr("选择…"))
        btn_pick.setObjectName("secondaryBtn")
        btn_pick.clicked.connect(self._pick_workdir)
        row_dir.addWidget(self.workdir_edit)
        row_dir.addWidget(btn_pick)
        form_g.addRow(self.tr("界面语言"), self.lang_combo)
        form_g.addRow(self.tr("工作目录"), row_dir)
        root.addWidget(general)

        # ---- 网络 ----
        network = QGroupBox(self.tr("网络"))
        form_n = QFormLayout(network)
        self.proxy_check = QCheckBox("启用代理")
        self.proxy_host = QLineEdit(str(config.get("proxy_host") or ""))
        self.proxy_port = QLineEdit(str(config.get("proxy_port") or 0))
        row_proxy = QHBoxLayout()
        row_proxy.addWidget(self.proxy_host)
        row_proxy.addWidget(self.proxy_port)
        self.net_btn = QPushButton(self.tr("检测外网"))
        self.net_btn.setObjectName("secondaryBtn")
        self.net_btn.clicked.connect(self._check_foreign_net)
        form_n.addRow(self.proxy_check)
        form_n.addRow(self.tr("代理地址"), row_proxy)
        form_n.addRow("", self.net_btn)
        root.addWidget(network)

        # ---- 密钥 ----
        keys = QGroupBox(self.tr("素材站密钥（系统凭据管理器存储）"))
        form_k = QFormLayout(keys)
        self.pexels_key = QLineEdit()
        self.pexels_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.pexels_key.setPlaceholderText(
            "已配置" if bool(config.get("pexels_api_key")) else "未配置"
        )
        self.pixabay_key = QLineEdit()
        self.pixabay_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.pixabay_key.setPlaceholderText(
            "已配置" if bool(config.get("pixabay_api_key")) else "未配置"
        )
        form_k.addRow("Pexels Key", self.pexels_key)
        form_k.addRow("Pixabay Key", self.pixabay_key)
        root.addWidget(keys)

        # ---- 高级 ----
        advanced = QGroupBox(self.tr("高级"))
        form_a = QFormLayout(advanced)
        self.download_conc = QLineEdit(str(config.get("download_concurrency")))
        self.process_conc = QLineEdit(str(config.get("process_concurrency")))
        self.max_retry = QLineEdit(str(config.get("max_retry")))
        self.readonly_check = QCheckBox("原始素材只读保护")
        self.readonly_check.setChecked(bool(config.get("readonly_protect_raw")))
        form_a.addRow("下载并行数", self.download_conc)
        form_a.addRow("处理并行数", self.process_conc)
        form_a.addRow("失败重试次数", self.max_retry)
        form_a.addRow("", self.readonly_check)
        root.addWidget(advanced)
        root.addStretch(1)

        # ---- 双向绑定 ----
        self.lang_combo.currentTextChanged.connect(self._on_lang_changed)
        self.workdir_edit.editingFinished.connect(
            lambda: config.set("workdir", self.workdir_edit.text().strip())
        )
        self.proxy_check.toggled.connect(lambda v: config.set("proxy_enabled", v))
        self.proxy_host.editingFinished.connect(
            lambda: config.set("proxy_host", self.proxy_host.text().strip())
        )
        self.proxy_port.editingFinished.connect(self._commit_port)
        self.pexels_key.editingFinished.connect(
            lambda: self._save_secret("pexels", self.pexels_key)
        )
        self.pixabay_key.editingFinished.connect(
            lambda: self._save_secret("pixabay", self.pixabay_key)
        )
        self.download_conc.editingFinished.connect(
            lambda: config.set("download_concurrency",
                               int(self.download_conc.text() or 3))
        )
        self.process_conc.editingFinished.connect(
            lambda: config.set("process_concurrency",
                               int(self.process_conc.text() or 2))
        )
        self.max_retry.editingFinished.connect(
            lambda: config.set("max_retry", int(self.max_retry.text() or 2))
        )
        self.readonly_check.toggled.connect(
            lambda v: config.set("readonly_protect_raw", v)
        )
        config.changed.connect(self._on_config_changed)
        self.proxy_check.setChecked(bool(config.get("proxy_enabled")))
        self.readonly_check.setChecked(bool(config.get("readonly_protect_raw")))

    # ---- 槽 ----
    def _pick_workdir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "选择素材工作目录")
        if chosen:
            path = Path(chosen)
            self.workdir_edit.setText(str(path))
            self._config.set("workdir", str(path))

    def _commit_port(self) -> None:
        try:
            port = int(self.proxy_port.text())
        except ValueError:
            port = 0
        self._config.set("proxy_port", port)

    def _save_secret(self, name: str, edit: QLineEdit) -> None:
        value = edit.text().strip()
        if value:
            self._config.secret_set(name, value)

    def _check_foreign_net(self) -> None:
        """外网能力检测：同步执行但限制在按钮点击线程内短时完成。"""
        from PySide6.QtWidgets import QMessageBox

        if self._net_checker is None:
            return
        status = self._net_checker.check(force=True)
        title = {
            "ok": "外网可用",
            "blocked": "无法访问外网素材站",
            "offline": "当前无网络连接",
        }.get(getattr(status, "value", ""), "检测结果")
        msg = {
            "ok": "已具备外网访问能力，可开启国外平台。",
            "blocked": "当前网络环境无法访问外网素材站，请检查代理设置。",
            "offline": "请先检查本机网络连接。",
        }.get(getattr(status, "value", ""), str(status))
        QMessageBox.information(self, title, msg)

    def _on_lang_changed(self, locale: str) -> None:
        self._config.set("language", locale)
        if self._i18n is not None:
            self._i18n.switch_locale(locale)

    def _on_config_changed(self, key: str, value: object) -> None:
        """config.changed → 控件回填（避免回环：仅当值不同才写控件）。"""
        mapping = {
            "workdir": (self.workdir_edit, "text"),
            "proxy_host": (self.proxy_host, "text"),
            "proxy_port": (self.proxy_port, "text"),
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
