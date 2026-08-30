# 搜索结果筛选（详设 12.2）：纯函数，表驱动单测对象
from __future__ import annotations

from ych.common.schemas import SearchFilters, VideoMeta

_MB = 1024.0 * 1024.0


class ResultFilter:
    """时长闭开区间 / 最低高度 / 大小上下限 / 水印三态 过滤 + 稳定排序。

    - file_size_bytes 为 None（平台未提供）时大小条件按"不限"放行；
    - watermark="unknown" 表示不限制；
    - 排序：分辨率降序 → 时长升序。
    """

    def apply(self, items: list[VideoMeta], f: SearchFilters) -> list[VideoMeta]:
        out: list[VideoMeta] = []
        for m in items:
            if m.duration_s < f.duration_min_s or m.duration_s >= f.duration_max_s:
                continue
            if f.min_height > 0 and m.height < f.min_height:
                continue
            if m.file_size_bytes is not None:
                size_mb = m.file_size_bytes / _MB
                if size_mb < f.size_min_mb or size_mb > f.size_max_mb:
                    continue
            if f.watermark != "unknown" and m.watermark_tag != f.watermark:
                continue
            out.append(m)
        out.sort(key=lambda m: (-m.height, m.duration_s))
        return out
