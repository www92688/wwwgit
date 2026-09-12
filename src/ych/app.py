# 应用入口：LogService.setup → AppContext DI → 主窗口启动（冷启动 ≤5s：懒加载）
from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from ych.common.errors import AppError


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from ych.context import build_context, user_data_dir
    from ych.services.s5_base.log_service import LogService

    # 日志写用户数据目录（打包后 CWD 可能不可写，禁用相对路径）
    LogService.setup(log_dir=user_data_dir() / "logs")
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

    # 主题 + 翻译装配（theme=light/dark/system，设置页可运行时切换）
    from ych.ui.u6_common.theme import apply_theme

    apply_theme(app, str(ctx.config().get("theme") or "system"))

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

    def _on_cancel_download(row_id: int) -> None:
        try:
            ctx.scheduler().cancel_by_row(int(row_id))
        except AppError as exc:
            toast(f"取消失败：{exc.message}", error=True)

    capture.queue_view.cancel_requested.connect(_on_cancel_download)

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

    preprocess = PreprocessPage(
        scheduler=cast(Any, ctx.scheduler()),
        frame_loader=lambda src: _load_preview_frame(ctx, src),
    )
    dedup = DedupPage(
        registry=ctx.technique_registry(), scheme_manager=ctx.scheme_manager(),
    )
    compare_srcs: dict[str, list[str]] = {}
    dedup.analyze_requested.connect(
        lambda srcs: _submit_compare(ctx, srcs, compare_srcs))
    dedup.dedup_requested.connect(
        lambda srcs, params: _submit_dedup(ctx, srcs, params))
    failures = FailurePage(ctx.daos().fails, ctx.scheduler())
    settings = SettingsPage(
        cast(Any, ctx.config()), i18n=cast(Any, ctx.i18n()),
        net_checker=cast(Any, ctx.net_checker()),
        ai_gateway=cast(Any, ctx.ai_gateway()),
        http=ctx.http(),
        model_downloader=cast(Any, ctx.model_downloader()),
    )

    for page in (capture, preprocess, dedup, failures, settings):
        window.add_page(page)
    # 底部状态栏：任务进行中摘要（进行中数量/平均进度/批次完成度）
    window.attach_task_status(ctx.scheduler())

    # 预处理/去重工作台素材：启动加载一次，之后下载成功即自动刷新
    refresh_assets = wire_asset_refresh(ctx, preprocess, dedup, dm)

    # 崩溃恢复：扫描遗留 running 行（下载续传重排队 / 处理任务转失败列表）。
    # 必须在 download handler 注册（ctx.download_manager() 已构造）之后执行
    try:
        summary = ctx.scheduler().recover_on_startup()
        if summary.resumed_downloads or summary.moved_to_fail \
                or summary.removed_orphan_parts:
            logger.info(
                "崩溃恢复：续传重排 %d 条，转失败列表 %d 条，清理孤儿 .part %d 个",
                summary.resumed_downloads, summary.moved_to_fail,
                summary.removed_orphan_parts,
            )
            refresh_assets()
    except Exception:
        logger.exception("崩溃恢复失败（不影响启动）")

    # 处理类任务反馈：完成/失败 Toast，分析报告回填去重页
    toast = wire_task_feedback(ctx, ctx.scheduler(), dedup, window, compare_srcs)
    preprocess.submitted.connect(lambda n: toast(f"已提交 {n} 条预处理任务"))
    dedup.analyze_requested.connect(lambda _srcs: toast("已提交重复度分析"))
    dedup.dedup_requested.connect(
        lambda srcs, _params: toast(f"已提交 {len(srcs)} 条去重任务"))
    coordinator.search_failed.connect(
        lambda _kw, msg: toast(f"搜索失败：{msg}", error=True, timeout_ms=8000),
    )
    # 搜索失败时恢复「搜索」按钮可用
    coordinator.search_failed.connect(capture.on_search_failed)

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


