# UI 冒烟测试（对照 15.4）：pytest-qt；Fake 注入，不触网不碰 ffmpeg
from __future__ import annotations

from types import SimpleNamespace

import pytest

from ych.common.schemas import VideoMeta
from ych.core.m1_capture.search_coordinator import SearchResultSet


class FakeScheduler:
    """M4 调度替身：记录 submit；信号可手动触发。"""

    task_state = None
    task_progress = None
    queue_stats = None

    def __init__(self) -> None:
        from PySide6.QtCore import QObject, Signal

        class _Sig(QObject):
            _s1 = Signal(str, str, str)
            _s2 = Signal(str, float)
            _s3 = Signal(int, int)

        self.submitted: list[object] = []
        self.handlers: dict[str, object] = {}

    def submit(self, payload: object, priority: int = 0) -> str:
        self.submitted.append(payload)
        return f"task-{len(self.submitted)}"

    def register_handler(self, task_type: str, handler: object) -> None:
        self.handlers[task_type] = handler

    def submit_from_fail_record(self, record_id: int) -> str:
        self.submitted.append(("fail_record", record_id))
        return f"task-fr-{record_id}"


class FakeCoordinator:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def search_multi(self, keywords, filters, per_platform_limit=30,   # type: ignore[no-untyped-def]
                     token=None, platform_ids=None):
        self.calls.append({"keywords": list(keywords), "filters": filters,
                           "platform_ids": platform_ids})
        metas = [
            VideoMeta(plugin_id="douyin", video_key="v1", title="示例",
                      duration_s=15.5, width=1080, height=1920,
                      file_size_bytes=3 * 1024 * 1024,
                      watermark_tag="no", download_url="https://x/1"),
        ]
        return SimpleNamespace(items=metas)


class FakeDownloadManager:
    item_updated = None

    def __init__(self) -> None:
        self.enqueued: list[tuple[list, str, int]] = []

    def enqueue_downloads(self, metas, keyword, limit):   # type: ignore[no-untyped-def]
        self.enqueued.append((list(metas), keyword, limit))
        return len(metas)


class FakeHistory:
    def suggestions(self, prefix: str = "", limit: int = 20) -> list[str]:
        return ["地毯清洗", "水管疏通"]

    def record(self, keyword: str, platform_ids: list[str]) -> None:
        pass


@pytest.fixture
def qapp(qtbot):
    yield


def test_capture_page_search_and_download_flow(qapp, qtbot) -> None:
    from ych.ui.u1_capture.capture_page import CapturePage

    coord, dm = FakeCoordinator(), FakeDownloadManager()
    page = CapturePage(coordinator=coord, download_manager=dm,
                       history=FakeHistory())
    qtbot.addWidget(page)
    page.show()

    page.keyword_edit.setText("地毯清洗")
    # 触发搜索按钮（按文本定位按钮）
    from PySide6.QtWidgets import QPushButton

    for b in page.findChildren(QPushButton):
        if b.text() == "搜索":
            b.click()
            break
    assert len(coord.calls) == 1
    assert coord.calls[0]["keywords"] == ["地毯清洗"]

    # 模拟搜索完成回填 → 勾选下载
    metas = [
        VideoMeta(plugin_id="douyin", video_key="v1", title="示例",
                  duration_s=15.0, width=1080, height=1920,
                  download_url="https://x/1"),
    ]
    result_set = SearchResultSet(keyword="地毯清洗", items=metas)
    page.on_search_finished(result_set)
    assert page.result_list.list.count() == 1

    page._on_download(page.result_list.checked_metas(), "地毯清洗")
    assert len(dm.enqueued) == 1
    assert dm.enqueued[0][2] == page.limit_spin.value()
    assert dm.enqueued[0][1] == "地毯清洗"


