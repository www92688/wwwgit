# 主题装配：theme.qss 为 @TOKEN 模板，按 浅色/深色/跟随系统 解析后应用
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from ych.common.fsutil import bundle_root

logger = logging.getLogger("ych.ui")


def _qss_path() -> Path:
    # 打包后源码进 exe，模板随 datas 落在 _internal/ych/ui/u6_common
    return bundle_root() / "ui" / "u6_common" / "theme.qss"

# 浅色（默认）与深色两套设计令牌：@TOKEN → 颜色值
_LIGHT: dict[str, str] = {
    "page": "#f2f4f8", "card": "#ffffff", "border": "#e2e6ee",
    "text": "#24292f", "text2": "#57606a", "text3": "#8b95a1",
    "input_bg": "#ffffff", "input_border": "#d4dae6", "input_border_h": "#b9c2d4",
    "hover": "#f0f3fa", "selected_bg": "#eef1fe", "selected_text": "#24292f",
    "nav_bg": "#232937", "nav_hover": "#2d3547", "nav_text": "#a9b3c6",
    "nav_text_h": "#dbe2ef", "side_footer": "#e8ebf3", "side_footer_h": "#dbe0ec",
    "primary": "#4c6ef5", "primary_h": "#3b5bdb", "primary_p": "#364fc7",
    "secondary": "#e8ebf3", "secondary_h": "#dbe0ec", "secondary_p": "#ccd3e4",
    "secondary_text": "#3b4252",
    "disabled_bg": "#c8cfdd", "disabled_text": "#f2f4f8",
    "alt_row": "#f8f9fc", "grid_line": "#eef0f5",
    "scrollbar": "#c9d0dd", "scrollbar_h": "#a8b2c4",
    "progress_bg": "#e8ebf3", "progress_text": "#3b4252",
    "header_bg": "#f7f8fb", "header_border": "#e6e9f0",
    "empty_title": "#a5aec0", "empty_hint": "#c2c9d6",
    "indicator_border": "#c0c8d8", "indeterminate": "#7d97f8",
    "arrow": "#7d879c", "arrow_h": "#4c6ef5",
    "splitter": "#e6e9f0", "splitter_h": "#9db1f8",
    "tab_text": "#57606a",
    "menu_bg": "#ffffff",
    "statusbar_bg": "#f2f4f8", "statusbar_border": "#e2e6ee",
}

_DARK: dict[str, str] = {
    "page": "#171a23", "card": "#232734", "border": "#2f3542",
    "text": "#e8ebf3", "text2": "#9aa3b5", "text3": "#6d7688",
    "input_bg": "#2a2f3d", "input_border": "#3d4456", "input_border_h": "#4d5468",
    "hover": "#2c3242", "selected_bg": "#313b5e", "selected_text": "#e8ebf3",
    "nav_bg": "#1d222e", "nav_hover": "#283042", "nav_text": "#9aa3b5",
    "nav_text_h": "#dbe2ef", "side_footer": "#333a4c", "side_footer_h": "#3c4356",
    "primary": "#5d7bf9", "primary_h": "#4a66e0", "primary_p": "#3d55c4",
    "secondary": "#333a4c", "secondary_h": "#3c4356", "secondary_p": "#454d63",
    "secondary_text": "#dfe3ee",
    "disabled_bg": "#3a4152", "disabled_text": "#7d879c",
    "alt_row": "#262b38", "grid_line": "#2c3242",
    "scrollbar": "#3d4456", "scrollbar_h": "#4d5468",
    "progress_bg": "#333a4c", "progress_text": "#dfe3ee",
    "header_bg": "#252a38", "header_border": "#2f3542",
    "empty_title": "#5d6779", "empty_hint": "#4d5566",
    "indicator_border": "#565f75", "indeterminate": "#7d97f8",
    "arrow": "#9aa3b5", "arrow_h": "#5d7bf9",
    "splitter": "#2f3542", "splitter_h": "#5d7bf9",
    "tab_text": "#9aa3b5",
    "menu_bg": "#2a2f3d",
    "statusbar_bg": "#171a23", "statusbar_border": "#2f3542",
}


def detect_system_theme() -> str:
    """读系统深浅色；无法判定时按浅色。"""
    try:
        hints = QGuiApplication.styleHints()
        scheme = hints.colorScheme()
    except Exception:
        return "light"
    name = getattr(scheme, "name", "")
    return "dark" if name == "Dark" else "light"


def resolve_theme(mode: str) -> str:
    """light / dark / system → 实际主题名。"""
    if mode == "system":
        return detect_system_theme()
    return "dark" if mode == "dark" else "light"


def render_theme(mode: str) -> str:
    """theme.qss 模板 + 配色令牌 → 最终 QSS。"""
    tokens = _DARK if resolve_theme(mode) == "dark" else _LIGHT
    qss = _qss_path().read_text(encoding="utf-8")
    for key, value in tokens.items():
        qss = qss.replace(f"@{key}", value)
    return qss


def apply_theme(app: QApplication | None, mode: str) -> None:
    """按模式应用主题（可重复调用实现运行时切换）。"""
    if app is None:
        return
    app.setStyleSheet(render_theme(mode))
    logger.info("主题已应用：%s（模式 %s）", resolve_theme(mode), mode)
