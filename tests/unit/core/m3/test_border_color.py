# border 手法颜色安全化回归：纯黑 hex 不得生成非法滤镜
# （旧实现 lstrip("0x#") 会把 "000000" 剥成空串 → pad=...:0x → ffmpeg 失败）
from __future__ import annotations

import pytest

from ych.core.m3_dedup.techniques.border import _safe_color


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("black", "black"),
        ("#000000", "0x000000"),
        ("000000", "0x000000"),
        ("0x1A2B3C", "0x1a2b3c"),
        ("#FF8800", "0xff8800"),
        ("javascript:alert(1)", "black"),   # 非法串注入兜底
        ("", "black"),
    ],
)
def test_safe_color_hex_black(raw: str, expected: str) -> None:
    assert _safe_color(raw) == expected