def test_capture_page_with_real_config_service(qapp, qtbot) -> None:
    # 回归：真实 ConfigService 缺 download_limit 键时启动即崩（CFG001）
    from ych.services.s5_base.config_service import ConfigService
    from ych.ui.u1_capture.capture_page import CapturePage

    page = CapturePage(
        coordinator=FakeCoordinator(), download_manager=FakeDownloadManager(),
        history=FakeHistory(), config=ConfigService(),
    )
    qtbot.addWidget(page)
    assert page.limit_spin.value() == 20   # 取自 _DEFAULTS 的 download_limit


def test_capture_queue_view_renders_updates(qapp, qtbot) -> None:
    from ych.ui.u1_capture.download_queue_view import DownloadQueueView

    view = DownloadQueueView()
    qtbot.addWidget(view)
    # 入队预登记：平台/标题立即可见，状态为等待中（修复"-"列）
    view.add_row_info(7, "pixabay", "sunset river")
    assert view.table.item(0, 0).text() == "pixabay"
    assert view.table.item(0, 1).text() == "sunset river"
    assert view.table.item(0, 2).text() == "等待中"
    view.on_item_updated(7, "running", 0.4, "")
    view.on_item_updated(7, "success", 1.0, "")
    assert view.table.rowCount() == 1
    assert view.table.item(0, 2).text() == "已完成"
    # DL010 提示行（row_id=-1）
    view.on_item_updated(-1, "limit_reached", 1.0, "已达单次下载上限，其余 2 条未下载")
    assert view.table.rowCount() == 2


def test_preprocess_page_build_payload(qapp, qtbot) -> None:
    from ych.services.s3_db.daos import AssetRow
    from ych.ui.u2_preprocess.preprocess_page import PreprocessPage

    sched = FakeScheduler()
    page = PreprocessPage(scheduler=sched)
    qtbot.addWidget(page)
    rows = [
        AssetRow(id=1, path=r"C:\wd\清洗类\地毯\2026-08-25\a.mp4", kind="raw",
                 size_bytes=1, duration_s=1.0, width=64, height=48, mtime=0.0,
                 category="清洗类", keyword="地毯", date_str="2026-08-25",
                 indexed_at="")
    ]
    page.set_assets(rows)
    page.asset_tree.select_all(True)
    payload = page.build_payload()
    assert payload is not None and payload.type == "preprocess"
    items = list(payload.data["items"])   # type: ignore[attr-defined]
    assert len(items) == 1
    page._on_start()
    assert len(sched.submitted) == 1


def test_dedup_page_preset_and_signals(qapp, qtbot) -> None:
    from ych.core.m3_dedup.techniques.registry import make_default_registry
    from ych.ui.u3_dedup.dedup_page import DedupPage

    page = DedupPage(registry=make_default_registry(), scheme_manager=None)
    qtbot.addWidget(page)
    page.set_assets([r"C:\wd\已去重\清洗类\地毯\2026-08-25\a_deduped.mp4"])

    captured: dict[str, object] = {}
    page.analyze_requested.connect(
        lambda srcs: captured.update(analyze=list(srcs)))
    page.dedup_requested.connect(
        lambda srcs, params: captured.update(dedup=(list(srcs), params)))

    for b in _buttons(page):
        if b.text().startswith("分析重复度"):
            b.click()
        if b.text().startswith("开始去重"):
            b.click()
    assert captured["analyze"] == [r"C:\wd\已去重\清洗类\地毯\2026-08-25\a_deduped.mp4"]
    _srcs, params = captured["dedup"]   # type: ignore[misc]
    assert isinstance(params, list) and len(params) > 0
    ids = {p["id"] for p in params}   # type: ignore[index]
    assert ids <= {"mirror", "crop_scale", "color_filter", "speed", "border"}

    # 推荐档徽标渲染
    page.mark_recommended("heavy")
    labels = {r.text() for r in page._preset_radios.values()}
    assert any("推荐档" in t for t in labels)


def _buttons(widget):   # type: ignore[no-untyped-def]
    from PySide6.QtWidgets import QPushButton

    return widget.findChildren(QPushButton)


