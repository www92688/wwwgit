# 网络检测面板单元测试：FakeHttp 下结果回填、结论、非阻塞（全后台线程）
from __future__ import annotations

from typing import Any, ClassVar

import pytest


class FakeHttp:
    """HttpClient 替身：瞬时返回，不触网。"""

    def probe_latency(self, url: str, timeout_s: float = 4.0) -> int | None:
        if "google" in url or "youtube" in url:
            return None          # 模拟被墙
        return 208

    def get_json(
        self, url: str, *args: Any, **kwargs: Any,
    ) -> dict[str, object]:
        return {
            "ip": "1.2.3.4", "country_name": "China", "country_code": "CN",
            "region": "Fujian", "city": "Putian", "org": "ChinaUnicom",
            "asn": "AS4837", "timezone": "Asia/Shanghai",
        }


@pytest.fixture(autouse=True)
def _fast_wait(qtbot: Any) -> None:
    pass


def test_dialog_fills_site_results_and_conclusion(qtbot: Any) -> None:
    from ych.ui.u5_settings.net_check_dialog import NetCheckDialog

    dlg = NetCheckDialog(FakeHttp())
    qtbot.addWidget(dlg)
    dlg.show()
    qtbot.waitUntil(
        lambda: dlg._site_labels["Apple"].text() == "208 ms", timeout=5000,
    )
    assert dlg._site_labels["Google"].text() == "不可达"
    assert "可访问国外素材站" in dlg.conclusion_label.text()


def test_dialog_fills_ip_info(qtbot: Any) -> None:
    from ych.ui.u5_settings.net_check_dialog import NetCheckDialog

    dlg = NetCheckDialog(FakeHttp())
    qtbot.addWidget(dlg)
    dlg.show()
    qtbot.waitUntil(
        lambda: dlg.ip_labels["ip"].text() == "1.2.3.4", timeout=5000,
    )
    assert "China" in dlg.ip_labels["country"].text()
    assert dlg.ip_labels["asn"].text() == "AS4837"
    assert dlg.ip_labels["location"].text() == "Putian，Fujian"


