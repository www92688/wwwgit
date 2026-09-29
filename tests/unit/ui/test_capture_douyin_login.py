# 抖音登录按钮交互回归（背景：曾出现"点击后无任何反应"的静默失败）
# 契约：点击必须产生可见状态变化；结果必须恢复按钮并给出反馈；不允许静默
from __future__ import annotations

import pytest

from ych.services.s5_base.config_service import ConfigService
from ych.ui.u1_capture.capture_page import CapturePage


@pytest.fixture
def page(qtbot):  # type: ignore[no-untyped-def]
    pg = CapturePage(config=ConfigService())
    qtbot.addWidget(pg)
    return pg


def test_login_click_disables_button_and_reports(
    page,  # type: ignore[no-untyped-def]
    qtbot,  # type: ignore[no-untyped-def]
) -> None:
    callbacks: list[object] = []
    page._douyin_login = lambda cb: callbacks.append(cb)
    page.show()
    page.btn_douyin_login.click()
    assert page._douyin_logging_in is True
    assert page.btn_douyin_login.isEnabled() is False
    assert len(callbacks) == 1
    # 模拟插件后台线程回调（直连槽调用；Signal.emit 为线程安全路径）
    callbacks[0](False, "登录窗口异常退出（退出码 1），请重试")  # type: ignore[operator]
    assert page._douyin_logging_in is False
    assert page.btn_douyin_login.isEnabled() is True
    assert page.btn_douyin_login.text() == page.tr("登录抖音")


def test_login_click_without_wiring_still_gives_feedback(page) -> None:  # type: ignore[no-untyped-def]
    page._douyin_login = None
    page.btn_douyin_login.click()   # 不应崩溃、不应进入登录中状态
    assert page._douyin_logging_in is False
    assert page.btn_douyin_login.isEnabled() is True


def test_login_double_click_is_guarded(
    page,  # type: ignore[no-untyped-def]
) -> None:
    callbacks: list[object] = []
    page._douyin_login = lambda cb: callbacks.append(cb)
    page.btn_douyin_login.click()
    page.btn_douyin_login.click()   # 登录中：按钮禁用，第二次点击不生效
    assert len(callbacks) == 1


def test_login_success_updates_name_label(
    page,  # type: ignore[no-untyped-def]
) -> None:
    callbacks: list[object] = []
    page._douyin_login = lambda cb: callbacks.append(cb)
    # 插件真实时序：先在后台线程写登录标记，再回调完成
    page._config.set("douyin_cookies_set", True)
    page.btn_douyin_login.click()
    callbacks[0](True, "")  # type: ignore[operator]
    assert "已登录" in page.douyin_name_label.text()
    # 昵称后到（config 信号驱动）→ 按钮旁显示昵称
    page._config.set("douyin_nickname", "解压小达人")
    assert "解压小达人" in page.douyin_name_label.text()


def test_cooldown_shows_in_name_label_and_clears(page) -> None:  # type: ignore[no-untyped-def]
    """方案一：风控冷却激活时登录小字可见"冷却中"，到期后自愈消失。"""
    import time

    page._config.set("douyin_cookies_set", True)
    assert "已登录" in page.douyin_name_label.text()
    assert "冷却" not in page.douyin_name_label.text()

    page._config.set("douyin_cooldown_until", time.time() + 600)
    assert "风控冷却中" in page.douyin_name_label.text()
    assert "分钟" in page.douyin_name_label.text()   # 带人话时长

    page._config.set("douyin_cooldown_until", 0)     # 冷却结束：小字消失
    assert "冷却" not in page.douyin_name_label.text()


def test_budget_tooltip_and_warning_threshold(page) -> None:  # type: ignore[no-untyped-def]
    """方案一：额度进 tooltip；剩余 ≤5 后每次用量递增提醒一次。"""
    page._search_includes_douyin = True
    page._warn_budget_if_needed()
    assert page._budget_warned_used == -1            # 低用量不提醒
    assert "额度" in page.douyin_name_label.toolTip()

    page._config.set("douyin_daily_usage", {"date": __import__("time").strftime("%Y-%m-%d"), "used": 16})
    page._warn_budget_if_needed()
    assert page._budget_warned_used == 16            # 进入告警区，提醒一次
    page._warn_budget_if_needed()
    assert page._budget_warned_used == 16            # 同用量不重复提醒

    page._config.set("douyin_daily_usage", {"date": __import__("time").strftime("%Y-%m-%d"), "used": 17})
    page._warn_budget_if_needed()
    assert page._budget_warned_used == 17            # 用量递增再次提醒
