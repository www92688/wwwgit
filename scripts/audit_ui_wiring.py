# UI 信号接线审计：离屏实例化全部页面，检查按钮/复选/单选/下拉的信号连接。
# 用法：python scripts/audit_ui_wiring.py
# 注意：lambda 连接不被 receivers() 统计——复选框类报告为"未连接"时，
# 需人工确认是否为「提交时读值」的合理设计（如平台勾选、参数编辑器）。
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QPushButton,
    QRadioButton,
)

from ych.core.m3_dedup.techniques.registry import make_default_registry
from ych.services.s5_base.config_service import ConfigService
from ych.ui.u1_capture.capture_page import CapturePage
from ych.ui.u2_preprocess.preprocess_page import PreprocessPage
from ych.ui.u3_dedup.dedup_page import DedupPage
from ych.ui.u4_failures.failure_page import FailurePage
from ych.ui.u5_settings.settings_page import SettingsPage


class FakeCoord:
    def search_multi(self, *a, **k) -> None:
        return None


class FakeDM:
    item_updated = None

    def enqueue_downloads(self, metas, keyword, limit):
        return 0


class FakeHistory:
    def suggestions(self, prefix: str = "", limit: int = 20) -> list[str]:
        return []

    def record(self, keyword: str, platform_ids: list[str]) -> None:
        pass


class FakeScheduler:
    task_state = task_progress = queue_stats = None

    def submit(self, payload, priority=0):
        return "t"

    def register_handler(self, t, h) -> None:
        pass


class FakeFails:
    @staticmethod
    def list_recent(limit: int = 200) -> list:
        return []


def build_pages() -> dict[str, object]:
    cfg = ConfigService()
    return {
        "采集工作台": CapturePage(
            coordinator=FakeCoord(), download_manager=FakeDM(),
            history=FakeHistory(), config=cfg,
        ),
        "预处理工作台": PreprocessPage(scheduler=FakeScheduler()),
        "去重工作台": DedupPage(
            registry=make_default_registry(), scheme_manager=None,
        ),
        "失败列表": FailurePage(FakeFails, FakeScheduler()),
        "设置": SettingsPage(cfg),
    }


CHECKS: tuple[tuple[type, tuple[str, ...]], ...] = (
    (QPushButton, ("2clicked()",)),
    (QRadioButton, ("2toggled()", "2clicked()")),
    (QCheckBox, ("2toggled()", "2clicked()")),
    (QComboBox, ("2currentTextChanged(QString)", "2activated(int)",
                 "2currentIndexChanged(int)")),
)


def main() -> int:
    _app = QApplication([])
    dead: list[tuple[str, str, str]] = []
    pages = build_pages()
    for page_name, page in pages.items():
        for wtype, sigs in CHECKS:
            for w in page.findChildren(wtype):
                if sum(w.receivers(s) for s in sigs) == 0:
                    label = w.text() if hasattr(w, "text") else ""
                    dead.append((page_name, wtype.__name__,
                                 label or w.objectName() or "unnamed"))
    if dead:
        print("以下控件信号无接收者（需人工确认为「提交时读值」或真死控件）：")
        for page_name, wtype, text in dead:
            print(f"  [{page_name}] {wtype}: {text!r}")
        return 1
    print("审计通过：所有按钮/复选/单选/下拉均有信号连接")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
