# 健壮性回归：提交防连点 / 静默 return 补提示 / 国外总开关勾选清理 /
# 失败页异常保护。覆盖 2026-09-13 审计修复项。
from __future__ import annotations

from typing import Any

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QListWidget

from ych.services.s3_db.daos import AssetRow
from ych.ui.u1_capture.capture_page import CapturePage
from ych.ui.u2_preprocess.preprocess_page import PreprocessPage
from ych.ui.u3_dedup.dedup_page import DedupPage
from ych.ui.u4_failures.failure_page import FailurePage
from ych.ui.u6_common.toast import Toast


class _FakeScheduler:
    def __init__(self) -> None:
        self.payloads: list[Any] = []

    def submit(self, payload: Any) -> str:
        self.payloads.append(payload)
        return f"task-{len(self.payloads)}"


class _ToastSpy:
    """替换 Toast.show_message，记录调用（避免真实弹窗依赖窗口显示）。"""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.messages: list[tuple[str, bool]] = []
        spy = self

        def _fake(parent: object, msg: str, error: bool = False,
                  **_k: object) -> None:
            spy.messages.append((msg, error))

        monkeypatch.setattr(Toast, "show_message", _fake)

    def texts(self) -> list[str]:
        return [m for m, _e in self.messages]


def _asset_row(path: str) -> AssetRow:
    return AssetRow(id=1, path=path, kind="raw", size_bytes=1, duration_s=1.0,
                    width=64, height=48, mtime=0.0, category="清洗类",
                    keyword="地毯", date_str="2026-08-25", indexed_at="")


