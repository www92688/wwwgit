# 抖音插件骨架占位（详设 12.1.5：首版统一不开放）
# 后续实现思路：网页端搜索接口逆向 + 无水印直链解析；主要风险：X-Bogus/msToken 签名频繁变更
from __future__ import annotations

from ych.core.m1_capture.plugin_base import SkeletonPlugin


class DouyinPlugin(SkeletonPlugin):
    id = "douyin"
    display_name = "抖音"
    region = "cn"
    enabled_by_default = True
