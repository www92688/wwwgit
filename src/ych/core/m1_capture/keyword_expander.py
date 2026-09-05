# 关键词扩展（M1 采集辅助）：LLM 围绕主词扩展素材搜索词；纯函数便于测试
from __future__ import annotations

import re

MAX_KEYWORDS = 12

_SYSTEM_PROMPT = "你是短视频素材搜索关键词扩展助手，只输出关键词列表本身。"

_USER_PROMPT = (
    "围绕关键词「{kw}」扩展 {n} 个适合在免费视频素材站搜索素材的中文关键词。\n"
    "要求：每行一个；不要编号、引号或任何解释；不要重复原词；"
    "偏具体场景和事物，便于按词搜到画面。"
)

# 行首列表标记：1. / 1、 / 1） / - / • / ·
_LIST_PREFIX = re.compile(r"^(?:\d{1,3}\s*[.、)）]|[•\-*·])\s*")


def build_prompts(keyword: str, count: int = 10) -> tuple[str, str]:
    """返回 (system, user) 提示词。"""
    return _SYSTEM_PROMPT, _USER_PROMPT.format(kw=keyword.strip(), n=count)


def parse_keywords(raw: str, origin: str, max_n: int = MAX_KEYWORDS) -> list[str]:
    """解析 LLM 输出为关键词列表：去编号/引号/空白，去重，剔除原词。"""
    origin_norm = origin.strip()
    seen: set[str] = set()
    result: list[str] = []
    for line in raw.splitlines():
        word = _clean_line(line)
        if not word or word == origin_norm or word in seen:
            continue
        seen.add(word)
        result.append(word)
        if len(result) >= max_n:
            break
    return result


def _clean_line(line: str) -> str:
    word = _LIST_PREFIX.sub("", line.strip())
    word = word.strip("\"'“”‘’《》【】[] ")
    # 截断行内解释（"词 —— 说明" / "词：说明"）
    for sep in ("——", "：", ":"):
        if sep in word:
            word = word.split(sep, 1)[0].strip()
    return word.strip("-•* ").strip()
