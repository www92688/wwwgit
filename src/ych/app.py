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
    _set_window_icon(app)

    ctx = build_context()
    # 数据库就绪回填配置；失败不阻断启动（配置退化为默认值）
    try:
        ctx.database()
    except Exception as exc:
        logger.error("数据库初始化失败：%s", exc, exc_info=True)
    locale = str(ctx.config().get("language") or "zh_CN")
    ctx.i18n().switch_locale(locale)

    # 主题 + 翻译装配
    from ych.common.fsutil import bundle_root

    qss = bundle_root() / "ui" / "u6_common" / "theme.qss"
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
        ai_gateway=ctx.ai_gateway(),
        open_files=lambda: ctx.workdirs().workdir(),
    )
    coordinator = ctx.search_coordinator()
    coordinator.search_finished.connect(capture.on_search_finished)
    dm = ctx.download_manager()
    dm.item_updated.connect(capture.queue_view.on_item_updated)
    # 入队即登记平台/标题，队列不再显示 "-"
    ctx.scheduler().download_row_registered.connect(
        capture.queue_view.add_row_info,
    )

    # 国外平台总开关：发出请求后异步探测外网，可达才允许开启（不阻塞界面）
    from ych.ui.u6_common.llm_worker import LlmWorker

    _net_workers: list[LlmWorker] = []

    def _check_foreign_net_async() -> None:
        def _probe() -> object:
            return ctx.net_checker().check(force=True).value

        worker = LlmWorker(_probe)
        _net_workers.append(worker)
        worker.done.connect(
            lambda status: capture.confirm_foreign_enable(status == "ok")
        )
        worker.finished.connect(worker.deleteLater)
        worker.start()

    capture.foreign_switch_requested.connect(_check_foreign_net_async)

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
        ai_gateway=cast(Any, ctx.ai_gateway()),
        http=ctx.http(),
    )

    for page in (capture, preprocess, dedup, failures, settings):
        window.add_page(page)

    # 预处理/去重工作台素材：启动加载一次，之后下载成功即自动刷新
    wire_asset_refresh(ctx, preprocess, dedup, dm)

    window.show()
    if not window.ensure_workdir():
        logger.warning("未设置工作目录，部分功能不可用")
    return app.exec()


def _set_window_icon(app: Any) -> None:
    """任务栏/窗口图标：源码运行 → 仓库根 resources/；打包 → _internal/resources/。"""
    from PySide6.QtGui import QIcon

    from ych.common.fsutil import bundle_data_root

    ico = bundle_data_root() / "resources" / "app.ico"
    if ico.exists():
        app.setWindowIcon(QIcon(str(ico)))


def wire_asset_refresh(
    ctx: Any, preprocess: Any, dedup: Any, dm: Any,
) -> None:
    """把 raw 素材喂给预处理/去重工作台：启动一次 + 下载成功 + 换工作目录。"""

    def refresh() -> None:
        try:
            rows = ctx.daos().assets.list_by_kind("raw")
        except Exception as exc:
            logging.getLogger("ych.app").warning("素材列表刷新失败：%s", exc)
            return
        preprocess.set_assets(rows)
        dedup.set_assets([r.path for r in rows])

    def on_updated(_row_id: int, state: str, _progress: float, _msg: str) -> None:
        if state == "success":
            refresh()

    refresh()
    dm.item_updated.connect(on_updated)
    ctx.workdirs().workdir_changed.connect(lambda _path: refresh())


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
