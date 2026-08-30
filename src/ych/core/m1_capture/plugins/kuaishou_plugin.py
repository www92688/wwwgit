# 快手插件骨架占位（详设 12.1.5：首版统一不开放）
# 后续实现思路：网页端 GraphQL 接口；主要风险：Cookie/风控
from __future__ import annotations

from ych.core.m1_capture.plugin_base import SkeletonPlugin


class KuaishouPlugin(SkeletonPlugin):
    id = "kuaishou"
    display_name = "快手"
    region = "cn"
    enabled_by_default = True