def _load_preview_frame(ctx: Any, src: str) -> str:
    """取素材中段单帧存临时 PNG 并返回路径（后台线程执行）。

    ts 取 min(1s, 时长/2)：避免短视频 -ss 越过末尾抽不到帧。
    """
    import tempfile

    import numpy as np
    from PySide6.QtGui import QImage

    path = Path(src)
    info = ctx.prober().probe(path)
    ts = min(1.0, max(0.0, (info.duration_s or 0.0) / 2.0))
    frame = ctx.frame_extractor().single(path, ts)
    img = np.ascontiguousarray(frame.img)
    h, w = img.shape[:2]
    qimg = QImage(img.data, w, h, w * 3, QImage.Format.Format_BGR888)
    out = (Path(tempfile.gettempdir()) / "YuChongGou"
           / f"preview_{abs(hash(src)) % 10 ** 8}.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    if not qimg.save(str(out)):
        raise RuntimeError("预览帧落盘失败")
    return str(out)


def wire_task_feedback(
    ctx: Any, sched: Any, dedup: Any, parent: Any,
    compare_srcs: dict[str, list[str]],
) -> Callable[..., None]:
    """处理类任务（preprocess/dedup/compare）终态反馈：Toast + 报告回填。

    task_done 由工作线程发出，经 QObject 桥接收者排队回主线程再弹 Toast。
    返回 toast 函数供提交时复用。
    """
    from PySide6.QtCore import QObject

    from ych.ui.u6_common.toast import Toast

    def toast(msg: str, error: bool = False, timeout_ms: int = 4000) -> None:
        Toast.show_message(parent, msg, error=error, log_dir=ctx.log_dir(),
                           timeout_ms=timeout_ms)

    labels = {"preprocess": "预处理", "dedup": "去重", "compare": "重复度分析"}

    class _Bridge(QObject):
        def on_done(
            self, task_id: str, ttype: str, state: str,
            message: str, summary: Any,
        ) -> None:
            if ttype == "download":
                return                  # 下载队列视图已逐条反馈
            label = labels.get(ttype, ttype)
            if state == "success":
                failed = int(summary.get("failed") or 0)
                skipped = int(summary.get("skipped") or 0)
                extra = "".join(
                    f"，{word} {count} 条"
                    for word, count in (("失败", failed), ("跳过", skipped))
                    if count
                )
                if ttype == "preprocess":
                    toast(f"预处理完成：成功 {len(summary.get('outputs') or [])} 条"
                          f"{extra}；输出与原文件同目录（_cleaned 后缀）")
                elif ttype == "dedup":
                    before = summary.get("before_pct")
                    after = summary.get("after_pct")
                    tail = (f"；重复度 {before}% → {after}%"
                            if before is not None and after is not None else "")
                    toast(f"去重完成：成功 {len(summary.get('outputs') or [])} 条"
                          f"{extra}{tail}；输出在 已去重/ 目录")
                elif ttype == "compare":
                    srcs = compare_srcs.get(task_id) or []
                    if srcs:
                        report = ctx.daos().reports.latest_for(Path(srcs[0]))
                        if report is not None:
                            dedup.render_report(report)
                    toast(f"重复度分析完成{extra}，已按结果标注推荐档位")
            elif state == "failed":
                extra = ("。可到 设置 → AI 模型 下载所需模型"
                         if "AI001" in message else "")
                toast(f"{label}失败：{message}{extra}",
                      error=True, timeout_ms=8000)
            elif state == "canceled":
                toast(f"{label}任务已取消")

    bridge = _Bridge()
    bridge.setParent(parent)    # 挂到主窗口：防 GC 断连 + 线程归属主线程
    sched.task_done.connect(bridge.on_done)   # 接收者为 QObject → 队列到主线程
    return toast


def wire_asset_refresh(
    ctx: Any, preprocess: Any, dedup: Any, dm: Any,
) -> Callable[[], None]:
    """把 raw 素材喂给预处理/去重工作台：启动一次 + 下载成功 + 换工作目录。

    同时接线工作目录增量扫描（后台线程）：手动放入的文件也能进素材索引，
    扫描完成后刷新两工作台列表。返回 refresh 供崩溃恢复后复用。
    """

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

    def run_scan() -> None:
        """后台线程增量扫描工作目录；完成信号回主线程后刷新列表。"""
        import threading

        indexer = ctx.scan_indexer()

        def _work() -> None:
            try:
                indexer.incremental_scan()
            except Exception as exc:
                logging.getLogger("ych.app").warning("素材增量扫描失败：%s", exc)

        threading.Thread(target=_work, daemon=True,
                         name="ych-asset-scan").start()

    def on_scan_finished(_added: int) -> None:
        # ScanIndexer 为主线程 QObject，信号经队列回主线程
        refresh()

    refresh()
    dm.item_updated.connect(on_updated)
    ctx.workdirs().workdir_changed.connect(lambda _path: refresh())
    ctx.workdirs().workdir_changed.connect(lambda _path: run_scan())
    ctx.scan_indexer().scan_finished.connect(on_scan_finished)
    run_scan()          # 启动即扫一遍（拾遗：手动放入工作目录的素材）
    return refresh


def _submit_compare(
    ctx: Any, srcs: list[str], compare_srcs: dict[str, list[str]],
) -> None:
    from ych.common.schemas import TaskPayload

    task_id = ctx.scheduler().submit(TaskPayload(
        type="compare", data={"srcs": srcs, "mode": "both"}))
    compare_srcs[task_id] = srcs


def _submit_dedup(
    ctx: Any, srcs: list[str], params: list[dict[str, object]],
) -> None:
    from ych.common.schemas import TaskPayload

    items = [{"src": s, "technique_params": params} for s in srcs]
    ctx.scheduler().submit(TaskPayload(type="dedup", data={"items": items}))


if __name__ == "__main__":
    sys.exit(main())
