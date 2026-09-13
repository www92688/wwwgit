# S3 数据持久化单元测试（对照 7.6 / tasks/04-s3-db.md）
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ych.common.errors import ERR_DB_CONSTRAINT, ERR_DB_MIGRATION_FAILED, AppError
from ych.common.schemas import (
    CompareReport,
    CompareTarget,
    DimScores,
    ResumeState,
    VideoMeta,
)
from ych.services.s3_db.daos import (
    AssetRow,
    make_daos,
)
from ych.services.s3_db.database import Database


@pytest.fixture
def db(tmp_path) -> Database:
    return Database(tmp_path / "app.db")


@pytest.fixture
def daos(db):
    return make_daos(db)


EXPECTED_TABLES = {
    "search_history", "download_task", "process_task", "fail_record",
    "dedup_scheme", "compare_report", "app_settings", "asset_index",
    "keyword_category",
}


def test_migration_creates_nine_tables_and_version(db) -> None:
    tables = {
        r[0] for r in db.query(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    assert tables == EXPECTED_TABLES
    # v2：download_task 增加 retry_count（跨重启重试预算不重置）
    assert db.query("PRAGMA user_version")[0][0] == db.SCHEMA_VERSION == 2


def test_status_check_is_seven_state(db) -> None:
    # ⚠ 偏差修正验证：canceled 必须是合法终态
    def _w(conn) -> None:
        conn.execute(
            "INSERT INTO download_task(video_meta,status,keyword,platform_id)"
            " VALUES('{}','canceled','kw','p')"
        )

    db.write(_w)
    rows = db.query("SELECT status FROM download_task")
    assert rows[0][0] == "canceled"


def test_status_check_rejects_unknown_state(db) -> None:

    def _w(conn) -> None:
        conn.execute(
            "INSERT INTO download_task(video_meta,status,keyword,platform_id)"
            " VALUES('{}','bogus','kw','p')"
        )

    with pytest.raises(AppError) as exc:
        db.write(_w)
    assert exc.value.code == ERR_DB_CONSTRAINT


# ---------- SettingsDao ----------
def test_settings_roundtrip(daos) -> None:
    daos.settings.set("language", "zh_CN")
    assert daos.settings.get("language") == "zh_CN"
    daos.settings.set("language", "en_US")   # upsert
    assert daos.settings.get("language") == "en_US"
    assert daos.settings.get("nope") is None
    assert "language" in daos.settings.all()


# ---------- SearchHistoryDao ----------
def test_search_history_add_and_distinct(daos) -> None:
    daos.history.add("地毯清洗", ["pexels"])
    daos.history.add("水管疏通", ["pixabay"])
    daos.history.add("地毯清洗", ["pexels"])   # 重复词 → distinct 只一条
    kws = daos.history.distinct_keywords()
    assert [k for k, _t in kws] == ["地毯清洗", "水管疏通"]
# ---------- DownloadTaskDao ----------
def test_download_task_lifecycle(daos) -> None:
    meta = VideoMeta(plugin_id="pexels", video_key="42", title="t",
                     duration_s=12.0, width=1920, height=1080)
    task_id = daos.downloads.create(meta, "地毯清洗")
    row = daos.downloads.get(task_id)
    assert row is not None and row.status == "pending"

    resume = ResumeState(downloaded_bytes=1024, etag="e1",
                         total_bytes=4096, temp_path="x.part")
    daos.downloads.update_state(
        task_id, "running", progress=0.25, resume=resume
    )
    row2 = daos.downloads.get(task_id)
    assert row2 is not None
    assert row2.status == "running" and abs(row2.progress - 0.25) < 1e-9

    # JSON 字段往返
    assert daos.downloads.load_meta(row2).video_key == "42"
    loaded_resume = daos.downloads.load_resume(row2)
    assert loaded_resume is not None and loaded_resume.etag == "e1"

    daos.downloads.update_state(task_id, "success")
    assert daos.downloads.list_by_status("success")[0].id == task_id


# ---------- ProcessTaskDao ----------
def test_process_task_mark_running_and_finish(daos, tmp_path) -> None:
    src = tmp_path / "a.mp4"
    pid = daos.processes.create("preprocess", src, {"ops": {}})
    daos.processes.mark_running(pid)          # 先写库再执行
    row = daos.processes.get(pid)
    assert row is not None and row.status == "running"
    assert row in daos.processes.list_interrupted()   # 崩溃恢复扫描口径

    daos.processes.finish(pid, "success", {"elapsed": 1.5}, None)
    finished = daos.processes.get(pid)
    assert finished is not None and finished.status == "success"
    assert finished.finished_at is not None
    assert finished not in daos.processes.list_interrupted()


# ---------- FailRecordDao ----------
def test_fail_record_add_list_delete(daos) -> None:
    rid = daos.fails.add(
        "douyin_清洗_001.mp4", "转码失败", "MED010", "preprocess",
        {"src": "x.mp4"},
    )
    rows = daos.fails.list_recent()
    assert len(rows) == 1 and rows[0].file_name.endswith(".mp4")
    got = daos.fails.get(rid)
    assert got is not None and got.error_code == "MED010"
    daos.fails.delete(rid)
    assert daos.fails.list_recent() == []


# ---------- SchemeDao ----------
def test_scheme_save_upsert_by_name(daos) -> None:
    sid = daos.schemes.save("我的方案", [{"id": "mirror", "params": {}}])
    same = daos.schemes.save("我的方案", [{"id": "border", "params": {}}])
    assert sid == same                       # upsert 不产生新行
    assert daos.schemes.load("我的方案") == [{"id": "border", "params": {}}]
    assert len(daos.schemes.list_all()) == 1
    daos.schemes.delete(sid)
    assert daos.schemes.load("我的方案") is None


# ---------- ReportDao ----------
def test_compare_report_roundtrip(daos, tmp_path) -> None:
    dims = DimScores(composition=0.9, motion=0.8, rhythm=0.7, overall=0.85)
    target = CompareTarget(source="manual", local_path="b.mp4", scores=dims)
    report = CompareReport(
        src_path="a.mp4", overall_score=85.0, dims=dims,
        weights=(0.5, 0.25, 0.25), targets=[target],
    )
    src = tmp_path / "a.mp4"
    daos.reports.add(src, report)
    back = daos.reports.latest_for(src)
    assert back is not None
    assert back.overall_score == 85.0
    assert back.dims.motion == 0.8
    assert back.targets[0].scores is not None
    assert back.weights == (0.5, 0.25, 0.25)
    assert daos.reports.latest_for(tmp_path / "none.mp4") is None
# ---------- AssetIndexDao / CategoryDao ----------
def _asset(path: str, kind: str = "raw", **kw) -> AssetRow:
    defaults = dict(id=0, size_bytes=100, duration_s=1.0, width=64,
                    height=48, mtime=1.0,
                    category="清洗类", keyword="地毯清洗",
                    date_str="2026-08-25", indexed_at="")
    defaults.update(kw)
    return AssetRow(path=path, kind=kind, **defaults)


def test_asset_index_upsert_and_queries(daos, tmp_path) -> None:
    base = tmp_path / "workdir" / "清洗类" / "地毯清洗" / "2026-08-25"
    rows = [
        _asset(str(base / "pexels_地毯_001.mp4")),
        _asset(str(base / "pexels_地毯_002.mp4"), kind="cleaned"),
    ]
    daos.assets.upsert_many(rows)
    assert len(daos.assets.all_paths()) == 2
    assert len(daos.assets.find_by_dir(base)) == 2
    # LIKE 计数（序号分配辅助）
    pattern = str(base / "pexels_地毯_%") + ".mp4"
    assert daos.assets.count_named_like(pattern) == 2
    # upsert 同路径更新 kind
    updated = [_asset(str(base / "pexels_地毯_001.mp4"), kind="deduped")]
    daos.assets.upsert_many(updated)
    kinds = {Path(r.path).name: r.kind for r in daos.assets.find_by_dir(base)}
    assert kinds["pexels_地毯_001.mp4"] == "deduped"
    # 失效清理
    daos.assets.delete_paths({str(base / "pexels_地毯_002.mp4")})
    assert len(daos.assets.find_by_dir(base)) == 1


def test_asset_list_by_kind_filters(daos) -> None:
    daos.assets.upsert_many([
        _asset("D:/w/raw1.mp4", kind="raw"),
        _asset("D:/w/cleaned1.mp4", kind="cleaned"),
        _asset("D:/w/raw2.mp4", kind="raw"),
    ])
    paths = [r.path for r in daos.assets.list_by_kind("raw")]
    assert paths == ["D:/w/raw1.mp4", "D:/w/raw2.mp4"]
    assert daos.assets.list_by_kind("deduped") == []


def test_category_map_rename_merge(daos) -> None:
    daos.categories.map_keyword("地毯清洗", "清洗类")
    daos.categories.map_keyword("水管疏通", "管道类")
    assert daos.categories.category_of("地毯清洗") == "清洗类"
    # 重命名：映射表与 asset_index 同步更新
    daos.assets.upsert_many([_asset("D:/w/清洗类/x.mp4")])
    daos.categories.rename_category("清洗类", "清洁类")
    assert daos.categories.category_of("地毯清洗") == "清洁类"
    assert daos.assets.all_paths()
    # merge：src 类目并入 dst，冲突关键词保留 dst 映射
    daos.categories.map_keyword("地毯清洗", "清洁类")
    daos.categories.merge_categories("清洁类", "管道类")
    assert daos.categories.category_of("地毯清洗") == "管道类"
    assert daos.categories.category_of("水管疏通") == "管道类"


# ---------- 并发冒烟 ----------
def test_concurrent_writers_zero_exception(tmp_path) -> None:
    from threading import Thread

    database = Database(tmp_path / "conc.db")

    def _w(conn) -> None:
        conn.execute(
            "INSERT INTO app_settings(key,value) VALUES(?,?)",
            ("k", "v"),
        )

    errors: list[Exception] = []

    def worker(idx: int) -> None:
        try:
            for i in range(20):
                database.write(
                    lambda conn, k=f"t{idx}_{i}": conn.execute(
                        "INSERT INTO app_settings(key,value) VALUES(?,?)", (k, "v")
                    )
                )
        except Exception as exc:
            errors.append(exc)

    threads = [Thread(target=worker, args=(i,)) for i in (0, 1, 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors
    count = database.query("SELECT COUNT(*) FROM app_settings")[0][0]
    assert count == 60


def test_migration_failure_maps_db002(tmp_path, monkeypatch) -> None:
    from ych.services.s3_db import database as db_mod

    monkeypatch.setattr(db_mod, "MIGRATIONS", [(1, "CREATE BAD SQL ;")])
    database = Database(tmp_path / "bad.db")
    with pytest.raises(AppError) as exc:
        database.connection()
    assert exc.value.code == ERR_DB_MIGRATION_FAILED


def test_default_db_path_under_appdata(monkeypatch, tmp_path) -> None:
    from ych.services.s3_db.database import default_db_path

    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert default_db_path() == tmp_path / "YuChongGou" / "app.db"


def test_connection_creates_missing_parent_dir(tmp_path) -> None:
    # 首次启动 %LOCALAPPDATA%/YuChongGou 不存在：建库前须自动创建父目录
    database = Database(tmp_path / "no_such_dir" / "app.db")
    database.write(
        lambda conn: conn.execute(
            "INSERT INTO app_settings(key,value) VALUES('k','v')"
        )
    )
    assert database.query("SELECT COUNT(*) FROM app_settings")[0][0] == 1


# sqlite3 直查辅助（供断言用）
assert sqlite3.threadsafety >= 1

