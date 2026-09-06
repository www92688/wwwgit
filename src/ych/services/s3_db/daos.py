# DAO 层（详设 7.5）：上层只见 DAO 与 dataclass，不见 SQL
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ych.common.errors import ERR_DB_IO, AppError
from ych.common.schemas import (
    CompareReport,
    CompareTarget,
    DimScores,
    ResumeState,
    TaskState,
    VideoMeta,
)
from ych.services.s3_db.database import Database

if TYPE_CHECKING:
    from sqlite3 import Row


# ---------- 行数据类（字段=DDL 列名，冻结） ----------
@dataclass(frozen=True)
class DownloadTaskRow:
    id: int
    video_meta: str          # VideoMeta JSON
    status: str
    progress: float
    dest_path: str | None
    resume_state: str | None  # ResumeState JSON
    keyword: str
    platform_id: str
    error_code: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ProcessTaskRow:
    id: int
    task_type: str
    src_path: str
    dst_path: str | None
    status: str
    retry_count: int
    params: str | None
    result_summary: str | None
    error_code: str | None
    created_at: str
    finished_at: str | None


@dataclass(frozen=True)
class FailRecordRow:
    id: int
    file_name: str
    fail_reason: str
    error_code: str | None
    fail_time: str
    task_type: str
    payload: str


@dataclass(frozen=True)
class SchemeRow:
    id: int
    name: str
    techniques_config: str   # [{id, params}] JSON
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class AssetRow:
    id: int
    path: str
    kind: str                # raw|cleaned|deduped
    size_bytes: int | None
    duration_s: float | None
    width: int | None
    height: int | None
    mtime: float | None
    category: str | None
    keyword: str | None
    date_str: str | None
    indexed_at: str


def _row_to_dataclass(row: Row, cls: type) -> object:
    """sqlite3.Row → 冻结 dataclass（按字段名取列）。"""
    names = {f.name for f in fields(cls)}
    kwargs = {k: row[k] for k in row.keys() if k in names}  # noqa: SIM118
    return cls(**kwargs)


def _dump(obj: Any) -> str:
    return json.dumps(asdict(obj), ensure_ascii=False)


