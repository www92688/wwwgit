# YouTube 插件骨架占位（详设 12.1.5：首版统一不开放）
# 后续实现思路：yt-dlp 引擎集成（作为可选依赖）；主要风险：频繁失效需跟进上游更新
from __future__ import annotations

from ych.core.m1_capture.plugin_base import SkeletonPlugin


class YoutubePlugin(SkeletonPlugin):
    id = "youtube"
    display_name = "YouTube"
    region = "global"
    enabled_by_default = False
