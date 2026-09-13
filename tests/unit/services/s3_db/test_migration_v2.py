# 迁移 v2 + 下载重试预算落库回归
from __future__ import annotations

import sqlite3
from pathlib import Path

from ych.common.schemas import VideoMeta
from ych.services.s3_db.daos import make_daos
from ych.services.s3_db.database import Database
from ych.services.s3_db.migrations import DDL_V1


def _make_v1_db(path: Path) -> None:
    """手工构造 v1 旧库（无 retry_count 列），验证升级路径。"""
    con = sqlite3.connect(path)
    con.executescript(DDL_V1)
    con.execute("INSERT INTO download_task(video_meta,status,progress,keyword,"
                "platform_id) VALUES('{}','pending',0,'kw','pexels')")
    con.execute("PRAGMA user_version = 1")
    con.commit()
    con.close()


def test_migration_v2_adds_download_retry_count(tmp_path: Path) -> None:
    db_path = tmp_path / "app.db"
    _make_v1_db(db_path)

    db = Database(db_path)
    daos = make_daos(db)
    # 旧数据行可读，新列取默认 0
    row = daos.downloads.get(1)
    assert row is not None and row.retry_count == 0

    daos.downloads.increment_retry(1)
    daos.downloads.increment_retry(1)
    row = daos.downloads.get(1)
    assert row is not None and row.retry_count == 2


def test_resume_requeue_restores_retry_budget(tmp_path: Path) -> None:
    """崩溃恢复重排队回填已用重试次数：跨重启预算不重置。"""
    from PySide6.QtCore import QThreadPool

    from ych.core.m4_scheduler.task_scheduler import TaskScheduler
    from ych.services.s5_base.config_service import ConfigService

    daos = make_daos(Database(tmp_path / "app.db"))
    meta = VideoMeta(plugin_id="pexels", video_key="9")
    rid = daos.downloads.create(meta, "地毯清洗")
    daos.downloads.increment_retry(rid)
    daos.downloads.increment_retry(rid)
    daos.downloads.update_state(rid, "running")

    cfg = ConfigService()
    pool = QThreadPool()
    pool.setMaxThreadCount(2)
    sched = TaskScheduler(pool, cfg, daos)
    try:
        summary = sched.recover_on_startup()   # 走 _resume_download_task
        assert summary.resumed_downloads == 1
        # 新提交的任务从行内回填 retry_count=2
        tasks = [t for t in sched._tasks.values() if t.db_row_id == rid]
        assert tasks and tasks[0].retry_count == 2
    finally:
        pool.clear()
        pool.waitForDone(2000)