# ---------- SettingsDao ----------
class SettingsDao:
    def __init__(self, db: Database) -> None:
        self._db = db

    def get(self, key: str) -> str | None:
        rows = self._db.query(
            "SELECT value FROM app_settings WHERE key=?", (key,)
        )
        return rows[0][0] if rows else None

    def set(self, key: str, value: str) -> None:
        def _w(conn: sqlite3.Connection) -> None:
            conn.execute(
                "INSERT INTO app_settings(key,value) VALUES(?,?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

        self._db.write(_w)

    def all(self) -> dict[str, str]:
        rows = self._db.query("SELECT key,value FROM app_settings")
        return {r[0]: r[1] for r in rows}


# ---------- SearchHistoryDao ----------
class SearchHistoryDao:
    def __init__(self, db: Database) -> None:
        self._db = db

    def add(self, keyword: str, platform_ids: list[str]) -> None:
        def _w(conn: sqlite3.Connection) -> None:
            conn.execute(
                "INSERT INTO search_history(keyword,platform_ids) VALUES(?,?)",
                (keyword, json.dumps(platform_ids, ensure_ascii=False)),
            )

        self._db.write(_w)

    def distinct_keywords(self, limit: int = 20) -> list[tuple[str, str]]:
        """(词, 最近时间)，按最近使用排序去重；同秒并列以更大 rowid 为新。"""
        rows = self._db.query(
            "SELECT keyword, MAX(created_at) AS latest FROM search_history"
            " GROUP BY keyword ORDER BY latest DESC, MAX(id) DESC LIMIT ?",
            (limit,),
        )
        return [(r[0], r[1]) for r in rows]


# ---------- DownloadTaskDao ----------
class DownloadTaskDao:
    def __init__(self, db: Database) -> None:
        self._db = db

    def create(self, meta: VideoMeta, keyword: str) -> int:
        def _w(conn: sqlite3.Connection) -> int:
            cur = conn.execute(
                "INSERT INTO download_task(video_meta,status,progress,keyword,platform_id)"
                " VALUES(?,?,?,?,?)",
                (_dump(meta), "pending", 0.0, keyword, meta.plugin_id),
            )
            rid = cur.lastrowid
            assert rid is not None
            return int(rid)

        return self._db.write(_w)

    def update_state(
        self,
        task_id: int,
        status: TaskState,
        progress: float | None = None,
        resume: ResumeState | None = None,
        error_code: str | None = None,
    ) -> None:
        sets = ["status=?", "updated_at=datetime('now','localtime')"]
        args: list[object] = [status]
        if progress is not None:
            sets.append("progress=?")
            args.append(progress)
        if resume is not None:
            sets.append("resume_state=?")
            args.append(json.dumps(asdict(resume), ensure_ascii=False))
        if error_code is not None:
            sets.append("error_code=?")
            args.append(error_code)
        args.append(task_id)

        def _w(conn: sqlite3.Connection) -> None:
            conn.execute(
                f"UPDATE download_task SET {', '.join(sets)} WHERE id=?", args
            )

        self._db.write(_w)

    def get(self, task_id: int) -> DownloadTaskRow | None:
        rows = self._db.query(
            "SELECT * FROM download_task WHERE id=?", (task_id,)
        )
        if not rows:
            return None
        result = _row_to_dataclass(rows[0], DownloadTaskRow)
        assert isinstance(result, DownloadTaskRow)
        return result

    def list_by_status(self, status: TaskState) -> list[DownloadTaskRow]:
        rows = self._db.query(
            "SELECT * FROM download_task WHERE status=? ORDER BY id", (status,)
        )
        out: list[DownloadTaskRow] = []
        for r in rows:
            item = _row_to_dataclass(r, DownloadTaskRow)
            assert isinstance(item, DownloadTaskRow)
            out.append(item)
        return out

    def find_resumable(self, plugin_id: str, video_key: str) -> DownloadTaskRow | None:
        """同素材最近一条带断点状态且未完成的行（重新提交时复用续传）。"""
        rows = self._db.query(
            "SELECT * FROM download_task"
            " WHERE platform_id=? AND dest_path IS NULL AND resume_state IS NOT NULL"
            " AND status IN ('failed','canceled','interrupted')"
            " AND json_extract(video_meta,'$.video_key')=?"
            " ORDER BY id DESC LIMIT 1",
            (plugin_id, video_key),
        )
        if not rows:
            return None
        result = _row_to_dataclass(rows[0], DownloadTaskRow)
        assert isinstance(result, DownloadTaskRow)
        return result

    def set_dest(self, task_id: int, dest: str) -> None:
        def _w(conn: sqlite3.Connection) -> None:
            conn.execute(
                "UPDATE download_task SET dest_path=? WHERE id=?", (dest, task_id)
            )

        self._db.write(_w)
    def load_resume(self, row: DownloadTaskRow) -> ResumeState | None:
        """行内 resume_state JSON → ResumeState。"""
        if not row.resume_state:
            return None
        try:
            data = json.loads(row.resume_state)
        except json.JSONDecodeError as exc:
            raise AppError(ERR_DB_IO, "续传状态反序列化失败", cause=exc) from exc
        return ResumeState(**data)

    def load_meta(self, row: DownloadTaskRow) -> VideoMeta:
        try:
            data = json.loads(row.video_meta)
        except json.JSONDecodeError as exc:
            raise AppError(ERR_DB_IO, "视频元数据反序列化失败", cause=exc) from exc
        return VideoMeta(**data)


# ---------- ProcessTaskDao ----------
class ProcessTaskDao:
    def __init__(self, db: Database) -> None:
        self._db = db

    def create(self, task_type: str, src: Path, params: dict[str, object]) -> int:
        def _w(conn: sqlite3.Connection) -> int:
            cur = conn.execute(
                "INSERT INTO process_task(task_type,src_path,status,retry_count,params)"
                " VALUES(?,?,?,0,?)",
                (task_type, str(src), "pending",
                 json.dumps(params, ensure_ascii=False)),
            )
            rid = cur.lastrowid
            assert rid is not None
            return int(rid)

        return self._db.write(_w)

    def mark_running(self, task_id: int) -> None:
        """先写库再执行（T-规则：崩溃后可恢复）。"""
        def _w(conn: sqlite3.Connection) -> None:
            conn.execute(
                "UPDATE process_task SET status='running' WHERE id=?", (task_id,)
            )

        self._db.write(_w)

    def finish(
        self,
        task_id: int,
        status: TaskState,
        summary: dict[str, object] | None,
        error_code: str | None,
    ) -> None:
        def _w(conn: sqlite3.Connection) -> None:
            conn.execute(
                "UPDATE process_task SET status=?,"
                " result_summary=?, error_code=?, finished_at=datetime('now','localtime')"
                " WHERE id=?",
                (status,
                 json.dumps(summary, ensure_ascii=False) if summary else None,
                 error_code, task_id),
            )

        self._db.write(_w)

    def set_dst(self, task_id: int, dst: Path) -> None:
        def _w(conn: sqlite3.Connection) -> None:
            conn.execute(
                "UPDATE process_task SET dst_path=? WHERE id=?", (str(dst), task_id)
            )

        self._db.write(_w)

    def increment_retry(self, task_id: int) -> None:
        def _w(conn: sqlite3.Connection) -> None:
            conn.execute(
                "UPDATE process_task SET retry_count=retry_count+1 WHERE id=?",
                (task_id,),
            )

        self._db.write(_w)

    def get(self, task_id: int) -> ProcessTaskRow | None:
        rows = self._db.query("SELECT * FROM process_task WHERE id=?", (task_id,))
        if not rows:
            return None
        result = _row_to_dataclass(rows[0], ProcessTaskRow)
        assert isinstance(result, ProcessTaskRow)
        return result

    def list_interrupted(self) -> list[ProcessTaskRow]:
        rows = self._db.query(
            "SELECT * FROM process_task WHERE status='running' ORDER BY id"
        )
        out: list[ProcessTaskRow] = []
        for r in rows:
            item = _row_to_dataclass(r, ProcessTaskRow)
            assert isinstance(item, ProcessTaskRow)
            out.append(item)
        return out


# ---------- FailRecordDao ----------
class FailRecordDao:
    def __init__(self, db: Database) -> None:
        self._db = db

    def add(
        self,
        file_name: str,
        reason: str,
        code: str,
        task_type: str,
        payload: dict[str, object],
    ) -> int:
        def _w(conn: sqlite3.Connection) -> int:
            cur = conn.execute(
                "INSERT INTO fail_record(file_name,fail_reason,error_code,task_type,payload)"
                " VALUES(?,?,?,?,?)",
                (file_name, reason, code, task_type,
                 json.dumps(payload, ensure_ascii=False)),
            )
            rid = cur.lastrowid
            assert rid is not None
            return int(rid)

        return self._db.write(_w)

    def list_recent(self, limit: int = 200) -> list[FailRecordRow]:
        rows = self._db.query(
            "SELECT * FROM fail_record ORDER BY fail_time DESC LIMIT ?", (limit,)
        )
        out: list[FailRecordRow] = []
        for r in rows:
            item = _row_to_dataclass(r, FailRecordRow)
            assert isinstance(item, FailRecordRow)
            out.append(item)
        return out

    def get(self, record_id: int) -> FailRecordRow | None:
        rows = self._db.query("SELECT * FROM fail_record WHERE id=?", (record_id,))
        if not rows:
            return None
        result = _row_to_dataclass(rows[0], FailRecordRow)
        assert isinstance(result, FailRecordRow)
        return result

    def delete(self, record_id: int) -> None:
        def _w(conn: sqlite3.Connection) -> None:
            conn.execute("DELETE FROM fail_record WHERE id=?", (record_id,))

        self._db.write(_w)


# ---------- SchemeDao ----------
class SchemeDao:
    def __init__(self, db: Database) -> None:
        self._db = db

    def save(self, name: str, config: list[dict[str, object]]) -> int:
        """upsert by name（自定义方案同名覆盖）。"""
        def _w(conn: sqlite3.Connection) -> int:
            conn.execute(
                "INSERT INTO dedup_scheme(name,techniques_config)"
                " VALUES(?,?)"
                " ON CONFLICT(name) DO UPDATE SET"
                " techniques_config=excluded.techniques_config,"
                " updated_at=datetime('now','localtime')",
                (name, json.dumps(config, ensure_ascii=False)),
            )
            row = conn.execute(
                "SELECT id FROM dedup_scheme WHERE name=?", (name,)
            ).fetchone()
            return int(row[0])

        return self._db.write(_w)

    def load(self, name: str) -> list[dict[str, object]] | None:
        rows = self._db.query(
            "SELECT techniques_config FROM dedup_scheme WHERE name=?", (name,)
        )
        if not rows:
            return None
        try:
            config = json.loads(rows[0][0])
        except json.JSONDecodeError as exc:
            raise AppError(ERR_DB_IO, "方案配置反序列化失败", cause=exc) from exc
        assert isinstance(config, list)
        return config

    def list_all(self) -> list[SchemeRow]:
        rows = self._db.query("SELECT * FROM dedup_scheme ORDER BY id")
        out: list[SchemeRow] = []
        for r in rows:
            item = _row_to_dataclass(r, SchemeRow)
            assert isinstance(item, SchemeRow)
            out.append(item)
        return out

    def delete(self, scheme_id: int) -> None:
        def _w(conn: sqlite3.Connection) -> None:
            conn.execute("DELETE FROM dedup_scheme WHERE id=?", (scheme_id,))

        self._db.write(_w)


# ---------- ReportDao ----------
class ReportDao:
    def __init__(self, db: Database) -> None:
        self._db = db

    def add(self, src: Path, report: CompareReport) -> int:
        def _w(conn: sqlite3.Connection) -> int:
            cur = conn.execute(
                "INSERT INTO compare_report(src_path,report) VALUES(?,?)",
                (str(src), _dump(report)),
            )
            rid = cur.lastrowid
            assert rid is not None
            return int(rid)

        return self._db.write(_w)

    def latest_for(self, src: Path) -> CompareReport | None:
        rows = self._db.query(
            "SELECT report FROM compare_report WHERE src_path=?"
            " ORDER BY id DESC LIMIT 1",   # id 单调递增：同秒多报告也取最新
            (str(src),),
        )
        if not rows:
            return None
        try:
            data = json.loads(rows[0][0])
        except json.JSONDecodeError as exc:
            raise AppError(ERR_DB_IO, "报告反序列化失败", cause=exc) from exc
        dims = DimScores(**data["dims"])
        targets = [
            CompareTarget(
                **{**t, "scores": DimScores(**t["scores"]) if t.get("scores") else None}
            )
            for t in data["targets"]
        ]
        return CompareReport(
            **{**data, "dims": dims, "targets": targets,
               "weights": tuple(data["weights"])}
        )


# ---------- AssetIndexDao ----------
class AssetIndexDao:
    def __init__(self, db: Database) -> None:
        self._db = db

    def upsert_many(self, items: list[AssetRow]) -> None:
        def _w(conn: sqlite3.Connection) -> None:
            for it in items:
                conn.execute(
                    "INSERT INTO asset_index(path,kind,size_bytes,duration_s,width,"
                    "height,mtime,category,keyword,date_str)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?)"
                    " ON CONFLICT(path) DO UPDATE SET"
                    " kind=excluded.kind,size_bytes=excluded.size_bytes,"
                    " duration_s=excluded.duration_s,width=excluded.width,"
                    " height=excluded.height,mtime=excluded.mtime,"
                    " category=excluded.category,keyword=excluded.keyword,"
                    " date_str=excluded.date_str,"
                    " indexed_at=datetime('now','localtime')",
                    (it.path, it.kind, it.size_bytes, it.duration_s, it.width,
                     it.height, it.mtime, it.category, it.keyword, it.date_str),
                )

        self._db.write(_w)

    def find_by_dir(self, dir_path: Path) -> list[AssetRow]:
        like = str(dir_path).rstrip("\\/") + "\\%"
        rows = self._db.query(
            "SELECT * FROM asset_index WHERE path LIKE ? ORDER BY path", (like,)
        )
        out: list[AssetRow] = []
        for r in rows:
            item = _row_to_dataclass(r, AssetRow)
            assert isinstance(item, AssetRow)
            out.append(item)
        return out

    def count_named_like(self, pattern: str) -> int:
        """序号分配辅助：统计匹配 LIKE 模式的既有条目数。"""
        rows = self._db.query(
            "SELECT COUNT(*) FROM asset_index WHERE path LIKE ?", (pattern,)
        )
        return int(rows[0][0])

    def list_by_kind(self, kind: str) -> list[AssetRow]:
        """按 kind（raw/cleaned/deduped）取素材行，供工作台素材列表。"""
        rows = self._db.query(
            "SELECT * FROM asset_index WHERE kind=? ORDER BY path", (kind,)
        )
        out: list[AssetRow] = []
        for r in rows:
            item = _row_to_dataclass(r, AssetRow)
            assert isinstance(item, AssetRow)
            out.append(item)
        return out

    def all_paths(self) -> set[str]:
        rows = self._db.query("SELECT path FROM asset_index")
        return {r[0] for r in rows}

    def delete_paths(self, paths: set[str]) -> None:
        """增量扫描时清理已失效的行。"""
        if not paths:
            return

        def _w(conn: sqlite3.Connection) -> None:
            for p in paths:
                conn.execute("DELETE FROM asset_index WHERE path=?", (p,))

        self._db.write(_w)


# ---------- CategoryDao ----------
class CategoryDao:
    def __init__(self, db: Database) -> None:
        self._db = db

    def map_keyword(self, keyword: str, category: str) -> None:
        def _w(conn: sqlite3.Connection) -> None:
            conn.execute(
                "INSERT INTO keyword_category(keyword,category) VALUES(?,?)"
                " ON CONFLICT(keyword) DO UPDATE SET category=excluded.category,"
                " updated_at=datetime('now','localtime')",
                (keyword, category),
            )

        self._db.write(_w)

    def category_of(self, keyword: str) -> str | None:
        rows = self._db.query(
            "SELECT category FROM keyword_category WHERE keyword=?", (keyword,)
        )
        return rows[0][0] if rows else None

    def rename_category(self, old: str, new: str) -> None:
        """事务内同时更新映射表与素材索引两表。"""
        def _w(conn: sqlite3.Connection) -> None:
            conn.execute(
                "UPDATE keyword_category SET category=?,"
                " updated_at=datetime('now','localtime') WHERE category=?",
                (new, old),
            )
            conn.execute(
                "UPDATE asset_index SET category=? WHERE category=?", (new, old)
            )

        self._db.write(_w)

    def merge_categories(self, src: str, dst: str) -> None:
        """归并：src 类目全部并入 dst（物理搬移由 M5 CategoryService 负责）。"""
        def _w(conn: sqlite3.Connection) -> None:
            # 先删掉 dst 下与 src 同名关键词的冲突映射，再整体改类目
            conn.execute(
                "DELETE FROM keyword_category WHERE category=? AND keyword IN"
                " (SELECT keyword FROM keyword_category WHERE category=?)",
                (dst, src),
            )
            conn.execute(
                "UPDATE keyword_category SET category=?,"
                " updated_at=datetime('now','localtime') WHERE category=?",
                (dst, src),
            )
            conn.execute(
                "DELETE FROM asset_index WHERE category=? AND path IN"
                " (SELECT a2.path FROM asset_index a2 WHERE a2.category=?)",
                (dst, src),
            )
            conn.execute(
                "UPDATE asset_index SET category=? WHERE category=?", (dst, src)
            )

        self._db.write(_w)


# ---------- 聚合包（M4 等处统一注入用） ----------
@dataclass
class DaosBundle:
    settings: SettingsDao
    history: SearchHistoryDao
    downloads: DownloadTaskDao
    processes: ProcessTaskDao
    fails: FailRecordDao
    schemes: SchemeDao
    reports: ReportDao
    assets: AssetIndexDao
    categories: CategoryDao


def make_daos(db: Database) -> DaosBundle:
    """按同一 Database 构建全部 DAO 的便捷工厂。"""
    return DaosBundle(
        settings=SettingsDao(db),
        history=SearchHistoryDao(db),
        downloads=DownloadTaskDao(db),
        processes=ProcessTaskDao(db),
        fails=FailRecordDao(db),
        schemes=SchemeDao(db),
        reports=ReportDao(db),
        assets=AssetIndexDao(db),
        categories=CategoryDao(db),
    )