# ---------- 预处理页 ----------
def test_preprocess_double_click_submits_once(
    qtbot: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = _ToastSpy(monkeypatch)
    sched = _FakeScheduler()
    page = PreprocessPage(scheduler=sched)
    qtbot.addWidget(page)
    page.set_assets([_asset_row("C:/wd/a.mp4")])
    page.asset_tree.select_all(True)

    page._on_start()
    page._on_start()          # 3 秒窗口内的重复点击 → 拦截
    assert len(sched.payloads) == 1
    assert any("重复点击" in t for t in spy.texts())


def test_preprocess_scheduler_missing_gives_feedback(
    qtbot: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = _ToastSpy(monkeypatch)
    page = PreprocessPage(scheduler=None)   # 服务未装配
    qtbot.addWidget(page)
    page.set_assets([_asset_row("C:/wd/a.mp4")])
    page.asset_tree.select_all(True)
    page._on_start()          # 此前静默 return（点了没反应）
    assert spy.messages and spy.messages[-1][1] is True


def test_preprocess_frame_loader_missing_gives_feedback(
    qtbot: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = _ToastSpy(monkeypatch)
    page = PreprocessPage(frame_loader=None)
    qtbot.addWidget(page)
    page.set_assets([_asset_row("C:/wd/a.mp4")])
    page._load_frame_async("C:/wd/a.mp4")   # 此前静默 return
    assert any("预览" in t for t in spy.texts())


# ---------- 去重页 ----------
def test_dedup_double_click_emits_once(
    qtbot: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = _ToastSpy(monkeypatch)
    page = DedupPage()
    qtbot.addWidget(page)
    page.set_assets(["C:/wd/a.mp4", "C:/wd/b.mp4"])
    got: list[list[str]] = []
    page.dedup_requested.connect(lambda srcs, _params: got.append(srcs))

    page._emit_dedup()
    page._emit_dedup()
    assert len(got) == 1
    assert any("重复点击" in t for t in spy.texts())

    # 换一批素材 → 允许立即再次提交
    page.set_assets(["C:/wd/c.mp4"])
    page._emit_dedup()
    assert len(got) == 2


def test_dedup_analyze_double_click_emits_once(
    qtbot: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ToastSpy(monkeypatch)
    page = DedupPage()
    qtbot.addWidget(page)
    page.set_assets(["C:/wd/a.mp4"])
    got: list[list[str]] = []
    page.analyze_requested.connect(lambda srcs: got.append(srcs))
    page._emit_analyze()
    page._emit_analyze()
    assert len(got) == 1


def test_dedup_select_all_refreshes_once(qtbot: Any) -> None:
    page = DedupPage()
    qtbot.addWidget(page)
    page.set_assets([f"C:/wd/{i}.mp4" for i in range(50)])
    page.asset_list.item(0).setCheckState(Qt.CheckState.Unchecked)
    assert len(page.checked_paths()) == 49

    counter = {"n": 0}
    page.asset_list.itemChanged.connect(lambda _i: counter.__setitem__(
        "n", counter["n"] + 1))
    page._select_all()
    assert counter["n"] == 0        # blockSignals：不逐条触发刷新
    assert len(page.checked_paths()) == 50


# ---------- 采集页 ----------
def test_foreign_master_off_clears_child_checks(qtbot: Any) -> None:
    page = CapturePage()
    qtbot.addWidget(page)
    page.foreign_master.setChecked(True)   # config 为 None：直接放行开启
    tiktok = page.global_checks["tiktok"]
    assert tiktok.isEnabled()
    tiktok.setChecked(True)

    page.foreign_master.setChecked(False)
    assert not tiktok.isEnabled()
    assert not tiktok.isChecked()          # 此前仅禁用不清勾选


def test_foreign_probe_failed_recovers_and_toasts(
    qtbot: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = _ToastSpy(monkeypatch)
    page = CapturePage()
    qtbot.addWidget(page)
    page.on_foreign_probe_failed("网络异常")
    assert not page.foreign_master.isChecked()
    assert any("网络异常" in t for t in spy.texts())


# ---------- 失败列表页 ----------
class _FlakyScheduler:
    def __init__(self) -> None:
        self.ids: list[int] = []

    def submit_from_fail_record(self, rid: int) -> str:
        self.ids.append(rid)
        if rid == 2:
            raise RuntimeError("payload 重建失败")
        return "t"


class _FakeFails:
    def list_recent(self) -> list[Any]:
        return []

    def delete(self, rid: int) -> None:
        if rid == 99:
            raise RuntimeError("db locked")


def _record(rid: int) -> Any:
    return type("R", (), {
        "id": rid, "file_name": f"v{rid}.mp4", "fail_reason": "x",
        "error_code": "MED010", "fail_time": "2026-09-13",
        "task_type": "preprocess",
    })()


def test_failure_reprocess_reports_partial_failure(
    qtbot: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = _ToastSpy(monkeypatch)
    sched = _FlakyScheduler()
    page = FailurePage(_FakeFails(), sched)
    qtbot.addWidget(page)
    page._model.set_rows([_record(1), _record(2), _record(3)])
    page.table.selectAll()

    page._reprocess()
    assert sched.ids == [1, 2, 3]     # 单条失败不阻塞其余
    assert any("1 条失败" in t and "#2" in t for t in spy.texts())


def test_failure_refresh_db_error_toasts(
    qtbot: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = _ToastSpy(monkeypatch)

    class _Boom:
        def list_recent(self) -> list[Any]:
            raise RuntimeError("db locked")

    page = FailurePage(_Boom(), _FlakyScheduler())
    qtbot.addWidget(page)
    page.refresh()            # 此前未捕获异常（点了没反应）
    assert any("读取失败记录出错" in t for t in spy.texts())


def test_dedup_empty_selection_toasts(qtbot: Any,
                                      monkeypatch: pytest.MonkeyPatch) -> None:
    """冒烟：防连点新增状态在无参构造下可用；空选走 Toast。"""
    spy = _ToastSpy(monkeypatch)
    page = DedupPage()
    qtbot.addWidget(page)
    assert isinstance(page.asset_list, QListWidget)
    page._emit_dedup()        # 空选 → Toast，不抛错
    assert spy.texts()
