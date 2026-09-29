# M1 素材采集测试（对照 12.6 / tasks/09-m1-capture.md）
from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest
import requests
import responses
from PySide6.QtCore import QThreadPool

from ych.common.cancellation import CancellationToken
from ych.common.errors import (
    ERR_PLG_KEY_MISSING,
    ERR_PLG_RATE_LIMITED,
    ERR_PLG_SCHEMA_CHANGED,
    ERR_PLG_UNAVAILABLE,
    AppError,
)
from ych.common.schemas import ResumeState, SearchFilters, VideoMeta
from ych.core.m1_capture.download_manager import DownloadManager
from ych.core.m1_capture.history_service import HistoryService
from ych.core.m1_capture.net_checker import ForeignNetChecker, NetStatus
from ych.core.m1_capture.plugin_base import PlatformPlugin
from ych.core.m1_capture.plugin_manager import PluginManager
from ych.core.m1_capture.plugins import _douyin_backend as douyin_backend
from ych.core.m1_capture.plugins import douyin_plugin as douyin_plugin_mod
from ych.core.m1_capture.plugins.douyin_plugin import DouyinPlugin
from ych.core.m1_capture.result_filter import ResultFilter
from ych.core.m1_capture.search_coordinator import SearchCoordinator
from ych.core.m4_scheduler.task_scheduler import TaskScheduler
from ych.core.m5_library.archive_service import ArchiveService
from ych.core.m5_library.workdir_manager import WorkDirManager
from ych.services.s3_db.daos import SettingsDao, make_daos
from ych.services.s3_db.database import Database
from ych.services.s4_net.http_client import HttpClient
from ych.services.s5_base.config_service import ConfigService

FIXTURES = Path(__file__).parents[3] / "fixtures"
TERMINAL = {"success", "failed", "canceled", "skipped", "interrupted"}


def _meta(**kw: object) -> VideoMeta:
    base: dict[str, object] = {
        "plugin_id": "pexels", "video_key": "v1", "duration_s": 10.0,
        "width": 1920, "height": 1080, "watermark_tag": "no",
        "download_url": "https://example/a.mp4",
    }
    base.update(kw)
    return VideoMeta(**base)  # type: ignore[arg-type]


# ================= ResultFilter（表驱动） =================

@pytest.mark.parametrize(
    ("patch", "items", "expect_keys"),
    [
        # 时长闭开区间：恰好等于 max 被剔除
        ({"duration_max_s": 10.0}, [10.0], []),
        ({"duration_max_s": 10.0}, [9.9], ["v1"]),
        ({"duration_min_s": 5.0}, [4.9], []),
        ({"duration_min_s": 5.0}, [5.0], ["v1"]),
        # min_height：0 不限；不足剔除
        ({"min_height": 720}, [_meta(height=480)], []),
        ({"min_height": 720}, [_meta(height=1080)], ["v1"]),
        ({"min_height": 0}, [_meta(height=144)], ["v1"]),
        # 大小上下限（MB）；None 按不限放行
        ({"size_min_mb": 5.0, "size_max_mb": 100.0},
         [_meta(file_size_bytes=1024)], []),
        ({"size_min_mb": 5.0, "size_max_mb": 100.0},
         [_meta(file_size_bytes=10 * 1024 * 1024)], ["v1"]),
        ({"size_min_mb": 5.0, "size_max_mb": 100.0},
         [_meta(file_size_bytes=None)], ["v1"]),
        # 水印三态
        ({"watermark": "yes"}, [_meta(watermark_tag="no")], []),
        ({"watermark": "no"}, [_meta(watermark_tag="yes")], []),
        ({"watermark": "unknown"}, [_meta(watermark_tag="yes"),
                                    _meta(watermark_tag="no")], ["v1", "v1"]),
    ],
)
def test_result_filter_table(patch: dict, items: list, expect_keys: list) -> None:
    f = SearchFilters(**patch)
    metas = []
    for it in items:
        # float 视为 duration_s 覆盖值；VideoMeta 原样使用
        metas.append(_meta(duration_s=it) if isinstance(it, float) else it)
    out = ResultFilter().apply(metas, f)
    assert len(out) == len(expect_keys)


def test_result_filter_sort_height_desc_duration_asc() -> None:
    metas = [
        _meta(video_key="a", height=720, duration_s=30.0),
        _meta(video_key="b", height=1080, duration_s=20.0),
        _meta(video_key="c", height=1080, duration_s=5.0),
        _meta(video_key="d", height=480, duration_s=1.0),
    ]
    out = ResultFilter().apply(metas, SearchFilters())
    assert [m.video_key for m in out] == ["c", "b", "a", "d"]


# ================= 五平台骨架占位（抖音已实装，见下节） =================

@pytest.mark.parametrize(
    "plugin_cls_path",
    [
        "ych.core.m1_capture.plugins.kuaishou_plugin:KuaishouPlugin",
        "ych.core.m1_capture.plugins.bilibili_plugin:BilibiliPlugin",
        "ych.core.m1_capture.plugins.xiaohongshu_plugin:XiaohongshuPlugin",
        "ych.core.m1_capture.plugins.tiktok_plugin:TiktokPlugin",
        "ych.core.m1_capture.plugins.youtube_plugin:YoutubePlugin",
    ],
)
def test_skeleton_plugins_not_implemented(plugin_cls_path: str) -> None:
    mod_name, cls_name = plugin_cls_path.split(":")
    import importlib

    cls = getattr(importlib.import_module(mod_name), cls_name)
    plugin = cls(None, None, None)  # type: ignore[arg-type]
    assert plugin.check_available() == (False, "not_implemented")
    with pytest.raises(AppError) as ei:
        plugin.search("地毯", SearchFilters(), 10, None)
    assert ei.value.code == ERR_PLG_UNAVAILABLE
    assert "暂未开放" in ei.value.message


# ================= 抖音插件（内置下载器子进程） =================

def _douyin_plugin(memory_config: ConfigService) -> DouyinPlugin:
    return DouyinPlugin(None, memory_config)  # type: ignore[arg-type]


def _douyin_vendor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    logged_in: bool = False,
) -> None:
    """伪造 vendor 目录：可用 + 最小配置（登录态由 msToken + 登录态 Cookie 决定）。"""
    monkeypatch.setattr(
        douyin_backend, "resolve_vendor_root", lambda override="": tmp_path,
    )
    mstoken = "x" * 40 if logged_in else "YOUR_MS_TOKEN"
    login_cookie = f"  sessionid: {'y' * 40}\n" if logged_in else ""
    (tmp_path / "config_plugin.yml").write_text(
        "link:\n  - __DOUYIN_HOME_URL__\nnumber:\n  post: 1\n"
        f'video: false\nstart_time: ""\nend_time: ""\n'
        f"cookies:\n  msToken: {mstoken}\n{login_cookie}",
        encoding="utf-8",
    )


