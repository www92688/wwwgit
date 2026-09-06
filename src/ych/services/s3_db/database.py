# SQLite 连接管理 / 迁移 / 事务（详设 7.2/7.4；T-5 线程约定）
from __future__ import annotations

import os
import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from ych.common.errors import ERR_DB_IO, ERR_DB_MIGRATION_FAILED, ERR_DB_OPEN_FAILED, AppError
from ych.services.s3_db.migrations import MIGRATIONS

logger_name = "ych.s3"

T = TypeVar("T")

_SCHEMA_VERSION = 1


def default_db_path() -> Path:
    """库文件位置：%APPDATA%/YuChongGou/app.db（不混入素材工作目录）。"""
    appdata = Path(os.environ.get("APPDATA", str(Path.home())))
    return appdata / "YuChongGou" / "app.db"



class Database:
    """thread-local 连接 + WAL + 写锁串行化（避免 SQLITE_BUSY）。"""

    SCHEMA_VERSION = _SCHEMA_VERSION

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = str(path) if path is not None else str(default_db_path())
        # 延迟连接：首次 DAO 访问才真正 open（启动提速，详设 7.2）
        self._opened = False
        self._local = threading.local()
        self._write_lock = threading.Lock()

    @property
    def path(self) -> str:
        return self._path

    # ---- 连接 ----
    def connection(self) -> sqlite3.Connection:
        """当前线程的连接（每线程一个，check_same_thread=False）。"""
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is None:
            try:
                if self._path != ":memory:":
                    # sqlite3.connect 不创建父目录；首启 %LOCALAPPDATA%/YuChongGou
                    # 不存在会报 "unable to open database file"（DB001）
                    Path(self._path).parent.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(self._path, check_same_thread=False)
                conn.row_factory = sqlite3.Row
                if self._path != ":memory:":
                    conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute("PRAGMA busy_timeout=5000")
            except sqlite3.Error as exc:
                raise AppError(ERR_DB_OPEN_FAILED, "数据库打开失败", cause=exc) from exc
            self._local.conn = conn
            self._opened = True
            # 迁移幂等：user_version 达标即跳过；:memory: 每线程独立库各自建表
            self._migrate(conn)
        return conn

    # ---- 迁移 ----
    def _migrate(self, conn: sqlite3.Connection) -> None:
        try:
            row = conn.execute("PRAGMA user_version").fetchone()
            current = int(row[0])
            for version, script in MIGRATIONS:
                if version > current:
                    conn.executescript(script)
                    conn.execute(f"PRAGMA user_version = {version}")
                    conn.commit()
        except (sqlite3.Error, ValueError) as exc:
            raise AppError(ERR_DB_MIGRATION_FAILED, "数据库迁移失败", cause=exc) from exc

    # ---- 事务 ----
    def write(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        """统一写入口：串行锁包裹 BEGIN IMMEDIATE..COMMIT。"""
        with self._write_lock:
            conn = self.connection()
            try:
                conn.execute("BEGIN IMMEDIATE")
                result = fn(conn)
                conn.commit()
                return result
            except sqlite3.IntegrityError as exc:
                conn.rollback()
                from ych.common.errors import ERR_DB_CONSTRAINT

                raise AppError(ERR_DB_CONSTRAINT, "数据约束冲突", cause=exc) from exc
            except sqlite3.Error as exc:
                conn.rollback()
                raise AppError(ERR_DB_IO, "数据库写入失败", cause=exc) from exc
            except BaseException:
                # 非 sqlite 异常（序列化失败/取消等）也必须回滚：
                # 否则事务滞留持锁，其他线程 busy_timeout 耗尽后全部 DB004
                conn.rollback()
                raise

    def query(self, sql: str, params: tuple[object, ...] = ()) -> list[sqlite3.Row]:
        """只读查询（走当前线程连接，不加写锁）。"""
        try:
            cur = self.connection().execute(sql, params)
            return list(cur.fetchall())
        except sqlite3.Error as exc:
            raise AppError(ERR_DB_IO, "数据库读取失败", cause=exc) from exc

    def close(self) -> None:
        """关闭当前线程连接（其余线程连接由其线程自行关闭）。"""
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None




