# 主题模块：双模式渲染有效性 + 解析逻辑
from __future__ import annotations

import re

from ych.ui.u6_common import theme as theme_mod

_TOKEN_RE = re.compile(r"@[a-z_]+")


def test_render_light_and_dark_differ() -> None:
    light = theme_mod.render_theme("light")
    dark = theme_mod.render_theme("dark")
    assert light != dark
    # 模板占位符必须全部被替换（不允许「@小写词」残留）
    for qss in (light, dark):
        assert _TOKEN_RE.search(qss) is None


def test_resolve_explicit_modes() -> None:
    assert theme_mod.resolve_theme("light") == "light"
    assert theme_mod.resolve_theme("dark") == "dark"
    assert theme_mod.resolve_theme("system") in ("light", "dark")
    assert theme_mod.resolve_theme("bogus") in ("light", "dark")   # 容错


def test_system_theme_detection_runs() -> None:
    # 无头环境下仅要求可调用且返回合法值
    assert theme_mod.detect_system_theme() in ("light", "dark")
