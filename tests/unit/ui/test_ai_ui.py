# 服务管理 UI 单元测试：列表页（素材站+AI 服务）与配置页跳转流程
from __future__ import annotations

from typing import Any

import pytest
from PySide6.QtWidgets import (
    QListWidget,
    QMessageBox,
)

from ych.services.s5_base.config_service import ConfigService


@pytest.fixture(autouse=True)
def _mute_message_boxes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a: None))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a: None))
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a: QMessageBox.StandardButton.Yes),
    )


class FakeAiGateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str | None]] = []

    def list_models(
        self, service_id: str, base_url: str, api_key: str | None = None,
    ) -> list[str]:
        self.calls.append((service_id, base_url, api_key))
        return ["model-b", "model-a"]

    def is_configured(self) -> bool:
        return False

    def expand_keywords(self, keyword: str) -> list[str]:
        return []


def _find_button(page: Any, text: str) -> Any:
    from PySide6.QtWidgets import QPushButton

    # 只在当前堆叠页内找：隐藏页存在同名按钮（如两个配置页的「保存」）
    current = page._stack.currentWidget()
    for btn in current.findChildren(QPushButton):
        if btn.text() == text:
            return btn
    raise AssertionError(f"button not found on current page: {text}")


def _select_row(page: Any, prefix: str) -> None:
    lst = page.findChildren(QListWidget)[0]
    for i in range(lst.count()):
        if prefix in lst.item(i).text():
            lst.setCurrentRow(i)
            return
    raise AssertionError(f"row not found: {prefix}")


def _save_via_button(page: Any) -> None:
    # 配置页唯一文本为「保存」的按钮
    _find_button(page, "保存").click()


# ---------- 列表页：素材站与 AI 服务统一展示 ----------
def test_list_shows_stock_sites_and_empty_ai(qtbot) -> None:
    from ych.ui.u5_settings.settings_page import SettingsPage

    page = SettingsPage(ConfigService(), ai_gateway=FakeAiGateway())
    qtbot.addWidget(page)
    texts = [
        page.service_list.item(i).text()
        for i in range(page.service_list.count())
    ]
    assert any(t.startswith("Pexels") and "未配置" in t for t in texts)
    assert any(t.startswith("Pixabay") and "未配置" in t for t in texts)
    # 列表页为当前页
    assert page._stack.currentIndex() == 0


# ---------- 素材站 Key 配置页 ----------
def test_stock_key_edit_flow(qtbot, memory_keyring) -> None:
    from ych.ui.u5_settings.settings_page import SettingsPage

    config = ConfigService()
    page = SettingsPage(config, ai_gateway=FakeAiGateway())
    qtbot.addWidget(page)

    _select_row(page, "Pexels")
    _find_button(page, "编辑").click()
    assert page._stack.currentIndex() == 2
    assert page.stock_key_title.text() == "Pexels Key"

    page.stock_key_edit.setText("plx-key-1")
    _save_via_button(page)
    assert page._stack.currentIndex() == 0
    assert memory_keyring.get_password("YuChongGou", "pexels") == "plx-key-1"
    assert config.get("pexels_api_key") is True

    texts = [
        page.service_list.item(i).text()
        for i in range(page.service_list.count())
    ]
    assert any(t.startswith("Pexels") and "已配置" in t for t in texts)

    # 清除：留空保存
    _select_row(page, "Pexels")
    _find_button(page, "编辑").click()
    page.stock_key_edit.setText("")
    _save_via_button(page)
    assert memory_keyring.get_password("YuChongGou", "pexels") is None
    assert config.get("pexels_api_key") is False


# ---------- AI 服务：添加（预设→表单→保存） ----------
def test_ai_add_via_preset_and_save(qtbot) -> None:
    from ych.ui.u5_settings.settings_page import SettingsPage

    config = ConfigService()
    page = SettingsPage(config, ai_gateway=FakeAiGateway())
    qtbot.addWidget(page)

    _find_button(page, "添加 AI 服务").click()
    assert page._stack.currentIndex() == 1

    _find_button(page, "DeepSeek").click()   # 预设 → 预填表单
    assert page.ai_name_edit.text() == "DeepSeek"
    assert page.ai_base_edit.text() == "https://api.deepseek.com/v1"

    page.ai_key_edit.setText("sk-1")
    page.ai_model_combo.setCurrentText("deepseek-chat")
    _save_via_button(page)

    assert page._stack.currentIndex() == 0
    services = config.get("ai_services")
    assert isinstance(services, dict) and len(services) == 1
    svc = next(iter(services.values()))
    assert svc["name"] == "DeepSeek"
    assert svc["base_url"] == "https://api.deepseek.com/v1"
    assert svc["model"] == "deepseek-chat"
    assert svc["has_key"] is True
    # 列表出现该服务
    texts = [
        page.service_list.item(i).text()
        for i in range(page.service_list.count())
    ]
    assert any("DeepSeek" in t for t in texts)


