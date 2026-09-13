# 崩溃恢复健壮性：单行脏数据（meta JSON 损坏）不得中断整轮恢复
from __future__ import annotations

import sqlite3
from pathlib import Path

from ych.common.schemas import VideoMeta
from ych.core.m4_scheduler.crash_recovery import CrashRecovery
from ych.services.s3_db.daos import make_daos
from ych.services.s3_db.database import Database


def test_dirty_meta_row_does_not_abort_scan(tmp_path: Path) -> None:
    daos = make_daos(Database(tmp_path / "app.db"))
    good = daos.downloads.create(
        VideoMeta(plugin_id="pexels", video_key="1"), "正常词")
    bad = daos.downloads.create(
        VideoMeta(plugin_id="pexels", video_key="2"), "脏数据词")
    daos.downloads.update_state(good, "running")
    daos.downloads.update_state(bad, "running")
    con = sqlite3.connect(tmp_path / "app.db")
    try:
        con.execute(
            "UPDATE download_task SET video_meta='{bad json' WHERE id=?",
            (bad,),
        )
        con.commit()
    finally:
        con.close()

    import json

    resumed: list[int] = []

    def resume_cb(rid: int, meta_json: str, _kw: str) -> None:
        json.loads(meta_json)   # 与真实 _resume_download_task 同一解析路径
        resumed.append(rid)

    summary = CrashRecovery(daos).scan(resume_cb=resume_cb)
    # 好行照常重排队续传；脏行在解析时抛错被逐行捕获跳过
    assert resumed == [good]
    assert summary.resumed_downloads == 1


def test_dirty_process_row_does_not_abort_scan(tmp_path: Path) -> None:
    daos = make_daos(Database(tmp_path / "app.db"))
    pid = daos.processes.create("preprocess", tmp_path / "a.mp4", {})
    daos.processes.mark_running(pid)
    summary = CrashRecovery(daos).scan()
    assert summary.moved_to_fail == 1
