# 小红书插件骨架占位（详设 12.1.5：首版统一不开放）
# 后续实现思路：网页笔记视频解析；主要风险：强风控、登录态
from __future__ import annotations

from ych.core.m1_capture.plugin_base import SkeletonPlugin


class XiaohongshuPlugin(SkeletonPlugin):
    id = "xiaohongshu"
    display_name = "小红书"
    region = "cn"
    enabled_by_default = True
