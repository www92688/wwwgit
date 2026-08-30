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


# ================= 六平台骨架占位 =================

@pytest.mark.parametrize(
    "plugin_cls_path",
    [
        "ych.core.m1_capture.plugins.douyin_plugin:DouyinPlugin",
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
    # 国内默认全开；国外总开关默认关
    assert {p.id for p in manager.enabled("cn")} == {
        "douyin", "kuaishou", "bilibili", "xiaohongshu"}
    assert manager.enabled("global") == []
    memory_config.set("foreign_platforms_enabled", True)
    assert {p.id for p in manager.enabled("global")} == {"pexels", "pixabay"}
    # 插件级覆盖：关掉 pexels；总开关开着时 tiktok 可被强开
    memory_config.set("enabled_plugins", {"pexels": False, "tiktok": True})
    assert {p.id for p in manager.enabled("global")} == {"pixabay", "tiktok"}
    # 总开关关闭后即使插件级强开也不越权
    memory_config.set("foreign_platforms_enabled", False)
    assert manager.enabled("global") == []


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
    # bad 抛错隔离进 unavailable；down 可用性预检失败同样入列
    assert ("bad", ERR_PLG_SCHEMA_CHANGED) in result.unavailable_platforms
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
                 resume: ResumeState | None, token: CancellationToken | None):
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