def test_ai_edit_default_and_delete(qtbot, memory_keyring) -> None:
    from ych.ui.u5_settings.settings_page import SettingsPage

    config = ConfigService()
    page = SettingsPage(config, ai_gateway=FakeAiGateway())
    qtbot.addWidget(page)

    # 添加两个服务
    for preset in ("DeepSeek", "OpenRouter"):
        _find_button(page, "添加 AI 服务").click()
        _find_button(page, preset).click()
        page.ai_key_edit.setText(f"sk-{preset}")
        _save_via_button(page)

    _select_row(page, "OpenRouter")
    _find_button(page, "设为默认").click()
    services = config.get("ai_services")
    default_id = config.get("ai_default_service")
    assert default_id in services   # type: ignore[operator]

    # 编辑：改名不改 Key（Key 留空 = 不修改）
    _select_row(page, "OpenRouter")
    _find_button(page, "编辑").click()
    page.ai_name_edit.setText("我的 OR")
    page.ai_key_edit.setText("")
    _save_via_button(page)
    sid = str(default_id)
    assert config.secret_get(f"ai:{sid}") == "sk-OpenRouter"

    # 删除默认服务（OpenRouter）→ 默认清空 + keyring 清除；DeepSeek 保留
    _select_row(page, "我的 OR")
    _find_button(page, "删除").click()
    remaining = config.get("ai_services")
    assert isinstance(remaining, dict) and len(remaining) == 1
    assert next(iter(remaining.values()))["name"] == "DeepSeek"
    assert config.get("ai_default_service") == ""
    assert memory_keyring.get_password("YuChongGou", f"ai:{sid}") is None


def test_ai_save_requires_base_url(qtbot) -> None:
    from ych.ui.u5_settings.settings_page import SettingsPage

    config = ConfigService()
    page = SettingsPage(config, ai_gateway=FakeAiGateway())
    qtbot.addWidget(page)
    _find_button(page, "添加 AI 服务").click()
    page.ai_name_edit.setText("缺地址")
    _save_via_button(page)
    # 地址为空被拦截：仍在配置页，未创建服务
    assert page._stack.currentIndex() == 1
    assert config.get("ai_services") == {}


def test_ai_fetch_models_populates_combo(qtbot) -> None:
    from ych.ui.u5_settings.settings_page import SettingsPage

    config = ConfigService()
    gateway = FakeAiGateway()
    page = SettingsPage(config, ai_gateway=gateway)
    qtbot.addWidget(page)

    _find_button(page, "添加 AI 服务").click()
    page.ai_base_edit.setText("https://relay.example.com")
    page.ai_key_edit.setText("sk-typed")
    _find_button(page, "拉取模型").click()
    qtbot.waitUntil(lambda: page.ai_model_combo.count() == 2, timeout=5000)
    assert gateway.calls == [("", "https://relay.example.com", "sk-typed")]
    assert [page.ai_model_combo.itemText(i)
            for i in range(page.ai_model_combo.count())] == ["model-b", "model-a"]


# ---------- 采集页：AI 扩展合并 ----------
def test_capture_merge_expanded_keywords_dedupes(qtbot) -> None:
    from ych.ui.u1_capture.capture_page import CapturePage

    page = CapturePage()
    qtbot.addWidget(page)
    page.keyword_edit.setText("地毯清洗， 地毯清洗教程")
    page._apply_expanded_keywords(
        ["地毯清洗", "沙发清洁", "家政保洁", "沙发清洁"],
    )
    assert page.keyword_edit.text() == "地毯清洗，地毯清洗教程，沙发清洁，家政保洁"


def test_capture_ai_expand_unconfigured_shows_hint(qtbot) -> None:
    from ych.ui.u1_capture.capture_page import CapturePage

    calls: list[str] = []

    def _fake_info(*args: Any) -> None:
        calls.append("info")

    QMessageBox.information = staticmethod(_fake_info)   # type: ignore[method-assign]
    page = CapturePage()   # 无网关
    qtbot.addWidget(page)
    page.keyword_edit.setText("地毯清洗")
    page._on_ai_expand()
    assert calls == ["info"]
    assert page._ai_worker is None


