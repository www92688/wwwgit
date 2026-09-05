# 关键词扩展纯函数单元测试（提示词构造 + 输出解析）
from __future__ import annotations

from ych.core.m1_capture.keyword_expander import (
    build_prompts,
    parse_keywords,
)


def test_build_prompts_contains_keyword_and_count() -> None:
    system, user = build_prompts("地毯清洗", count=8)
    assert "助手" in system
    assert "地毯清洗" in user
    assert "8" in user


def test_parse_plain_lines() -> None:
    raw = "地毯清洗教程\n地毯污渍处理\n家政保洁\n"
    assert parse_keywords(raw, "地毯清洗") == [
        "地毯清洗教程", "地毯污渍处理", "家政保洁",
    ]


def test_parse_strips_numbering_and_marks() -> None:
    raw = "1. 地毯清洗教程\n2、沙发深度清洁\n3）地毯高温烘干\n- 布艺沙发清洗\n"
    assert parse_keywords(raw, "地毯清洗") == [
        "地毯清洗教程", "沙发深度清洁", "地毯高温烘干", "布艺沙发清洗",
    ]


def test_parse_cuts_inline_explanations_and_dedupes() -> None:
    raw = "地毯清洗教程——适合第一步\n地毯清洗教程\n地毯清洗教程：备用词\n"
    assert parse_keywords(raw, "地毯清洗") == ["地毯清洗教程"]


def test_parse_drops_origin_and_caps_count() -> None:
    raw = "\n".join(f"词{i}" for i in range(1, 20)) + "\n地毯清洗"
    words = parse_keywords(raw, "地毯清洗")
    assert len(words) == 12
    assert "地毯清洗" not in words


def test_parse_empty_output() -> None:
    assert parse_keywords("", "地毯清洗") == []