def test_capture_open_files_button(qapp, qtbot, tmp_path, monkeypatch) -> None:
    """「查看文件」：正常打开工作目录；未设置目录时提示而非报错。"""
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtWidgets import QMessageBox

    from ych.ui.u1_capture.capture_page import CapturePage

    opened: list[object] = []
    monkeypatch.setattr(
        QDesktopServices, "openUrl",
        staticmethod(lambda url: opened.append(url)),
    )
    boxes: list[tuple] = []
    monkeypatch.setattr(
        QMessageBox, "information",
        staticmethod(lambda *a, **k: boxes.append(a)),
    )

    page = CapturePage(open_files=lambda: tmp_path)
    qtbot.addWidget(page)
    page._on_open_files()
    assert len(opened) == 1
    from pathlib import Path

    assert Path(opened[0].toLocalFile()) == tmp_path   # type: ignore[attr-defined]
    assert boxes == []

    def _unset() -> object:
        raise RuntimeError("尚未设置工作目录")

    page2 = CapturePage(open_files=_unset)
    qtbot.addWidget(page2)
    page2._on_open_files()
    assert len(boxes) == 1 and len(opened) == 1   # 未开目录，仅提示


def test_wire_asset_refresh_feeds_pages(qapp, qtbot) -> None:
    """下载成功信号触发后，预处理/去重工作台应同步到最新素材。"""
    from types import SimpleNamespace

    from PySide6.QtCore import QObject, Signal

    from ych.app import wire_asset_refresh
    from ych.services.s3_db.daos import AssetRow

    class _Sigs(QObject):
        item_updated = Signal(int, str, float, str)
        workdir_changed = Signal(object)
        scan_finished = Signal(int)

    sigs = _Sigs()
    rows = [
        AssetRow(id=1, path=r"C:\wd\清洗类\地毯\2026-08-25\a.mp4", kind="raw",
                 size_bytes=1, duration_s=1.0, width=64, height=48, mtime=0.0,
                 category="清洗类", keyword="地毯", date_str="2026-08-25",
                 indexed_at="")
    ]
    daos = SimpleNamespace(assets=SimpleNamespace(list_by_kind=lambda kind: rows))
    ctx = SimpleNamespace(
        daos=lambda: daos,
        workdirs=lambda: SimpleNamespace(workdir_changed=sigs.workdir_changed),
        scan_indexer=lambda: SimpleNamespace(
            scan_finished=sigs.scan_finished,
            incremental_scan=lambda: 0,
        ),
    )
    pre_calls: list[list[AssetRow]] = []
    dedup_calls: list[list[str]] = []
    preprocess = SimpleNamespace(set_assets=pre_calls.append)
    dedup = SimpleNamespace(set_assets=dedup_calls.append)
    dm = SimpleNamespace(item_updated=sigs.item_updated)

    wire_asset_refresh(ctx, preprocess, dedup, dm)
    assert len(pre_calls) == 1 and pre_calls[0] == rows       # 启动加载一次
    assert dedup_calls[0] == [rows[0].path]

    sigs.item_updated.emit(1, "running", 0.5, "")             # 进行中不刷新
    assert len(pre_calls) == 1

    sigs.item_updated.emit(1, "success", 1.0, r"C:\wd\x.mp4")  # 成功即刷新
    assert len(pre_calls) == 2 and dedup_calls[1] == [rows[0].path]

    sigs.workdir_changed.emit(rows[0].path)                   # 换目录刷新
    assert len(pre_calls) == 3


def test_failure_page_reprocess(qapp, qtbot, tmp_path) -> None:
    from ych.services.s3_db.daos import make_daos
    from ych.services.s3_db.database import Database
    from ych.ui.u4_failures.failure_page import FailurePage

    daos = make_daos(Database(tmp_path / "app.db"))
    rid = daos.fails.add(file_name="a.mp4", reason="转码失败", code="MED010",
                         task_type="preprocess",
                         payload={"type": "preprocess",
                                  "data": {"items": [{"src": "a.mp4"}]}})
    sched = FakeScheduler()
    page = FailurePage(daos.fails, sched)
    qtbot.addWidget(page)
    page.refresh()
    assert page._model.rowCount() == 1
    page.table.selectRow(0)
    for b in _buttons(page):
        if b.text() == "重新处理":
            b.click()
            break
    assert ("fail_record", rid) in sched.submitted


