# ConfigService 单元测试（对照 5.4 / tasks/02-s5-base.md）
from __future__ import annotations

import json
import sqlite3

import pytest

from ych.common.errors import ERR_CFG_KEY_MISSING, AppError
from ych.services.s5_base.config_service import ConfigService


class InMemoryDao:
    """模拟 S3 SettingsDao 的最小实现（get/set/all）。"""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self._store.get(key)

    def set(self, key: str, value: str) -> None:
        self._store[key] = value

    def all(self) -> dict[str, str]:
        return dict(self._store)


class SqliteSettingsDao:
    """用临时 sqlite 文件的 app_settings 表模拟真实 DAO。"""

    def __init__(self, path) -> None:
        self._conn = sqlite3.connect(str(path))
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS app_settings"
            " (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        self._conn.commit()

    def get(self, key: str) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM app_settings WHERE key=?", (key,)
        ).fetchone()
        return row[0] if row else None

    def set(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO app_settings(key,value) VALUES(?,?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self._conn.commit()

    def all(self) -> dict[str, str]:
        rows = self._conn.execute("SELECT key,value FROM app_settings").fetchall()
        return {k: v for k, v in rows}


def test_memory_mode_returns_default_for_unset_key() -> None:
    cfg = ConfigService()
    assert cfg.get("download_concurrency") == 3
    assert cfg.get("max_retry") == 2
    assert cfg.get("language") == "zh_CN"


def test_get_unknown_key_without_default_raises_cfg001() -> None:
    cfg = ConfigService()
    with pytest.raises(AppError) as exc:
        cfg.get("no_such_key")
    assert exc.value.code == ERR_CFG_KEY_MISSING


def test_set_get_roundtrip_in_memory_and_signal() -> None:
    cfg = ConfigService()
    seen: list[tuple[str, object]] = []
    cfg.changed.connect(lambda k, v: seen.append((k, v)))
    cfg.set("max_retry", 5)
    assert cfg.get_typed("max_retry", int) == 5
    assert seen == [("max_retry", 5)]


def test_defaults_cover_full_2_4_table() -> None:
    defaults = ConfigService._DEFAULTS
    expected_keys = {
        "download_concurrency", "process_concurrency", "max_retry",
        "retry_backoff_seconds", "compare_candidates_per_platform",
        "candidate_cache_ttl_days", "feature_max_frames",
        "detect_sample_frames", "inpaint_tile_size", "dedup_weights",
        "net_probe_ttl_seconds", "foreign_platforms_enabled",
        "readonly_protect_raw", "language", "workdir",
        "proxy_enabled", "proxy_host", "proxy_port",
        "pexels_api_key", "pixabay_api_key",
    }
    assert expected_keys <= set(defaults)
    assert defaults["download_concurrency"] == 3
    assert defaults["process_concurrency"] == 2
    assert defaults["detect_sample_frames"] == 24


def test_database_ready_backfills_pending_writes(tmp_path) -> None:
    dao = SqliteSettingsDao(tmp_path / "app.db")
    cfg = ConfigService()
    cfg.set("max_retry", 7)          # 内存模式期间写入
    cfg.database_ready(dao)           # 回填落库
    assert json.loads(dao.get("max_retry")) == 7

    # 新实例从库加载
    cfg2 = ConfigService()
    cfg2.database_ready(dao)
    assert cfg2.get("max_retry") == 7


def test_database_ready_corrupt_value_falls_back_to_default(tmp_path) -> None:
    dao = SqliteSettingsDao(tmp_path / "app.db")
    dao.set("broken_key", "{not-json")
    cfg = ConfigService()
    cfg.database_ready(dao)
    # 反序列化失败 → 该键退回默认值（无默认值则 CFG001）
    with pytest.raises(AppError):
        cfg.get("broken_key")


def test_secret_readwrite_with_memory_keyring(memory_keyring) -> None:
    cfg = ConfigService()
    assert cfg.secret_get("pexels") == ""
    cfg.secret_set("pexels", "SECRET-123")
    assert cfg.secret_get("pexels") == "SECRET-123"
    # 已知键名自动置位布尔标记
    assert cfg.get_typed("pexels_api_key", bool) is True
