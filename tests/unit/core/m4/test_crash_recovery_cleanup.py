# .downloading 孤儿 .part 清理（崩溃恢复扫描顺带，详设 11.3 拾遗）
from __future__ import annotations

import os
import time
from pathlib import Path

from ych.common.schemas import ResumeState, VideoMeta
from ych.core.m4_scheduler.crash_recovery import CrashRecovery
from ych.services.s3_db.daos import make_daos
from ych.services.s3_db.database import Database


def _make_env(tmp_path: Path):
    daos = make_daos(Database(tmp_path / "app.db"))
    wd = tmp_path / "wd"
    (wd / ".downloading").mkdir(parents=True)
    return daos, wd


def _aged(path: Path, hours: float = 2.0) -> None:
    path.write_bytes(b"x")
    old = time.time() - hours * 3600
    os.utime(path, (old, old))


def test_orphan_parts_removed_referenced_kept(tmp_path: Path) -> None:
    daos, wd = _make_env(tmp_path)
    dl = wd / ".downloading"
    referenced = dl / "aaa.part"        # 失败行断点引用 → 保留（可续传）
    orphan = dl / "bbb.part"            # 无引用旧文件 → 删
    fresh = dl / "ccc.part"             # 无引用但太新 → 保留（防竞态门槛）
    not_part = dl / "ddd.tmp"           # 非 .part → 不碰
    fresh.write_bytes(b"x")             # 保持当前 mtime
    for f in (referenced, orphan, not_part):
        _aged(f)

    meta = VideoMeta(plugin_id="pexels", video_key="1")
    row = daos.downloads.create(meta, "地毯清洗")
    daos.downloads.update_state(row, "failed", resume=ResumeState(
        downloaded_bytes=10, temp_path=str(referenced)))

    summary = CrashRecovery(daos, lambda: wd).scan()
    assert referenced.exists() and fresh.exists() and not_part.exists()
    assert not orphan.exists()
    assert summary.removed_orphan_parts == 1


def test_success_row_reference_no_longer_protects(tmp_path: Path) -> None:
    """success 行的临时文件已被归档移动，残留引用不再保留 .part。"""
    daos, wd = _make_env(tmp_path)
    stale = wd / ".downloading" / "done.part"
    _aged(stale)
    meta = VideoMeta(plugin_id="pexels", video_key="2")
    row = daos.downloads.create(meta, "kw")
    daos.downloads.update_state(row, "success", resume=ResumeState(
        downloaded_bytes=99, temp_path=str(stale)))

    summary = CrashRecovery(daos, lambda: wd).scan()
    assert not stale.exists()
    assert summary.removed_orphan_parts == 1


def test_unset_or_missing_workdir_is_noop(tmp_path: Path) -> None:
    daos, wd = _make_env(tmp_path)
    keep = wd / ".downloading" / "a.part"
    _aged(keep)

    # 未提供 provider / 工作目录未设置 / 目录不存在 → 全部安全跳过
    assert CrashRecovery(daos).scan().removed_orphan_parts == 0
    assert CrashRecovery(daos, lambda: None).scan().removed_orphan_parts == 0
    summary = CrashRecovery(daos, lambda: tmp_path / "nope").scan()
    assert summary.removed_orphan_parts == 0
    assert keep.exists()
