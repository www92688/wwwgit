# B站插件骨架占位（详设 12.1.5：首版统一不开放）
# 后续实现思路：官方开放 API（search/video info）+ 播放地址接口；
# 主要风险：需处理清晰度权限与 Referer 防盗链
from __future__ import annotations

from ych.core.m1_capture.plugin_base import SkeletonPlugin


class BilibiliPlugin(SkeletonPlugin):
    id = "bilibili"
    display_name = "B站"
    region = "cn"
    enabled_by_default = True