def test_settings_button_opens_net_dialog(
    qtbot: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ych.ui.u5_settings.settings_page as sp
    from ych.services.s5_base.config_service import ConfigService

    opened: list[bool] = []

    class FakeDialog:
        def __init__(self, http: Any, parent: Any = None) -> None:
            opened.append(True)

        def exec(self) -> int:
            return 0

    monkeypatch.setattr(sp, "NetCheckDialog", FakeDialog)
    from ych.ui.u5_settings.settings_page import SettingsPage

    page = SettingsPage(ConfigService(), http=FakeHttp())
    qtbot.addWidget(page)
    for btn in page.findChildren(type(page.net_btn)):
        if btn.text() == "网络检测":
            btn.click()
            break
    assert opened == [True]


# ---------- 代理自动检测（设置页按钮接线） ----------
def test_auto_detect_proxy_fills_and_enables(
    qtbot: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ych.ui.u5_settings.settings_page as sp
    from ych.services.s5_base.config_service import ConfigService
    from ych.ui.u5_settings.settings_page import SettingsPage

    monkeypatch.setattr(
        sp, "detect_local_proxy", lambda *a, **k: ("127.0.0.1", 7890),
    )
    config = ConfigService()
    page = SettingsPage(config, http=FakeHttp())
    qtbot.addWidget(page)
    page.show()
    page.proxy_auto_btn.click()
    qtbot.waitUntil(
        lambda: page.proxy_edit.text() == "127.0.0.1:7890", timeout=5000,
    )
    assert page.proxy_check.isChecked() is True
    assert config.get("proxy_enabled") is True
    assert config.get("proxy_host") == "127.0.0.1"
    assert config.get("proxy_port") == 7890
    assert "已自动配置" in page.proxy_status.text()


def test_auto_detect_proxy_not_found_hint(
    qtbot: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ych.ui.u5_settings.settings_page as sp
    from ych.services.s5_base.config_service import ConfigService
    from ych.ui.u5_settings.settings_page import SettingsPage

    monkeypatch.setattr(sp, "detect_local_proxy", lambda *a, **k: None)
    page = SettingsPage(ConfigService(), http=FakeHttp())
    qtbot.addWidget(page)
    page.show()
    page.proxy_auto_btn.click()
    qtbot.waitUntil(
        lambda: "未检测到可用代理" in page.proxy_status.text(), timeout=5000,
    )
    assert page.proxy_check.isChecked() is False


# ---------- 代理地址单框：整段粘贴 → 失焦归一化提交 ----------
def test_proxy_address_commit_parses_full_url(
    qtbot: Any,
) -> None:
    """粘贴 "http://IP:端口" 失焦后拆解入库并归一化显示，不再静默停用。"""
    from ych.services.s5_base.config_service import ConfigService
    from ych.ui.u5_settings.settings_page import SettingsPage

    config = ConfigService()
    page = SettingsPage(config, http=FakeHttp())
    qtbot.addWidget(page)
    page.show()
    page.proxy_edit.setText("http://120.25.100.196:3000")
    page.proxy_edit.editingFinished.emit()
    assert config.get("proxy_host") == "120.25.100.196"
    assert config.get("proxy_port") == 3000
    assert page.proxy_edit.text() == "120.25.100.196:3000"
    assert "已保存" in page.proxy_status.text()


def test_proxy_address_commit_missing_port_hint(qtbot: Any) -> None:
    from ych.services.s5_base.config_service import ConfigService
    from ych.ui.u5_settings.settings_page import SettingsPage

    config = ConfigService()
    page = SettingsPage(config, http=FakeHttp())
    qtbot.addWidget(page)
    page.show()
    page.proxy_edit.setText("127.0.0.1")
    page.proxy_edit.editingFinished.emit()
    assert "缺少端口" in page.proxy_status.text()
    assert config.get("proxy_port") == 0


def test_proxy_address_commit_invalid_keeps_hint(qtbot: Any) -> None:
    from ych.services.s5_base.config_service import ConfigService
    from ych.ui.u5_settings.settings_page import SettingsPage

    page = SettingsPage(ConfigService(), http=FakeHttp())
    qtbot.addWidget(page)
    page.show()
    page.proxy_edit.setText("http://")
    page.proxy_edit.editingFinished.emit()
    assert "无法识别" in page.proxy_status.text()


# ---------- API Key 显示/隐藏切换 ----------
def test_password_visibility_toggle(qtbot: Any) -> None:
    from PySide6.QtWidgets import QLineEdit

    from ych.services.s5_base.config_service import ConfigService
    from ych.ui.u5_settings.settings_page import SettingsPage

    page = SettingsPage(ConfigService(), http=FakeHttp())
    qtbot.addWidget(page)
    for edit, toggle in (
        (page.ai_key_edit, page._ai_key_toggle),
        (page.stock_key_edit, page._stock_key_toggle),
    ):
        assert edit.echoMode() == QLineEdit.EchoMode.Password
        toggle.setChecked(True)
        assert edit.echoMode() == QLineEdit.EchoMode.Normal
        toggle.setChecked(False)
        assert edit.echoMode() == QLineEdit.EchoMode.Password


# ---------- AI 模型表：无下载源行为「导入…」而非禁用 ----------
class _FakeSpec:
    def __init__(self, key: str, file: str, desc: str, urls: list[str]) -> None:
        self.key = key
        self.file = file
        self.desc = desc
        self.urls = urls


class _FakeDl:
    progress: ClassVar[dict[str, float]] = {}

    def all_specs(self) -> list[_FakeSpec]:
        return [
            _FakeSpec("subtitle", "sub.onnx", "字幕", ["u1"]),
            _FakeSpec("watermark", "wm.onnx", "水印", []),
        ]

    def exists(self, key: str) -> bool:
        return False

    def size_of(self, key: str) -> int:
        return 0

    def path_of(self, key: str) -> Any:
        from pathlib import Path

        return Path("x")


def test_model_rows_no_url_spec_offers_import(qtbot: Any) -> None:
    """无公开下载源的模型行不得是禁用按钮：应为可点的「导入…」。"""
    from ych.services.s5_base.config_service import ConfigService
    from ych.ui.u5_settings.settings_page import SettingsPage

    page = SettingsPage(
        ConfigService(), http=FakeHttp(), model_downloader=_FakeDl(),
    )
    qtbot.addWidget(page)
    table = page.model_table
    assert table.rowCount() == 2
    dl_btn = page._model_dl_cells["subtitle"]
    imp_btn = page._model_dl_cells["watermark"]
    assert dl_btn.text() == "下载" and dl_btn.isEnabled()
    assert imp_btn.text() == "导入…" and imp_btn.isEnabled()
    assert "导入" in table.item(1, 2).text()