def test_douyin_check_available_branches(
    memory_config: ConfigService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _douyin_plugin(memory_config)
    monkeypatch.setattr(
        douyin_backend, "resolve_vendor_root", lambda override="": None,
    )
    assert plugin.check_available() == (False, "PLG010")          # vendor 缺失
    _douyin_vendor(tmp_path, monkeypatch)
    assert plugin.check_available() == (False, ERR_PLG_KEY_MISSING)  # 未登录
    _douyin_vendor(tmp_path, monkeypatch, logged_in=True)
    assert plugin.check_available() == (True, "ok")               # 文件态已登录


def test_douyin_search_rejects_non_home_url(memory_config: ConfigService) -> None:
    plugin = _douyin_plugin(memory_config)
    with pytest.raises(AppError) as ei:
        plugin.search("解压视频", SearchFilters(), 10, None)
    assert "主页链接" in ei.value.message


def test_douyin_search_requires_login(
    memory_config: ConfigService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _douyin_plugin(memory_config)
    _douyin_vendor(tmp_path, monkeypatch, logged_in=False)
    with pytest.raises(AppError) as ei:
        plugin.search("https://www.douyin.com/user/MS4wLjAB123", SearchFilters(), 10, None)
    assert ei.value.code == ERR_PLG_KEY_MISSING


def test_douyin_search_happy_path(
    memory_config: ConfigService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _douyin_plugin(memory_config)
    _douyin_vendor(tmp_path, monkeypatch, logged_in=True)

    def fake_run_cli(
        root: Path, args: list[str], token: object, timeout: float,
    ) -> tuple[int, list[str]]:
        run_dir = Path(args[args.index("-p") + 1])
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "2026-01-01_样例_999_data.json").write_text(json.dumps({
            "aweme_id": "999", "desc": "样例", "create_time": 1767225600,
            "video": {"duration": 15000, "width": 1080, "height": 1920},
        }), encoding="utf-8")
        return 0, ["done"]

    calls = {"n": 0}

    def counting_run_cli(
        root: Path, args: list[str], token: object, timeout: float,
    ) -> tuple[int, list[str]]:
        calls["n"] += 1
        return fake_run_cli(root, args, token, timeout)

    monkeypatch.setattr(douyin_backend, "run_cli", counting_run_cli)
    metas = plugin.search(
        "https://www.douyin.com/user/MS4wLjAB123", SearchFilters(), 10, None,
    )
    assert len(metas) == 1
    assert metas[0].video_key == "999"
    assert metas[0].watermark_tag == "no"
    assert metas[0].extra["home_url"] == "https://www.douyin.com/user/MS4wLjAB123"
    assert calls["n"] == 1   # 首次命中不再重试


def test_douyin_search_empty_retries_then_succeeds(
    memory_config: ConfigService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """间歇性风控：首次拉空退避重试一次；第二窗口产出即成功。"""
    plugin = _douyin_plugin(memory_config)
    _douyin_vendor(tmp_path, monkeypatch, logged_in=True)
    monkeypatch.setattr(douyin_plugin_mod, "_EMPTY_RETRY_BACKOFF_S", 0.01)

    calls = {"n": 0}

    def fake_run_cli(
        root: Path, args: list[str], token: object, timeout: float,
    ) -> tuple[int, list[str]]:
        calls["n"] += 1
        run_dir = Path(args[args.index("-p") + 1])
        run_dir.mkdir(parents=True, exist_ok=True)
        if calls["n"] >= 2:   # 重试窗口才有产出
            (run_dir / "2026-01-01_样例_777_data.json").write_text(
                json.dumps({"aweme_id": "777", "desc": "重试产出"}),
                encoding="utf-8",
            )
        return 0, []

    monkeypatch.setattr(douyin_backend, "run_cli", fake_run_cli)
    metas = plugin.search(
        "https://www.douyin.com/user/MS4wLjAB123", SearchFilters(), 10, None,
    )
    assert calls["n"] == 2
    assert [m.video_key for m in metas] == ["777"]


def test_douyin_search_empty_after_retry_raises(
    memory_config: ConfigService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """重试后仍拉空：给出含"已自动重试"的明确报错，不静默返回空。"""
    plugin = _douyin_plugin(memory_config)
    _douyin_vendor(tmp_path, monkeypatch, logged_in=True)
    monkeypatch.setattr(douyin_plugin_mod, "_EMPTY_RETRY_BACKOFF_S", 0.01)
    monkeypatch.setattr(
        douyin_backend, "run_cli",
        lambda root, args, token, timeout: (0, []),
    )
    with pytest.raises(AppError) as ei:
        plugin.search(
            "https://www.douyin.com/user/MS4wLjAB123", SearchFilters(), 10, None,
        )
    assert "已自动重试" in ei.value.message


def test_douyin_search_timeout_salvages_harvest(
    memory_config: ConfigService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """超时被杀≠全无产出：已落盘的作品元数据必须回收为部分成功。"""
    plugin = _douyin_plugin(memory_config)
    _douyin_vendor(tmp_path, monkeypatch, logged_in=True)

    def fake_run_cli(
        root: Path, args: list[str], token: object, timeout: float,
    ) -> tuple[int, list[str]]:
        run_dir = Path(args[args.index("-p") + 1])
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "2026-01-01_样例_888_data.json").write_text(
            json.dumps({"aweme_id": "888", "desc": "超时前已抓到"}),
            encoding="utf-8",
        )
        raise AppError("PLG010", "抖音下载器执行超时，已终止")

    monkeypatch.setattr(douyin_backend, "run_cli", fake_run_cli)
    metas = plugin.search(
        "https://www.douyin.com/user/MS4wLjAB123", SearchFilters(), 10, None,
    )
    assert [m.video_key for m in metas] == ["888"]


def test_douyin_search_cancel_propagates(
    memory_config: ConfigService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """用户取消必须原样上抛（回收逻辑不得吞掉取消信号）。"""
    from ych.common.cancellation import TaskCanceled

    plugin = _douyin_plugin(memory_config)
    _douyin_vendor(tmp_path, monkeypatch, logged_in=True)

    def fake_run_cli(
        root: Path, args: list[str], token: object, timeout: float,
    ) -> tuple[int, list[str]]:
        raise TaskCanceled("抖音采集已取消")

    monkeypatch.setattr(douyin_backend, "run_cli", fake_run_cli)
    with pytest.raises(TaskCanceled):
        plugin.search(
            "https://www.douyin.com/user/MS4wLjAB123", SearchFilters(), 10, None,
        )


def test_douyin_download_empty_then_retry_succeeds(
    memory_config: ConfigService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """下载与搜索同源的风控拉空：退出码 0 但无产出时退避重试一次。"""
    plugin = _douyin_plugin(memory_config)
    _douyin_vendor(tmp_path, monkeypatch, logged_in=True)
    monkeypatch.setattr(douyin_plugin_mod, "_EMPTY_RETRY_BACKOFF_S", 0.01)

    calls = {"n": 0}

    def fake_run_cli(
        root: Path, args: list[str], token: object, timeout: float,
    ) -> tuple[int, list[str]]:
        calls["n"] += 1
        if calls["n"] >= 2:   # 重试窗口才产出媒体
            media = Path(args[args.index("-p") + 1]) / "x_999.mp4"
            media.parent.mkdir(parents=True, exist_ok=True)
            media.write_bytes(b"mp4-bytes")
        return 0, ["no media"]

    monkeypatch.setattr(douyin_backend, "run_cli", fake_run_cli)
    state = plugin.download(
        _meta(plugin_id="douyin", video_key="999", extra={
            "home_url": "https://www.douyin.com/user/x", "date": "2026-01-01",
        }),
        tmp_path / "dest_base", None, None, None,
    )
    assert calls["n"] == 2
    assert Path(state.temp_path).read_bytes() == b"mp4-bytes"


# ================= 抖音风控节流（方案一） =================

def test_douyin_risk_cooldown_blocks_search_without_cli(
    memory_config: ConfigService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """冷却期内搜索：快速给出冷却提示，不发起 CLI（不撞平台）。"""
    import time as _time

    plugin = _douyin_plugin(memory_config)
    _douyin_vendor(tmp_path, monkeypatch, logged_in=True)
    memory_config.set("douyin_cooldown_until", _time.time() + 600)

    def _boom(*a: object, **k: object) -> tuple[int, list[str]]:
        raise AssertionError("冷却期内不得发起 CLI")

    monkeypatch.setattr(douyin_backend, "run_cli", _boom)
    with pytest.raises(AppError) as ei:
        plugin.search(
            "https://www.douyin.com/user/MS4wLjAB123", SearchFilters(), 10, None,
        )
    assert "冷却" in ei.value.message
    assert memory_config.get("douyin_last_search_ts") == 0.0   # 未消耗预算


def test_douyin_risk_rejection_sets_cooldown_and_skips_retry(
    memory_config: ConfigService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """确定性拒绝：立即进冷却且不再重试（重试只会延长封锁）。"""
    import time as _time

    plugin = _douyin_plugin(memory_config)
    _douyin_vendor(tmp_path, monkeypatch, logged_in=True)

    calls = {"n": 0}

    def fake_run_cli(
        root: Path, args: list[str], token: object, timeout: float,
    ) -> tuple[int, list[str]]:
        calls["n"] += 1
        return 0, ["作品列表 第 1 页被抖音拒绝：抖音安全校验只放行网页内发起的请求"]

    monkeypatch.setattr(douyin_backend, "run_cli", fake_run_cli)
    with pytest.raises(AppError) as ei:
        plugin.search(
            "https://www.douyin.com/user/MS4wLjAB123", SearchFilters(), 10, None,
        )
    assert calls["n"] == 1                       # 未进入空产出重试
    assert "安全校验拒绝" in ei.value.message
    assert memory_config.get("douyin_reject_strikes") == 1
    until = float(memory_config.get("douyin_cooldown_until") or 0)
    assert until > _time.time()                  # 冷却已生效


def test_douyin_risk_interval_and_budget(
    memory_config: ConfigService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """最小搜索间隔与每日预算：超限快速提示；跨天预算自动归零。"""
    import time as _time

    plugin = _douyin_plugin(memory_config)
    _douyin_vendor(tmp_path, monkeypatch, logged_in=True)
    monkeypatch.setattr(
        douyin_backend, "run_cli",
        lambda root, args, token, timeout: (0, []),
    )
    home = "https://www.douyin.com/user/MS4wLjAB123"

    # 间隔：发起一次后立刻再搜 → 间隔拦截
    with pytest.raises(AppError, match="未获取到作品"):   # 第一次放行（内部重试耗尽）
        plugin.search(home, SearchFilters(), 10, None)
    with pytest.raises(AppError) as ei:
        plugin.search(home, SearchFilters(), 10, None)
    assert "距离上次搜索" in ei.value.message

    # 预算：今天已用满 → 拦截；日期回退到昨天 → 自动归零放行
    memory_config.set(
        "douyin_daily_usage",
        {"date": _time.strftime("%Y-%m-%d"), "used": 20},
    )
    memory_config.set("douyin_last_search_ts", 0)   # 解除间隔拦截
    with pytest.raises(AppError) as ei:
        plugin.search(home, SearchFilters(), 10, None)
    assert "预算" in ei.value.message
    memory_config.set(
        "douyin_daily_usage", {"date": "2000-01-01", "used": 20},
    )
    with pytest.raises(AppError, match="未获取到作品"):   # 跨天放行
        plugin.search(home, SearchFilters(), 10, None)


def test_douyin_risk_ladder_and_success_reset(
    memory_config: ConfigService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """连续拒绝冷却阶梯放大；采集成功后拒绝计数清零。"""
    plugin = _douyin_plugin(memory_config)
    _douyin_vendor(tmp_path, monkeypatch, logged_in=True)
    risk = plugin._risk
    risk._clock = lambda: 1_000_000.0   # 固定时钟便于断言

    risk.note_rejection()
    first = risk.cooldown_remaining()
    risk.note_rejection()
    second = risk.cooldown_remaining()
    assert second > first               # 阶梯放大

    def fake_run_cli(
        root: Path, args: list[str], token: object, timeout: float,
    ) -> tuple[int, list[str]]:
        run_dir = Path(args[args.index("-p") + 1])
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "x_777_data.json").write_text(
            json.dumps({"aweme_id": "777"}), encoding="utf-8",
        )
        return 0, []

    monkeypatch.setattr(douyin_backend, "run_cli", fake_run_cli)
    memory_config.set("douyin_cooldown_until", 0)   # 清冷却：验证成功路径本身
    metas = plugin.search(
        "https://www.douyin.com/user/MS4wLjAB123", SearchFilters(), 10, None,
    )
    assert [m.video_key for m in metas] == ["777"]
    assert memory_config.get("douyin_reject_strikes") == 0   # 成功清零


def test_douyin_download_cooldown_fails_fast(
    memory_config: ConfigService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """冷却期内的下载任务：快速失败带明确原因，不发起 CLI。"""
    import time as _time

    plugin = _douyin_plugin(memory_config)
    _douyin_vendor(tmp_path, monkeypatch, logged_in=True)
    memory_config.set("douyin_cooldown_until", _time.time() + 600)

    def _boom(*a: object, **k: object) -> tuple[int, list[str]]:
        raise AssertionError("冷却期内不得发起 CLI")

    monkeypatch.setattr(douyin_backend, "run_cli", _boom)
    with pytest.raises(AppError) as ei:
        plugin.download(
            _meta(plugin_id="douyin", video_key="999", extra={
                "home_url": "https://www.douyin.com/user/x", "date": "2026-01-01",
            }),
            tmp_path / "dest", None, None, None,
        )
    assert "冷却" in ei.value.message


def test_douyin_download_happy_path(
    memory_config: ConfigService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _douyin_plugin(memory_config)
    _douyin_vendor(tmp_path, monkeypatch, logged_in=True)
    media = tmp_path / "_downloads" / "2026-01-01_样例_999.mp4"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b"mp4-bytes")
    monkeypatch.setattr(
        douyin_backend, "run_cli",
        lambda root, args, token, timeout: (0, ["done"]),
    )
    state = plugin.download(
        _meta(plugin_id="douyin", video_key="999", extra={
            "home_url": "https://www.douyin.com/user/x", "date": "2026-01-01",
        }),
        tmp_path / "dest_base", None, None, None,
    )
    assert Path(state.temp_path).read_bytes() == b"mp4-bytes"
    assert state.total_bytes == len(b"mp4-bytes")


def test_douyin_download_missing_media_raises(
    memory_config: ConfigService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _douyin_plugin(memory_config)
    _douyin_vendor(tmp_path, monkeypatch, logged_in=True)
    monkeypatch.setattr(
        douyin_backend, "run_cli",
        lambda root, args, token, timeout: (0, ["no media"]),
    )
    with pytest.raises(AppError) as ei:
        plugin.download(
            _meta(plugin_id="douyin", video_key="404", extra={
                "home_url": "https://www.douyin.com/user/x", "date": "2026-01-01",
            }),
            tmp_path / "dest", None, None, None,
        )
    assert "可能被风控拦截" in ei.value.message


def test_douyin_build_run_config_replacements() -> None:
    text = douyin_backend.build_run_config(
        "link:\n  - __DOUYIN_HOME_URL__\nnumber:\n  post: 1\n"
        'start_time: ""\nend_time: ""\n',
        "https://www.douyin.com/user/abc", 0,
        start_date="2026-01-01", end_date="2026-01-01",
    )
    assert "https://www.douyin.com/user/abc" in text
    assert "__DOUYIN_HOME_URL__" not in text
    assert "  post: 0" in text
    assert 'start_time: "2026-01-01"' in text
    assert 'end_time: "2026-01-01"' in text


def test_douyin_build_run_config_single_quoted_dates() -> None:
    """CLI 重写后的运行态配置用单引号空日期：日期窗口仍必须写入。

    只认双引号会让窗口静默失效，number.post=0 时全量下载整个主页。
    """
    text = douyin_backend.build_run_config(
        "link:\n  - __DOUYIN_HOME_URL__\n"
        "start_time: ''\nend_time: ''\n",
        "https://www.douyin.com/user/abc", 0,
        start_date="2026-08-27", end_date="2026-08-27",
    )
    assert 'start_time: "2026-08-27"' in text
    assert 'end_time: "2026-08-27"' in text


def test_douyin_build_run_config_link_forced_replace() -> None:
    """link 注入不依赖占位符：CLI/工具重写后的真实链接、无缩进、多条目
    残留都必须被整体替换为目标主页（否则静默采错博主）。"""
    text = douyin_backend.build_run_config(
        "link:\n"
        "- https://www.douyin.com/user/old_one\n"
        "- https://www.douyin.com/user/old_two\n"
        "start_time: ''\n",
        "https://www.douyin.com/user/new_target", 30,
    )
    assert "- https://www.douyin.com/user/new_target" in text
    assert "old_one" not in text and "old_two" not in text
    assert "__DOUYIN_HOME_URL__" not in text


def test_douyin_build_run_config_missing_link_section_raises() -> None:
    """模板缺 link 段（结构损坏）必须报错，禁止静默产出跑偏的运行配置。"""
    with pytest.raises(AppError) as ei:
        douyin_backend.build_run_config(
            "number:\n  post: 5\n",
            "https://www.douyin.com/user/abc", 5,
        )
    assert "主页链接写入失败" in ei.value.message


def test_douyin_build_run_config_video_flip_failure_raises() -> None:
    """模板 video 键缺失/变形时下载阶段必须报错（否则永远下载不出文件）。"""
    with pytest.raises(AppError) as ei:
        douyin_backend.build_run_config(
            "link:\n  - __DOUYIN_HOME_URL__\nvideo: auto\n",
            "https://www.douyin.com/user/abc", 1,
            download_media=True,
        )
    assert "video" in ei.value.message


def test_douyin_build_run_config_overrides_scroll_tuning() -> None:
    """运行态配置残留旧滚动参数（240/8）时按代码调优值覆盖（60/4）。"""
    text = douyin_backend.build_run_config(
        "link:\n  - __DOUYIN_HOME_URL__\n"
        "scroll:\n  max_scrolls: 240\n  idle_rounds: 8\n",
        "https://www.douyin.com/user/abc", 5,
    )
    assert "  max_scrolls: 60" in text
    assert "  idle_rounds: 4" in text


def test_douyin_run_cli_forces_utf8_stdio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """中文 Windows 管道默认 GBK，CLI 的 ℹ️ 日志会让子进程崩溃：
    必须强制子进程 UTF-8 标准流，父进程按 UTF-8 解码。"""
    import io as _io

    captured: dict[str, object] = {}

    class _FakeProc:
        def __init__(self, *_args: object, **kwargs: object) -> None:
            captured.update(kwargs)
            self.stdout = _io.StringIO("")
            self.pid = 4242
            self.returncode = 0

        def poll(self) -> int:
            return 0

    monkeypatch.setattr(douyin_backend.subprocess, "Popen", _FakeProc)
    code, _tail = douyin_backend.run_cli(tmp_path, ["-c", "x"], None, 1.0)
    assert code == 0
    assert captured["env"]["PYTHONIOENCODING"] == "utf-8"
    assert captured["encoding"] == "utf-8"
    assert captured["errors"] == "replace"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://www.douyin.com/user/MS4wLjAB123",
         "https://www.douyin.com/user/MS4wLjAB123"),
        ("素材 https://www.douyin.com/user/abc123 搬运",
         "https://www.douyin.com/user/abc123"),
        ("解压视频", None),
        ("https://www.douyin.com/video/123", None),
    ],
)
def test_douyin_extract_home_url(raw: str, expected: str | None) -> None:
    assert douyin_backend.extract_home_url(raw) == expected


def test_douyin_meta_from_aweme_drops_missing_id() -> None:
    assert douyin_backend.meta_from_aweme({"desc": "无id"}, "u") is None


def test_douyin_read_self_sec_uid(tmp_path: Path) -> None:
    (tmp_path / "self_user.txt").write_text(
        "https://www.douyin.com/user/MS4wLjABSELF?a=x", encoding="utf-8",
    )
    assert douyin_backend.read_self_sec_uid(tmp_path) == "MS4wLjABSELF"
    assert douyin_backend.read_self_sec_uid(tmp_path / "none") == ""


def test_douyin_refresh_nickname_sets_config(
    memory_config: ConfigService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _douyin_plugin(memory_config)
    _douyin_vendor(tmp_path, monkeypatch, logged_in=True)
    (tmp_path / "self_user.txt").write_text(
        "https://www.douyin.com/user/MS4wLjABSELF", encoding="utf-8",
    )

    def fake_run_cli(
        root: Path, args: list[str], token: object, timeout: float,
    ) -> tuple[int, list[str]]:
        run_dir = Path(args[args.index("-p") + 1])
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "2026-01-01_自己_1_data.json").write_text(json.dumps({
            "aweme_id": "1", "author": {"nickname": "解压小达人"},
        }), encoding="utf-8")
        return 0, ["done"]

    monkeypatch.setattr(douyin_backend, "run_cli", fake_run_cli)
    assert plugin.refresh_nickname() == "解压小达人"
    assert memory_config.get("douyin_nickname") == "解压小达人"


def test_douyin_refresh_nickname_degrades_without_self_file(
    memory_config: ConfigService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _douyin_plugin(memory_config)
    _douyin_vendor(tmp_path, monkeypatch, logged_in=True)
    monkeypatch.setattr(
        douyin_backend, "run_cli",
        lambda root, args, token, timeout: (_ for _ in ()).throw(
            AssertionError("不应触发子进程"),
        ),
    )
    assert plugin.refresh_nickname() == ""


def test_douyin_has_real_cookies_requires_login_cookie(tmp_path: Path) -> None:
    """匿名会话（只有 msToken/ttwid）不得误判为已登录。"""
    anon = (
        'start_time: ""\nend_time: ""\ncookies:\n'
        f"  msToken: {'x' * 40}\n  ttwid: 1%7Cabc\n"
    )
    (tmp_path / "config_plugin.yml").write_text(anon, encoding="utf-8")
    assert douyin_backend.has_real_cookies(tmp_path) is False
    (tmp_path / "config_plugin.yml").write_text(
        anon + f"  sessionid: {'y' * 40}\n", encoding="utf-8",
    )
    assert douyin_backend.has_real_cookies(tmp_path) is True


def test_douyin_refresh_nickname_rolls_back_stale_flag(
    memory_config: ConfigService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """文件态 Cookie 被清理后，启动同步须回滚残留的已登录标记与昵称。"""
    memory_config.set("douyin_cookies_set", True)
    memory_config.set("douyin_nickname", "旧昵称")
    plugin = _douyin_plugin(memory_config)
    _douyin_vendor(tmp_path, monkeypatch, logged_in=False)
    assert plugin.refresh_nickname() == ""
    assert memory_config.get("douyin_cookies_set") is False
    assert memory_config.get("douyin_nickname") == ""


def test_douyin_login_reports_without_vendor(
    memory_config: ConfigService, monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _douyin_plugin(memory_config)
    monkeypatch.setattr(
        douyin_backend, "resolve_vendor_root", lambda override="": None,
    )
    results: list[tuple[bool, str]] = []
    plugin.start_cookie_login(lambda ok, reason: results.append((ok, reason)))
    assert len(results) == 1
    ok, reason = results[0]
    assert ok is False
    assert "未找到内置下载器" in reason


# ================= PluginManager =================

class CountingPlugin(PlatformPlugin):
    id = "counting"
    display_name = "计数"
    region = "global"

    def __init__(self, http: object, config: ConfigService,
                 limiter: object = None) -> None:
        super().__init__(http, config, limiter)  # type: ignore[arg-type]
        self.checks = 0

    def check_available(self) -> tuple[bool, str]:
        self.checks += 1
        return (True, "ok")

    def search(self, keyword: str, filters: SearchFilters, max_count: int,
               token: CancellationToken | None) -> list[VideoMeta]:
        return []


@pytest.fixture
def pm_env(memory_config: ConfigService) -> HttpClient:
    return HttpClient(memory_config)


def test_discover_finds_all_eight(pm_env: HttpClient, memory_config: ConfigService) -> None:
    manager = PluginManager(pm_env, memory_config)
    manager.discover()
    ids = {p.id for p in manager.all()}
    assert ids == {"douyin", "kuaishou", "bilibili", "xiaohongshu",
                   "tiktok", "youtube", "pexels", "pixabay"}
    regions = manager.by_region()
    assert {p.id for p in regions["cn"]} == {
        "douyin", "kuaishou", "bilibili", "xiaohongshu"}


def test_enabled_switch_and_overrides(pm_env: HttpClient, memory_config: ConfigService) -> None:
    manager = PluginManager(pm_env, memory_config)
    manager.discover()
    # 国内默认全开；素材站不受总开关约束，TikTok/YouTube 受约束
    assert {p.id for p in manager.enabled("cn")} == {
        "douyin", "kuaishou", "bilibili", "xiaohongshu"}
    assert {p.id for p in manager.enabled("global")} == {"pexels", "pixabay"}
    memory_config.set("foreign_platforms_enabled", True)
    assert {p.id for p in manager.enabled("global")} == {"pexels", "pixabay"}
    # 插件级覆盖：关掉 pexels；总开关开着时 tiktok 可被强开
    memory_config.set("enabled_plugins", {"pexels": False, "tiktok": True})
    assert {p.id for p in manager.enabled("global")} == {"pixabay", "tiktok"}
    # 总开关关闭后素材站仍在，受门控插件即使强开也不越权
    memory_config.set("foreign_platforms_enabled", False)
    assert {p.id for p in manager.enabled("global")} == {"pixabay"}


def test_availability_ttl_cache(pm_env: HttpClient, tmp_path: Path) -> None:
    cfg = ConfigService()
    plugin = CountingPlugin(pm_env, cfg)
    clock_now = [0.0]

    def clock() -> float:
        return clock_now[0]

    manager = PluginManager(pm_env, cfg, ttl_seconds=50.0, clock=clock)  # type: ignore[arg-type]
    assert manager.availability(plugin) == (True, "ok")
    assert manager.availability(plugin) == (True, "ok")
    assert plugin.checks == 1                      # TTL 内走缓存
    clock_now[0] += 60.0
    assert manager.availability(plugin) == (True, "ok")
    assert plugin.checks == 2                      # 过期重查
    assert manager.availability(plugin, force=True) == (True, "ok")
    assert plugin.checks == 3                      # force 直查


# ================= Pexels 插件（responses 四分支） =================

_PEXELS_URL = "https://api.pexels.com/videos/search"


def _pexels_plugin(cfg: ConfigService) -> PlatformPlugin:
    from ych.core.m1_capture.plugins.pexels_plugin import PexelsPlugin

    return PexelsPlugin(HttpClient(cfg), cfg, None)


@pytest.fixture
def keyed_cfg(memory_keyring, memory_config: ConfigService) -> ConfigService:
    memory_config.secret_set("pexels", "KEY-PEXELS")
    return memory_config


@responses.activate
def test_pexels_search_maps_fields(keyed_cfg: ConfigService) -> None:
    responses.add(responses.GET, _PEXELS_URL,
                  json=json.loads((FIXTURES / "pexels_search.json").read_text("utf-8")),
                  status=200)
    plugin = _pexels_plugin(keyed_cfg)
    items = plugin.search("地毯清洗", SearchFilters(), 30, None)
    assert len(items) == 2
    first = next(m for m in items if m.video_key == "101")
    assert first.plugin_id == "pexels"
    assert first.page_url.endswith("/video/101/")
    assert first.duration_s == 25.0
    assert (first.width, first.height) == (1920, 1080)   # 顶层最高规格
    assert first.thumbnail_url.startswith("https://images.pexels.com")
    assert first.watermark_tag == "no"
    # 默认选型：高度满足下的最小体积 → 720p（有 file_size）
    assert first.download_url.endswith("101_720.mp4")
    assert first.file_size_bytes == 1048576
    req = responses.calls[0].request
    assert req.headers["Authorization"] == "KEY-PEXELS"
    assert "per_page=30" in req.url


@responses.activate
def test_pexels_search_min_height_selection(keyed_cfg: ConfigService) -> None:
    responses.add(responses.GET, _PEXELS_URL,
                  json=json.loads((FIXTURES / "pexels_search.json").read_text("utf-8")),
                  status=200)
    plugin = _pexels_plugin(keyed_cfg)
    items = plugin.search("k", SearchFilters(min_height=1080), 30, None)
    v101 = next(m for m in items if m.video_key == "101")
    assert v101.download_url.endswith("101_1080.mp4")
    # min_height 无法满足时该条被本地过滤
    items2 = plugin.search("k", SearchFilters(min_height=4000), 30, None)
    assert items2 == []
    # per_page 上限 80
    plugin.search("k", SearchFilters(), 500, None)
    assert "per_page=80" in responses.calls[-1].request.url


@responses.activate
def test_pexels_401_invalid_key(keyed_cfg: ConfigService) -> None:
    responses.add(responses.GET, _PEXELS_URL, json={}, status=401)
    plugin = _pexels_plugin(keyed_cfg)
    ok, reason = plugin.check_available()
    assert ok is False and reason == "PLG002"
    with pytest.raises(AppError) as ei:
        plugin.search("k", SearchFilters(), 10, None)
    assert ei.value.code == "PLG002"


@responses.activate
def test_pexels_403_also_invalid(keyed_cfg: ConfigService) -> None:
    responses.add(responses.GET, _PEXELS_URL, json={}, status=403)
    plugin = _pexels_plugin(keyed_cfg)
    assert plugin.check_available() == (False, "PLG002")


@responses.activate
def test_pexels_429_rate_limited(keyed_cfg: ConfigService) -> None:
    responses.add(responses.GET, _PEXELS_URL, json={}, status=429)
    plugin = _pexels_plugin(keyed_cfg)
    with pytest.raises(AppError) as ei:
        plugin.search("k", SearchFilters(), 10, None)
    assert ei.value.code == ERR_PLG_RATE_LIMITED


@responses.activate
def test_pexels_timeout_plg010(keyed_cfg: ConfigService) -> None:
    responses.add(responses.GET, _PEXELS_URL, body=requests.exceptions.ConnectTimeout())
    plugin = _pexels_plugin(keyed_cfg)
    ok, reason = plugin.check_available()
    assert ok is False and reason == ERR_PLG_UNAVAILABLE
    with pytest.raises(AppError) as ei:
        plugin.search("k", SearchFilters(), 10, None)
    assert ei.value.code == ERR_PLG_UNAVAILABLE


@responses.activate
def test_pexels_missing_key(keyed_cfg: ConfigService, memory_keyring) -> None:
    memory_keyring._store.clear()
    plugin = _pexels_plugin(keyed_cfg)
    ok, reason = plugin.check_available()
    assert ok is False and reason == ERR_PLG_KEY_MISSING
    with pytest.raises(AppError) as ei:
        plugin.search("k", SearchFilters(), 10, None)
    assert ei.value.code == ERR_PLG_KEY_MISSING


# ================= Pixabay 插件 =================

_PIXABAY_URL = "https://pixabay.com/api/videos/"


def _pixabay_plugin(cfg: ConfigService) -> PlatformPlugin:
    from ych.core.m1_capture.plugins.pixabay_plugin import PixabayPlugin

    return PixabayPlugin(HttpClient(cfg), cfg, None)


@pytest.fixture
def pixabay_cfg(memory_keyring, memory_config: ConfigService) -> ConfigService:
    memory_config.secret_set("pixabay", "KEY-PX")
    return memory_config


@responses.activate
def test_pixabay_search_maps_fields(pixabay_cfg: ConfigService) -> None:
    responses.add(responses.GET, _PIXABAY_URL,
                  json=json.loads((FIXTURES / "pixabay_search.json").read_text("utf-8")),
                  status=200)
    plugin = _pixabay_plugin(pixabay_cfg)
    items = plugin.search("地毯清洗", SearchFilters(), 30, None)
    assert len(items) == 2
    hit1 = next(m for m in items if m.video_key == "9001")
    # tiny 的 gif 已被扩展名过滤 → 最小体积命中 small
    assert hit1.download_url.endswith("9001_small.mp4")
    assert (hit1.width, hit1.height) == (960, 540)
    assert hit1.file_size_bytes == 1048576
    assert hit1.duration_s == 18.0
    assert hit1.watermark_tag == "no"
    assert hit1.thumbnail_url == "https://i.vimeocdn.com/video/77777_640.jpg"
    assert hit1.extra["views"] == 1234
    hit2 = next(m for m in items if m.video_key == "9002")
    assert hit2.thumbnail_url == ""          # picture_id 缺失
    assert hit2.download_url.endswith("9002_medium.mp4")


@responses.activate
def test_pixabay_min_height(pixabay_cfg: ConfigService) -> None:
    responses.add(responses.GET, _PIXABAY_URL,
                  json=json.loads((FIXTURES / "pixabay_search.json").read_text("utf-8")),
                  status=200)
    plugin = _pixabay_plugin(pixabay_cfg)
    items = plugin.search("k", SearchFilters(min_height=720), 30, None)
    hit1 = next(m for m in items if m.video_key == "9001")
    assert hit1.download_url.endswith("9001_medium.mp4")


@responses.activate
def test_pixabay_availability_branches(pixabay_cfg: ConfigService) -> None:
    plugin = _pixabay_plugin(pixabay_cfg)
    responses.add(responses.GET, _PIXABAY_URL, json={"hits": []}, status=200)
    assert plugin.check_available() == (True, "ok")
    responses.reset()
    responses.add(responses.GET, _PIXABAY_URL,
                  body='{"error": "invalid key"}', status=400)
    ok, reason = plugin.check_available()
    assert ok is False and reason == "PLG002"


# ================= SearchCoordinator（Fake 注入） =================

class FakeSearchPlugin:
    def __init__(self, pid: str, region: str, metas: list[VideoMeta] | None = None,
                 exc: Exception | None = None) -> None:
        self.id = pid
        self.region = region
        self.metas = metas or []
        self.exc = exc
        self.search_calls = 0

    def check_available(self) -> tuple[bool, str]:
        return (True, "ok")

    def search(self, keyword: str, filters: SearchFilters, max_count: int,
               token: CancellationToken | None) -> list[VideoMeta]:
        self.search_calls += 1
        if self.exc is not None:
            raise self.exc
        return list(self.metas)


class FakeManager:
    def __init__(self, plugins: list[FakeSearchPlugin],
                 avail: dict[str, tuple[bool, str]] | None = None) -> None:
        self._plugins = plugins
        self.avail = avail or {}

    def enabled(self, region: str) -> list[FakeSearchPlugin]:
        return [p for p in self._plugins if p.region == region]

    def availability(self, plugin: FakeSearchPlugin,
                     force: bool = False) -> tuple[bool, str]:
        return self.avail.get(plugin.id, (True, "ok"))


def _coord(qtbot, tmp_path: Path, manager: FakeManager) -> tuple[
        SearchCoordinator, HistoryService, Database]:
    cfg = ConfigService()
    db = Database(tmp_path / "app.db")
    daos = make_daos(db)
    history = HistoryService(daos.history)
    coord = SearchCoordinator(manager, history, cfg)  # type: ignore[arg-type]
    return coord, history, db


def test_coordinator_isolation_and_merge(qtbot, tmp_path: Path) -> None:
    good = FakeSearchPlugin("good", "cn", metas=[
        _meta(video_key="a", height=720, duration_s=8.0),
        _meta(video_key="b", height=1080, duration_s=25.0),
        _meta(video_key="c", height=480, duration_s=5.0),
    ])
    bad = FakeSearchPlugin("bad", "cn",
                           exc=AppError(ERR_PLG_SCHEMA_CHANGED, "结构变更"))
    down = FakeSearchPlugin("down", "global")
    manager = FakeManager([good, bad, down],
                          avail={"down": (False, "PLG010")})
    coord, history, _db = _coord(qtbot, tmp_path, manager)

    with qtbot.waitSignal(coord.search_finished, timeout=8000) as blocker:
        coord.search_multi(["地毯"], SearchFilters(duration_max_s=20.0))
    result = blocker.args[0]
    assert result.keyword == "地毯"
    # bad 抛错隔离进 unavailable（码+明细一起给 UI）；down 可用性预检失败同样入列
    assert ("bad", f"{ERR_PLG_SCHEMA_CHANGED} 结构变更") in result.unavailable_platforms
    assert ("down", "PLG010") in result.unavailable_platforms
    assert good.search_calls == 1
    assert down.search_calls == 0
    # 筛选 + 排序生效：25s 被时长上限剔除，高度降序
    assert [m.video_key for m in result.items] == ["a", "c"]
    # 历史只记实际执行搜索的平台
    kws = history.suggestions()
    assert kws == ["地毯"]
    coord._worker.join(3000)


def test_history_suggestions_prefix(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.db")
    daos = make_daos(db)
    hs = HistoryService(daos.history)
    hs.record("地毯清洗", ["douyin"])
    hs.record("地毯保养", ["pexels"])
    hs.record("水管疏通", ["bilibili"])
    assert hs.suggestions(limit=2)[0] == "水管疏通"
    assert set(hs.suggestions(prefix="地毯")) == {"地毯清洗", "地毯保养"}
    # 前缀过滤保持最近使用优先：保养晚于清洗写入
    assert hs.suggestions(prefix="地毯", limit=1) == ["地毯保养"]


# ================= DownloadManager（limit/闸门/续传） =================

class FakeDownloadPlugin:
    """模拟 S4 语义的插件下载：写入 dest_part+".part"，续传按 resume.temp_path。"""

    def __init__(self, body: bytes, delay: float = 0.0) -> None:
        self.id = "fake"
        self.body = body
        self.delay = delay
        self.seen_resume: list[int] = []
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0

    def download(self, meta: VideoMeta, dest_part: Path, on_progress,
                 resume: ResumeState | None, token: CancellationToken | None,
                 on_state=None):
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            start = resume.downloaded_bytes if resume else 0
            self.seen_resume.append(start)
            temp = (Path(resume.temp_path) if resume and resume.temp_path
                    else Path(str(dest_part) + ".part"))
            temp.parent.mkdir(parents=True, exist_ok=True)
            time.sleep(self.delay)
            mode = "ab" if start > 0 and temp.exists() else "wb"
            with open(temp, mode) as f:
                f.write(self.body[start:])
                f.flush()
            return ResumeState(downloaded_bytes=len(self.body),
                               total_bytes=len(self.body),
                               temp_path=str(temp))
        finally:
            with self.lock:
                self.active -= 1


class FakePM:
    def __init__(self, plugin: FakeDownloadPlugin | None) -> None:
        self._plugin = plugin

    def get(self, plugin_id: str) -> FakeDownloadPlugin | None:
        if self._plugin is not None and self._plugin.id == plugin_id:
            return self._plugin
        return None


@pytest.fixture
def dl_env(qtbot, tmp_path: Path):
    cfg = ConfigService()
    cfg.set("download_concurrency", 2)
    wd_root = tmp_path / "wd"
    wd_root.mkdir()
    cfg.set("workdir", str(wd_root))
    wd = WorkDirManager(cfg)
    wd.set_workdir(wd_root)
    db = Database(tmp_path / "app.db")
    daos = make_daos(db)
    archive = ArchiveService(wd, cfg, daos.assets, daos.categories)
    pool = QThreadPool()
    pool.setMaxThreadCount(8)
    sched = TaskScheduler(pool, cfg, daos)
    plugin = FakeDownloadPlugin(b"\xab" * 100)
    dm = DownloadManager(sched, FakePM(plugin), archive, wd, daos, cfg)  # type: ignore[arg-type]
    yield {
        "cfg": cfg, "wd": wd, "daos": daos, "sched": sched,
        "dm": dm, "plugin": plugin, "qtbot": qtbot, "tmp": tmp_path,
    }
    pool.clear()
    pool.waitForDone(3000)


def _wait_terminal(env: dict, timeout_s: float = 15.0) -> list[str]:
    """轮询等待全部任务到终态；同时要求下载行离开活动态。

    竞态加固（与 m4 崩溃恢复用例同口径）：仅看内存任务态可能在终态落库
    完成前提前返回，导致后续 DAO 断言读到 running/pending 旧值。
    """
    qtbot_wait: Callable[[int], None] = env["qtbot"].wait
    daos = env["daos"]
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        states = [t.state for t in env["sched"]._tasks.values()]
        active_rows = (
            len(daos.downloads.list_by_status("pending"))
            + len(daos.downloads.list_by_status("running"))
        )
        if states and all(s in TERMINAL for s in states) and active_rows == 0:
            return states
        qtbot_wait(30)
    return [t.state for t in env["sched"]._tasks.values()]


def test_enqueue_limit_truncates_and_dl010_hint(dl_env: dict, qtbot) -> None:
    dm: DownloadManager = dl_env["dm"]
    daos = dl_env["daos"]
    metas = [_meta(plugin_id="fake", video_key=f"m{i}",
                   download_url=f"https://x/{i}.mp4") for i in range(5)]
    with qtbot.waitSignal(dm.item_updated, timeout=15000) as hint_blocker:
        n = dm.enqueue_downloads(metas, "地毯清洗", 3)
    assert n == 3
    _wait_terminal(dl_env)
    rows_all = sum(len(daos.downloads.list_by_status(s)) for s in
                   ("pending", "running", "success", "failed", "canceled"))
    assert rows_all == 3                       # 只建了截断后的行
    states = [t.state for t in dl_env["sched"]._tasks.values()]
    assert len(states) == 4                    # 3 下载 + 1 提示
    args = hint_blocker.args
    assert args[0] == -1 and args[1] == "limit_reached"
    assert f"其余 {2} 条" in args[3]


def test_download_success_archives(dl_env: dict) -> None:
    dm: DownloadManager = dl_env["dm"]
    daos = dl_env["daos"]
    meta = _meta(plugin_id="fake", video_key="ok1", duration_s=12.0,
                 width=1280, height=720, file_size_bytes=100)
    n = dm.enqueue_downloads([meta], "地毯清洗", 1)
    assert n == 1
    _wait_terminal(dl_env)
    row = daos.downloads.list_by_status("success")[0]
    dest = Path(row.dest_path or "")
    assert dest.exists()
    assert dest.read_bytes() == b"\xab" * 100
    # 三级归档：工作目录/大类(关键词)/关键词/日期/
    parts = dest.relative_to(dl_env["wd"].workdir()).parts
    assert parts[0] == "地毯清洗" and parts[1] == "地毯清洗"
    assert dest.name.startswith("fake_地毯清洗_")


def test_concurrency_gate_batches(dl_env: dict) -> None:
    plugin: FakeDownloadPlugin = dl_env["plugin"]
    plugin.delay = 0.25
    dm: DownloadManager = dl_env["dm"]
    metas = [_meta(plugin_id="fake", video_key=f"c{i}") for i in range(4)]
    dm.enqueue_downloads(metas, "地毯", 4)
    _wait_terminal(dl_env)
    # 并发上限=配置值 2，且确实出现了并行（分批执行）
    assert plugin.max_active == 2


def test_resume_continues_from_partial_bytes(dl_env: dict) -> None:
    dm: DownloadManager = dl_env["dm"]
    daos = dl_env["daos"]
    plugin: FakeDownloadPlugin = dl_env["plugin"]
    old_part = dl_env["wd"].workdir() / ".downloading" / "old.part"
    old_part.parent.mkdir(parents=True, exist_ok=True)
    old_part.write_bytes(plugin.body[:60])
    meta = _meta(plugin_id="fake", video_key="r1")
    rid = daos.downloads.create(meta, "地毯")
    daos.downloads.update_state(
        rid, "running",
        resume=ResumeState(downloaded_bytes=60, total_bytes=100,
                           temp_path=str(old_part)),
    )
    dm.enqueue_resume(rid, meta, "地毯")
    _wait_terminal(dl_env)
    assert plugin.seen_resume == [60]          # 从断点字节继续
    row = daos.downloads.get(rid)
    assert row is not None and row.status == "success"
    dest = Path(row.dest_path or "")
    assert dest.read_bytes() == plugin.body    # 60 + 40 补齐为完整内容


def test_cancel_running_download(dl_env: dict, qtbot) -> None:
    plugin: FakeDownloadPlugin = dl_env["plugin"]

    def cooperative_download(meta, dest_part, on_progress, resume,
                             token):  # type: ignore[no-untyped-def]
        # 模拟长下载：轮询协作式取消令牌（与 S4 download_stream 行为一致）
        for _ in range(300):
            if token is not None:
                token.check()
            time.sleep(0.02)
        return ResumeState(downloaded_bytes=0, temp_path=str(dest_part) + ".part")

    plugin.download = cooperative_download  # type: ignore[method-assign]
    dm: DownloadManager = dl_env["dm"]
    dm.enqueue_downloads([_meta(plugin_id="fake", video_key="z1")], "地毯", 1)
    deadline = time.monotonic() + 5
    tid = next(iter(dl_env["sched"]._tasks))
    while time.monotonic() < deadline:
        if dl_env["sched"]._tasks[tid].state == "running":
            break
        qtbot.wait(20)
    dl_env["sched"].cancel(tid)
    states = _wait_terminal(dl_env)
    assert states == ["canceled"]


# ================= ForeignNetChecker（探测矩阵） =================

class FakeProbeClient:
    def __init__(self, script: list[str]) -> None:
        self.script = list(script)
        self.calls = 0

    def probe_url(self, url: str, timeout_s: float = 5.0) -> str:
        self.calls += 1
        return self.script.pop(0)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _checker(script: list[str], settings: SettingsDao,
             clock: FakeClock) -> tuple[ForeignNetChecker, FakeProbeClient]:
    client = FakeProbeClient(script)
    checker = ForeignNetChecker(client, ConfigService(), settings, clock)  # type: ignore[arg-type]
    return checker, client


@pytest.fixture
def net_settings(tmp_path: Path) -> SettingsDao:
    db = Database(tmp_path / "app.db")
    daos = make_daos(db)
    return daos.settings


@pytest.mark.parametrize(
    ("script", "expected"),
    [
        (["conn_fail", "dns_fail", "ok"], NetStatus.OK),
        (["ok", "ok", "ok"], NetStatus.OK),
        (["dns_fail", "dns_fail", "dns_fail"], NetStatus.OFFLINE),
        (["conn_fail", "dns_fail", "conn_fail"], NetStatus.BLOCKED),
        (["conn_fail", "conn_fail", "conn_fail"], NetStatus.BLOCKED),
    ],
)
def test_probe_result_matrix(net_settings: SettingsDao, script: list[str],
                             expected: NetStatus) -> None:
    checker, client = _checker(script, net_settings, FakeClock())
    assert checker.check(force=True) == expected
    assert client.calls == 3


def test_ttl_cache_and_force(net_settings: SettingsDao) -> None:
    clock = FakeClock()
    checker, client = _checker(["ok"] * 9, net_settings, clock)
    assert checker.check() == NetStatus.OK
    assert checker.check() == NetStatus.OK
    assert client.calls == 3                   # TTL 内复用缓存
    clock.now += 700.0
    assert checker.check() == NetStatus.OK
    assert client.calls == 6                   # 过期重新探测
    assert checker.check(force=True) == NetStatus.OK
    assert client.calls == 9


def test_status_persisted_cross_restart(net_settings: SettingsDao) -> None:
    checker, _client = _checker(["dns_fail"] * 3, net_settings, FakeClock())
    assert checker.check(force=True) == NetStatus.OFFLINE
    raw = net_settings.get("foreign_net_status")
    assert raw is not None and json.loads(raw)["status"] == "offline"
    # 新实例跨重启读取上次结论
    again, _c = _checker(["ok"] * 3, net_settings, FakeClock())
    assert again.last_known == NetStatus.OFFLINE
