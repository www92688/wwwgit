# 本地相似素材池：同关键词（回退同大类）的已有原始素材 → 重复度对比目标。
# 在线对比平台全部不可用时，这里是"和类似的视频做相似度对比"的兜底数据源。
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("ych.m3")

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".flv", ".ts"}
_DERIVED_SUFFIXES = ("_deduped", "_cleaned")   # 本应用自己的产物不入池
_MIRROR_DIR = "已去重"


def archive_parts(src: Path, workdir: Path) -> tuple[str, ...] | None:
    """src 相对工作目录的归档段（剥掉"已去重"镜像层）。

    布局 <workdir>/<大类>/<关键词>/<日期>/文件 → (大类, 关键词, 日期, 文件)；
    手动放入的无日期层为 (大类, 关键词, 文件)。不在工作目录内返回 None。
    """
    try:
        rel = Path(src).relative_to(Path(workdir))
    except ValueError:
        return None
    parts = tuple(p for p in rel.parts if p != _MIRROR_DIR)
    if len(parts) < 3:
        return None
    return parts


def keyword_of(src: Path, workdir: Path) -> str | None:
    """归档路径中的关键词段（= parts[1]，带不带日期层都成立）。"""
    parts = archive_parts(src, workdir)
    if parts is None:
        return None
    return parts[1] or None


def _is_pool_video(p: Path) -> bool:
    if p.suffix.lower() not in VIDEO_EXTS:
        return False
    if p.name.startswith("."):
        return False
    return not p.stem.endswith(_DERIVED_SUFFIXES)


def _videos_under(root: Path, src: Path, date: str | None) -> list[Path]:
    """root 下全部可入池视频：排除 src 自身/衍生文件/隐藏与临时目录。"""
    if not root.is_dir():
        return []
    out: list[Path] = []
    for p in root.rglob("*"):
        if not p.is_file() or not _is_pool_video(p) or p == src:
            continue
        rel = p.relative_to(root)
        if any(part.startswith(".") for part in rel.parts):
            continue
        out.append(p)
    # 确定性排序：同日期优先，其余按文件名；截断交给调用方 limit
    out.sort(key=lambda p: (0 if (date and p.parent.name == date) else 1,
                            p.name))
    return out


def local_similar_pool(
    src: Path, workdir: Path, limit: int = 8,
) -> list[Path]:
    """与 src 同关键词的其它原始素材（不含自身与 _deduped/_cleaned）。

    同关键词没有其它素材时回退同大类其它关键词；src 不在工作目录内、
    归档层级不足或目录不存在返回 []。同日期素材排前（更可能是同批下载）。
    """
    parts = archive_parts(src, workdir)
    if parts is None:
        return []
    category, keyword = parts[0], parts[1]
    date = parts[2] if len(parts) >= 4 else None
    src = Path(src)
    same = _videos_under(workdir / category / keyword, src, date)
    if same:
        return same[: max(1, int(limit))]
    cross = _videos_under(workdir / category, src, date)
    return cross[: max(1, int(limit))]