# ---------- 选中保持：保存后按钮不再全部置灰 ----------
def test_selection_restored_after_save(qtbot) -> None:
    from ych.ui.u5_settings.settings_page import SettingsPage

    config = ConfigService()
    page = SettingsPage(config, ai_gateway=FakeAiGateway())
    qtbot.addWidget(page)

    _find_button(page, "添加 AI 服务").click()
    _find_button(page, "DeepSeek").click()
    page.ai_key_edit.setText("sk-1")
    _save_via_button(page)

    # 保存回到列表页后，刚保存的服务自动选中 → 删除/设为默认可用
    assert page._stack.currentIndex() == 0
    assert page._selected_service() is not None
    assert page._selected_service()[0] == "ai"   # type: ignore[index]
    assert page.btn_del_service.isEnabled() is True
    assert page.btn_default_service.isEnabled() is True


def test_first_row_auto_selected_on_construction(qtbot) -> None:
    from ych.ui.u5_settings.settings_page import SettingsPage

    page = SettingsPage(ConfigService(), ai_gateway=FakeAiGateway())
    qtbot.addWidget(page)
    # 初始自动选中第一个可选行（Pexels）→ 编辑按钮可用
    sel = page._selected_service()
    assert sel == ("stock", "pexels")
    assert page.btn_edit_service.isEnabled() is True
    # 素材站行：删除/设为默认保持禁用（内置项）
    assert page.btn_del_service.isEnabled() is False


# ---------- 平台勾选：素材站独立 + 总开关确认流 ----------
def test_stock_checks_independent_from_master(qtbot) -> None:
    from ych.ui.u1_capture.capture_page import CapturePage

    page = CapturePage()
    qtbot.addWidget(page)
    # 素材站默认勾选且始终可用；TikTok/YouTube 初始禁用
    assert page.stock_checks["pexels"].isChecked() is True
    assert page.stock_checks["pexels"].isEnabled() is True
    assert page.global_checks["tiktok"].isEnabled() is False
    # 勾选素材站不受总开关影响
    page.stock_checks["pexels"].setChecked(False)
    assert page.stock_checks["pexels"].isChecked() is False


def test_master_bounces_and_emits_request(qtbot) -> None:
    from ych.ui.u1_capture.capture_page import CapturePage

    config = ConfigService()
    page = CapturePage(config=config)
    qtbot.addWidget(page)
    fired: list[bool] = []
    page.foreign_switch_requested.connect(lambda: fired.append(True))

    page.foreign_master.setChecked(True)
    # 未确认外网能力：回弹 + 发出检测请求 + 子项保持禁用
    assert page.foreign_master.isChecked() is False
    assert fired == [True]
    assert page.global_checks["tiktok"].isEnabled() is False


def test_confirm_foreign_enable_persists(qtbot) -> None:
    from ych.ui.u1_capture.capture_page import CapturePage

    config = ConfigService()
    page = CapturePage(config=config)
    qtbot.addWidget(page)

    page.confirm_foreign_enable(False)
    assert page.foreign_master.isChecked() is False
    assert config.get("foreign_platforms_enabled") is False

    page.confirm_foreign_enable(True)
    assert config.get("foreign_platforms_enabled") is True
    assert page.foreign_master.isChecked() is True
    assert page.global_checks["tiktok"].isEnabled() is True


def test_search_passes_selected_platform_ids(qtbot) -> None:
    from ych.ui.u1_capture.capture_page import CapturePage

    class RecCoordinator:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def search_multi(self, keywords, filters, per_platform_limit=30,
                         token=None, platform_ids=None) -> None:
            self.calls.append({"keywords": keywords,
                               "platform_ids": platform_ids})

    coord = RecCoordinator()
    page = CapturePage(coordinator=coord)
    qtbot.addWidget(page)
    page.keyword_edit.setText("地毯清洗")
    page._on_search()
    assert coord.calls[0]["platform_ids"] is not None
    ids = coord.calls[0]["platform_ids"]
    assert "pexels" in ids and "pixabay" in ids          # type: ignore[operator]
    assert "douyin" in ids                                # type: ignore[operator]
    page._set_searching(False)   # 模拟上一轮搜索结束（搜索中重入会被守卫拒绝）
    # 取消勾选 pexels 后不再传入
    page.stock_checks["pexels"].setChecked(False)
    page._on_search()
    assert "pexels" not in coord.calls[1]["platform_ids"]  # type: ignore[operator]
