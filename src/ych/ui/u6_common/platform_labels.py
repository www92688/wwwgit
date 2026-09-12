# 平台 ID → 中文/品牌显示名（UI 展示用；新增平台在表里补一行）
from __future__ import annotations

PLATFORM_LABELS: dict[str, str] = {
    "douyin": "抖音",
    "kuaishou": "快手",
    "bilibili": "B站",
    "xiaohongshu": "小红书",
    "tiktok": "TikTok",
    "youtube": "YouTube",
    "pexels": "Pexels",
    "pixabay": "Pixabay",
}


def platform_label(plugin_id: str) -> str:
    return PLATFORM_LABELS.get(plugin_id, plugin_id)
