# DDL 脚本序列（详设 7.3/7.4）：v1 九表 + 索引
# ⚠ 偏差修正（tasks/04-s3-db.md）：status CHECK 一律七态——含 canceled；
#   详设 7.3 漏列 canceled 但 11.2 状态机需要落库该终态，以任务文件为准。
from __future__ import annotations

# schema 版本：PRAGMA user_version = 1（不建版本表，避免双轨）
DDL_V1 = """
CREATE TABLE IF NOT EXISTS search_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword     TEXT NOT NULL,
    platform_ids TEXT NOT NULL DEFAULT '[]',
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_sh_keyword ON search_history(keyword);
CREATE INDEX IF NOT EXISTS idx_sh_created ON search_history(created_at DESC);

CREATE TABLE IF NOT EXISTS download_task (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    video_meta   TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending'
                 CHECK(status IN ('pending','running','success',
                            'failed','skipped','interrupted','canceled')),
    progress     REAL NOT NULL DEFAULT 0,
    dest_path    TEXT,
    resume_state TEXT,
    keyword      TEXT NOT NULL,
    platform_id  TEXT NOT NULL,
    error_code   TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_dt_status ON download_task(status);
CREATE INDEX IF NOT EXISTS idx_dt_kw ON download_task(keyword);

CREATE TABLE IF NOT EXISTS process_task (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    task_type     TEXT NOT NULL CHECK(task_type IN ('preprocess','dedup','compare')),
    src_path      TEXT NOT NULL,
    dst_path      TEXT,
    status        TEXT NOT NULL DEFAULT 'pending'
                  CHECK(status IN ('pending','running','success',
                            'failed','skipped','interrupted','canceled')),
    retry_count   INTEGER NOT NULL DEFAULT 0,
    params        TEXT,
    result_summary TEXT,
    error_code    TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    finished_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_pt_status ON process_task(status);
CREATE INDEX IF NOT EXISTS idx_pt_type_src ON process_task(task_type, src_path);

CREATE TABLE IF NOT EXISTS fail_record (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    file_name   TEXT NOT NULL,
    fail_reason TEXT NOT NULL,
    error_code  TEXT,
    fail_time   TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    task_type   TEXT NOT NULL CHECK(task_type IN ('download','preprocess','dedup','compare')),
    payload     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fr_time ON fail_record(fail_time DESC);

CREATE TABLE IF NOT EXISTS dedup_scheme (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT NOT NULL UNIQUE,
    techniques_config TEXT NOT NULL,
    created_at        TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS compare_report (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    src_path   TEXT NOT NULL,
    report     TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_cr_src ON compare_report(src_path, created_at DESC);

CREATE TABLE IF NOT EXISTS app_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- 相对概要设计的补充表（详设 7.5 偏差说明）
CREATE TABLE IF NOT EXISTS asset_index (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    path       TEXT NOT NULL UNIQUE,
    kind       TEXT NOT NULL CHECK(kind IN ('raw','cleaned','deduped')),
    size_bytes INTEGER,
    duration_s REAL,
    width      INTEGER,
    height     INTEGER,
    mtime      REAL,
    category   TEXT,
    keyword    TEXT,
    date_str   TEXT,
    indexed_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_ai_kind ON asset_index(kind);
CREATE INDEX IF NOT EXISTS idx_ai_cat_kw_date ON asset_index(category, keyword, date_str);

CREATE TABLE IF NOT EXISTS keyword_category (
    keyword    TEXT PRIMARY KEY,
    category   TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
"""

# 迁移脚本序列：后续版本追加，永不修改历史脚本
MIGRATIONS: list[tuple[int, str]] = [(1, DDL_V1)]