def test_i18n_switch_loads_qm(qapp, qtbot) -> None:
    from ych.services.s5_base.i18n_service import I18nService

    service = I18nService()
    assert "zh_CN" in service.available_locales()
    assert "en_US" in service.available_locales()

    received: list[str] = []
    service.locale_changed.connect(received.append)
    service.switch_locale("en_US")
    assert service.current_locale() == "en_US"
    assert received[-1] == "en_US"


def test_asset_tree_check_cascades(qapp, qtbot) -> None:
    """父节点勾选级联到叶子；叶子取消后父节点变三态。"""
    from PySide6.QtCore import Qt

    from ych.services.s3_db.daos import AssetRow
    from ych.ui.u2_preprocess.asset_tree import AssetTree

    def _row(path: str, cat: str, kw: str) -> AssetRow:
        return AssetRow(id=0, path=path, kind="raw", size_bytes=1,
                        duration_s=1.0, width=64, height=48, mtime=0.0,
                        category=cat, keyword=kw, date_str="2026-09-05",
                        indexed_at="")

    rows = [
        _row(r"C:\wd\砍木头视频\砍木头视频\2026-09-05\a.mp4",
             "砍木头视频", "砍木头视频"),
        _row(r"C:\wd\砍木头视频\砍木头视频\2026-09-05\b.mp4",
             "砍木头视频", "砍木头视频"),
        _row(r"C:\wd\木头\木头\2026-09-05\c.mp4", "木头", "木头"),
    ]
    tree = AssetTree()
    qtbot.addWidget(tree)
    tree.set_assets(rows)
    assert tree.topLevelItemCount() == 2

    top = tree.topLevelItem(0)
    top.setCheckState(0, Qt.CheckState.Checked)     # 勾大类 → 叶子全选
    assert top.child(0).checkState(0) == Qt.CheckState.Checked
    assert len(tree.checked_files()) == 2

    leaf = top.child(0).child(1)
    leaf.setCheckState(0, Qt.CheckState.Unchecked)  # 取消一个叶子 → 父三态
    assert top.child(0).checkState(0) == Qt.CheckState.PartiallyChecked
    assert top.checkState(0) == Qt.CheckState.PartiallyChecked
    assert tree.checked_files() == [rows[0].path]

    leaf.setCheckState(0, Qt.CheckState.Checked)    # 恢复 → 父全勾
    assert top.checkState(0) == Qt.CheckState.Checked
    assert len(tree.checked_files()) == 2


def test_preprocess_preview_and_empty_selection_toast(
    qapp, qtbot, tmp_path, monkeypatch,
) -> None:
    """预览框选帧可用；空选时开始/预览给出提示而非无响应。"""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage

    from ych.services.s3_db.daos import AssetRow
    from ych.ui.u2_preprocess.preprocess_page import PreprocessPage
    from ych.ui.u6_common.toast import Toast

    captured: list[str] = []
    monkeypatch.setattr(
        Toast, "show_message",
        staticmethod(lambda parent, msg, **k: captured.append(msg)),
    )
    png = tmp_path / "frame.png"
    img = QImage(4, 4, QImage.Format.Format_ARGB32)
    img.fill(0)
    assert img.save(str(png))

    sched = FakeScheduler()
    page = PreprocessPage(scheduler=sched, frame_loader=lambda src: str(png))
    qtbot.addWidget(page)
    page.set_assets([AssetRow(
        id=1, path=r"C:\wd\砍木头视频\a.mp4", kind="raw", size_bytes=1,
        duration_s=15.0, width=1280, height=720, mtime=0.0,
        category="砍木头视频", keyword="砍木头视频",
        date_str="2026-09-05", indexed_at="",
    )])

    page._on_start()                                # 空选：Toast 提示
    assert "勾选" in captured[-1]
    assert sched.submitted == []
    page._on_preview()
    assert "勾选" in captured[-1]

    page.asset_tree.topLevelItem(0).setCheckState(0, Qt.CheckState.Checked)
    shown: list[str] = []
    page.load_frame_image = shown.append   # type: ignore[method-assign]
    page._on_preview()
    qtbot.waitUntil(lambda: bool(shown), timeout=5000)
    assert shown == [str(png)]

    page._on_start()                                # 有勾选：正常提交
    assert len(sched.submitted) == 1


