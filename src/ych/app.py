# 应用入口：LogService.setup → AppContext DI → 主窗口启动（冷启动 ≤5s：懒加载）
from __future__ import annotations

import logging
import sys
from typing import Any, cast


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from ych.context import build_context
    from ych.services.s5_base.log_service import LogService

    LogService.setup()
    logger = logging.getLogger("ych.app")
    logger.info("应用启动")

    app = QApplication(sys.argv)

    ctx = build_context()
    # 数据库就绪回填配置；失败不阻断启动（配置退化为默认值）
    try:
        ctx.database()
    except Exception as exc:
        logger.error("数据库初始化失败：%s", exc)
    locale = str(ctx.config().get("language") or "zh_CN")
    ctx.i18n().switch_locale(locale)

    # 主题 + 翻译装配
    from pathlib import Path

    qss = Path(__file__).parent / "ui" / "u6_common" / "theme.qss"
    if qss.exists():
        app.setStyleSheet(qss.read_text(encoding="utf-8"))

    # 业务 handler 注册（preprocess/dedup/compare）
    ctx.register_task_handlers()

    from ych.ui.u0_main.main_window import MainWindow
    from ych.ui.u1_capture.capture_page import CapturePage
    from ych.ui.u2_preprocess.preprocess_page import PreprocessPage
    from ych.ui.u3_dedup.dedup_page import DedupPage
    from ych.ui.u4_failures.failure_page import FailurePage
    from ych.ui.u5_settings.settings_page import SettingsPage

    window = MainWindow(ctx)

    capture = CapturePage(
        coordinator=ctx.search_coordinator(),
        download_manager=ctx.download_manager(),
        history=ctx.history(),
        config=ctx.config(),
    )
    coordinator = ctx.search_coordinator()
    coordinator.search_finished.connect(capture.on_search_finished)
    dm = ctx.download_manager()
    dm.item_updated.connect(capture.queue_view.on_item_updated)

    preprocess = PreprocessPage(scheduler=cast(Any, ctx.scheduler()))
    dedup = DedupPage(
        registry=ctx.technique_registry(), scheme_manager=ctx.scheme_manager(),
    )
    dedup.analyze_requested.connect(lambda srcs: _submit_compare(ctx, srcs))
    dedup.dedup_requested.connect(
        lambda srcs, params: _submit_dedup(ctx, srcs, params))
    failures = FailurePage(ctx.daos().fails, ctx.scheduler())
    settings = SettingsPage(
        cast(Any, ctx.config()), i18n=cast(Any, ctx.i18n()),
        net_checker=cast(Any, ctx.net_checker()),
    )

    for page in (capture, preprocess, dedup, failures, settings):
        window.add_page(page)

    window.show()
    if not window.ensure_workdir():
        logger.warning("未设置工作目录，部分功能不可用")
    return app.exec()


def _submit_compare(ctx, srcs: list[str]) -> None:   # type: ignore[no-untyped-def]
    from ych.common.schemas import TaskPayload

    ctx.scheduler().submit(TaskPayload(type="compare",
                                       data={"srcs": srcs, "mode": "both"}))


def _submit_dedup(
    ctx: Any, srcs: list[str], params: list[dict[str, object]],
) -> None:
    from ych.common.schemas import TaskPayload

    items = [{"src": s, "technique_params": params} for s in srcs]
    ctx.scheduler().submit(TaskPayload(type="dedup", data={"items": items}))


if __name__ == "__main__":
    sys.exit(main())
