# 离屏渲染五个工作台页面 → PNG（视觉回归自查用，不弹窗口不触网）
# 用法：.venv/Scripts/python.exe scripts/ui_preview.py [输出目录]
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from ych.common.schemas import VideoMeta  # noqa: E402
from ych.services.s3_db.daos import AssetRow  # noqa: E402


def _asset(i: int, cat: str, kw: str, date: str) -> AssetRow:
    return AssetRow(
        id=i, path=rf"C:\wd\{cat}\{kw}\{date}\素材{i}.mp4", kind="raw",
        size_bytes=1024 * 1024 * 12, duration_s=15.0, width=1080, height=1920,
        mtime=0.0, category=cat, keyword=kw, date_str=date, indexed_at="",
    )


def _meta(i: int, pid: str) -> VideoMeta:
    return VideoMeta(
        plugin_id=pid, video_key=f"v{i}", title=f"示例素材视频 {i}",
        duration_s=15.0 + i, width=1080, height=1920,
        file_size_bytes=3 * 1024 * 1024, watermark_tag="no",
        download_url=f"https://x/{i}",
    )


class _FakeHistory:
    def suggestions(self, prefix: str = "", limit: int = 20) -> list[str]:
        return ["地毯清洗", "水管疏通", "砍木头"]


def main() -> int:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "build" / "ui_preview"
    out_dir.mkdir(parents=True, exist_ok=True)

    app = QApplication(sys.argv)
    qss = ROOT / "src" / "ych" / "ui" / "u6_common" / "theme.qss"
    app.setStyleSheet(qss.read_text(encoding="utf-8"))

    from ych.services.s5_base.config_service import ConfigService
    from ych.ui.u0_main.main_window import MainWindow
    from ych.ui.u1_capture.capture_page import CapturePage
    from ych.ui.u2_preprocess.preprocess_page import PreprocessPage
    from ych.ui.u3_dedup.dedup_page import DedupPage
    from ych.ui.u4_failures.failure_page import FailurePage
    from ych.ui.u5_settings.settings_page import SettingsPage

    ctx = SimpleNamespace(workdirs=lambda: SimpleNamespace(is_set=lambda: True))
    window = MainWindow(ctx)

    # 采集页：预填搜索结果 + 队列样例行
    capture = CapturePage(
        coordinator=None, download_manager=None, history=_FakeHistory(),
        config=ConfigService(),
    )
    capture.on_search_finished(SimpleNamespace(
        keyword="地毯清洗",
        items=[_meta(1, "pexels"), _meta(2, "pixabay"), _meta(3, "pexels")],
        unavailable_platforms=[("douyin", "占位"), ("kuaishou", "占位")],
    ))
    capture.queue_view.add_row_info(1, "pexels", "sunset river 4k")
    capture.queue_view.on_item_updated(1, "running", 0.42, "")
    capture.queue_view.add_row_info(2, "pixabay", "ocean waves drone")
    capture.queue_view.on_item_updated(2, "success", 1.0, "")
    capture.queue_view.add_row_info(3, "pexels", "forest mist")
    capture.queue_view.on_item_updated(3, "failed", 0.1, "")

    # 预处理页：样例素材树
    preprocess = PreprocessPage(scheduler=None)
    preprocess.set_assets([
        _asset(1, "清洗类", "地毯", "2026-09-01"),
        _asset(2, "清洗类", "地毯", "2026-09-01"),
        _asset(3, "修理工", "水管", "2026-09-02"),
    ])
    preprocess.asset_tree.topLevelItem(0).child(0).setCheckState(0, __import__("PySide6.QtCore", fromlist=["Qt"]).Qt.CheckState.Checked)

    # 去重页：样例素材 + 报告
    dedup = DedupPage()
    dedup.set_assets([
        rf"C:\wd\已去重\轻度\清洗类\地毯\2026-09-01\素材{i}_deduped.mp4"
        for i in (1, 2, 3)
    ])

    # 设置页：真实 ConfigService，其余依赖离线缺省
    settings = SettingsPage(ConfigService())

    pages = {
        "1_capture": capture,
        "2_preprocess": preprocess,
        "3_dedup": dedup,
        "5_settings": settings,
    }
    for name, page in pages.items():
        page.resize(1080, 700)
        page.show()
        app.processEvents()
        app.processEvents()
        page.grab().save(str(out_dir / f"{name}.png"))

    # 失败列表：真实 DAO（临时库）
    import tempfile

    from ych.services.s3_db.daos import make_daos
    from ych.services.s3_db.database import Database

    tmp = Path(tempfile.gettempdir()) / "ych_ui_preview" / "fails.db"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    if tmp.exists():
        tmp.unlink()
    daos = make_daos(Database(tmp))
    daos.fails.add(file_name="素材1.mp4", reason="AI 模型缺失：watermark_yolov8n",
                   code="AI001", task_type="preprocess", payload={})
    daos.fails.add(file_name="素材2.mp4", reason="下载超时", code="NET010",
                   task_type="download", payload={})
    failures = FailurePage(daos.fails, None)
    failures.resize(1080, 700)
    failures.show()
    app.processEvents()
    failures.grab().save(str(out_dir / "4_failures.png"))

    # 主窗口（含导航）：采集页入栈
    window.add_page(capture)
    window.resize(1280, 800)
    window.show()
    app.processEvents()
    app.processEvents()
    window.grab().save(str(out_dir / "0_main.png"))
    print("saved to", out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
