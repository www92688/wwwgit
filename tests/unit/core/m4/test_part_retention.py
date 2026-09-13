# .part 保留期限：失败/取消终态行超龄断点清理；interrupted（待重排队）保留
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

from ych.common.schemas import ResumeState, VideoMeta
from ych.core.m4_scheduler.crash_recovery import CrashRecovery
from ych.services.s3_db.daos import make_daos
from ych.services.s3_db.database import Database


def _age_row_updated_at(db_path: Path, rid: int, days: float) -> None:
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "UPDATE download_task SET updated_at="
            "datetime('now','localtime', ?) WHERE id=?",
            (f"-{days:g} days", rid),
        )
        con.commit()
    finally:
        con.close()


def _age_file(path: Path, days: float) -> None:
    path.write_bytes(b"part-data")
    old = time.time() - days * 86400
    os.utime(path, (old, old))


def test_stale_failed_row_part_removed_fresh_kept(tmp_path: Path) -> None:
    daos = make_daos(Database(tmp_path / "app.db"))
    wd = tmp_path / "wd"
    (wd / ".downloading").mkdir(parents=True)
    db_path = tmp_path / "app.db"

    stale_part = wd / ".downloading" / "old.part"
    fresh_part = wd / ".downloading" / "new.part"
    _age_file(stale_part, days=10)
    _age_file(fresh_part, days=0.1)   # 新文件，行也是新失败

    rid_old = daos.downloads.create(VideoMeta(plugin_id="pexels", video_key="1"),
                                    "kw")
    daos.downloads.update_state(rid_old, "failed", resume=ResumeState(
        downloaded_bytes=10, temp_path=str(stale_part)))
    rid_new = daos.downloads.create(VideoMeta(plugin_id="pexels", video_key="2"),
                                    "kw")
    daos.downloads.update_state(rid_new, "failed", resume=ResumeState(
        downloaded_bytes=10, temp_path=str(fresh_part)))
    _age_row_updated_at(db_path, rid_old, days=10)

    summary = CrashRecovery(daos, lambda: wd).scan()
    assert not stale_part.exists()       # 超龄终态断点：删
    assert fresh_part.exists()           # 未超龄：保留
    assert summary.removed_orphan_parts == 1


def test_interrupted_row_part_kept_for_requeue(tmp_path: Path) -> None:
    """interrupted 行启动时会被重排队续传，断点不参与超龄清理。"""
    daos = make_daos(Database(tmp_path / "app.db"))
    wd = tmp_path / "wd"
    (wd / ".downloading").mkdir(parents=True)
    db_path = tmp_path / "app.db"

    part = wd / ".downloading" / "resume.part"
    _age_file(part, days=30)
    rid = daos.downloads.create(VideoMeta(plugin_id="pexels", video_key="3"),
                                "kw")
    daos.downloads.update_state(rid, "running", resume=ResumeState(
        downloaded_bytes=10, temp_path=str(part)))
    _age_row_updated_at(db_path, rid, days=30)
    # 置 interrupted（崩溃恢复的前置状态）后再扫
    daos.downloads.update_state(rid, "interrupted", resume=ResumeState(
        downloaded_bytes=10, temp_path=str(part)))

    CrashRecovery(daos, lambda: wd).scan(resume_cb=None)
    assert part.exists()                 # 仍引用：保留