def test_dedup_empty_selection_toast(qapp, qtbot, monkeypatch) -> None:
    from ych.core.m3_dedup.techniques.registry import make_default_registry
    from ych.ui.u3_dedup.dedup_page import DedupPage
    from ych.ui.u6_common.toast import Toast

    captured: list[str] = []
    monkeypatch.setattr(
        Toast, "show_message",
        staticmethod(lambda parent, msg, **k: captured.append(msg)),
    )
    got: dict[str, object] = {}
    page = DedupPage(registry=make_default_registry(), scheme_manager=None)
    qtbot.addWidget(page)
    page.analyze_requested.connect(
        lambda srcs: got.update(analyze=list(srcs)))
    page.dedup_requested.connect(
        lambda srcs, params: got.update(dedup=(list(srcs), params)))

    page._emit_analyze()
    page._emit_dedup()
    assert len(captured) == 2 and "勾选" in captured[0]
    assert got == {}


def test_wire_task_feedback(qapp, qtbot, tmp_path, monkeypatch) -> None:
    """任务终态反馈：下载不提示、报告回填、失败/去重对比 Toast。"""
    from types import SimpleNamespace

    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QWidget

    from ych.app import wire_task_feedback
    from ych.ui.u6_common.toast import Toast

    captured: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        Toast, "show_message",
        staticmethod(lambda parent, msg, error=False, **k:
                     captured.append((msg, error))),
    )

    class _Sigs(QObject):
        task_done = Signal(str, str, str, str, object)

    sigs = _Sigs()
    report = SimpleNamespace(overall_score=60.0)
    ctx = SimpleNamespace(
        daos=lambda: SimpleNamespace(reports=SimpleNamespace(
            latest_for=lambda p: report)),
        log_dir=lambda: tmp_path,
    )
    rendered: list[object] = []
    dedup = SimpleNamespace(render_report=rendered.append)
    parent = QWidget()
    qtbot.addWidget(parent)
    compare_srcs: dict[str, list[str]] = {}

    toast = wire_task_feedback(ctx, sigs, dedup, parent, compare_srcs)
    toast("已提交 3 条预处理任务")
    assert captured[-1] == ("已提交 3 条预处理任务", False)

    sigs.task_done.emit("t1", "download", "success", "", {})   # 下载不提示
    assert len(captured) == 1

    compare_srcs["t2"] = [r"C:\w\a.mp4"]
    sigs.task_done.emit("t2", "compare", "success", "",
                        {"overall_score": 60.0})
    assert rendered == [report]
    assert "重复度分析完成" in captured[-1][0]

    sigs.task_done.emit("t3", "preprocess", "failed", "[AI001] 模型缺失", {})
    assert captured[-1][1] is True and "AI001" in captured[-1][0]

    sigs.task_done.emit("t4", "dedup", "success", "",
                        {"outputs": ["o"], "failed": 0, "skipped": 0,
                         "before_pct": 80.0, "after_pct": 12.0})
    assert "80.0% → 12.0%" in captured[-1][0]


def test_download_queue_cancel_button(qapp, qtbot) -> None:
    """队列「取消」：点击发出 row_id；终态后按钮禁用。"""
    from ych.ui.u1_capture.download_queue_view import DownloadQueueView

    view = DownloadQueueView()
    qtbot.addWidget(view)
    cancels: list[int] = []
    view.cancel_requested.connect(cancels.append)
    view.add_row_info(7, "pixabay", "sunset river")
    btn = view._cancel_btns[7]
    assert btn.isEnabled()
    btn.click()
    assert cancels == [7]
    view.on_item_updated(7, "success", 1.0, "")
    assert not btn.isEnabled()
