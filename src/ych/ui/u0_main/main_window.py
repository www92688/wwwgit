# 主窗口（U0）：左侧导航 + 页面栈 + 工作目录引导 + 使用说明
from __future__ import annotations

import logging
from typing import Any, Protocol, cast

from PySide6.QtCore import QByteArray, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QIcon,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
    QPolygonF,
    QShortcut,
)
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ych import __version__

logger = logging.getLogger("ych.ui")

_NAV_KEYS = [
    ("采集工作台", "Capture"), ("预处理工作台", "Preprocess"),
    ("去重工作台", "Dedup"), ("失败列表", "Failures"), ("设置", "Settings"),
]

_ICON_COLOR = "#b7c1d4"
_ICON_SIZE = QSize(20, 20)


def _nav_icon(kind: str, color: str = _ICON_COLOR) -> QIcon:
    """QPainter 自绘单色导航图标（20×20 逻辑像素，2x 高清）。"""
    pm = QPixmap(40, 40)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    c = QColor(color)

    if kind == "Capture":            # 下载：竖线 + 箭头 + 托盘
        p.drawLine(QPointF(10, 3), QPointF(10, 10.5))
        p.setBrush(c)
        p.drawPolygon(QPolygonF([QPointF(5.5, 9), QPointF(14.5, 9), QPointF(10, 14)]))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(4, 17), QPointF(16, 17))
    elif kind == "Preprocess":       # 调节滑杆：三横线 + 圆点
        for y, dot in ((5, 13.0), (10, 6.5), (15, 12.0)):
            p.drawLine(QPointF(3, y), QPointF(17, y))
            p.setBrush(QColor("#232937"))
            p.drawEllipse(QPointF(dot, y), 2.3, 2.3)
            p.setBrush(Qt.BrushStyle.NoBrush)
    elif kind == "Dedup":            # 两个交叠圆角矩形（重复素材）
        p.drawRoundedRect(QRectF(3.5, 3.5, 9.5, 9.5), 2.5, 2.5)
        p.drawRoundedRect(QRectF(7.5, 7.5, 9.5, 9.5), 2.5, 2.5)
    elif kind == "Failures":         # 警告三角 + 感叹号
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPolygon(QPolygonF([
            QPointF(10, 2.6), QPointF(17.6, 16.6), QPointF(2.4, 16.6),
        ]))
        p.drawLine(QPointF(10, 8), QPointF(10, 12))
        p.drawPoint(QPointF(10, 14.6))
    else:                            # Settings：齿轮（圆心 + 齿）
        p.drawEllipse(QPointF(10, 10), 3.4, 3.4)
        import math
        for i in range(8):
            a = math.pi / 4 * i
            p.drawLine(
                QPointF(10 + 5.6 * math.cos(a), 10 + 5.6 * math.sin(a)),
                QPointF(10 + 8.1 * math.cos(a), 10 + 8.1 * math.sin(a)),
            )
    p.end()
    return QIcon(pm)


class _WorkDirsLike(Protocol):
    def is_set(self) -> bool: ...

    def set_workdir(self, path) -> None: ...   # type: ignore[no-untyped-def]


class HelpDialog(QDialog):
    """使用说明：三大工作台三步操作指引。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("使用说明")
        self.setMinimumSize(600, 500)
        text = QTextBrowser()
        text.setFrameShape(QTextBrowser.Shape.NoFrame)
        text.setOpenExternalLinks(True)
        text.setHtml(
            "<style>h3{color:#3b4252;margin:18px 0 4px 0;} "
            "p{color:#57606a;line-height:1.7;margin:4px 0;} "
            ".step{color:#4c6ef5;font-weight:600;}</style>"
            "<h3>采集（输词 → 搜 → 下）</h3>"
            "<p><span class='step'>1.</span> 顶部输入关键词（逗号分隔可批量）→ "
            "<span class='step'>2.</span> 点击「搜索」→ "
            "<span class='step'>3.</span> 勾选结果点击「下载选中」。"
            "下载数量上限在列表下方设置。</p>"
            "<h3>预处理（勾素材 → 选项 → 开始）</h3>"
            "<p><span class='step'>1.</span> 左侧勾选素材 → "
            "<span class='step'>2.</span> 右侧选择处理项（去水印/去字幕/裁剪/比例/去原声，"
            "手动模式可在画布框选区域）→ "
            "<span class='step'>3.</span> 点击「开始处理」。</p>"
            "<h3>去重（选素材 → 选方案 → 开始）</h3>"
            "<p><span class='step'>1.</span> 左侧勾选素材 → "
            "<span class='step'>2.</span> 先「分析重复度」获得推荐档位，"
            "选择轻/中/重度或自定义参数 → "
            "<span class='step'>3.</span> 点击「开始去重」。"
            "输出保存在 已去重/ 目录并展示前后重复度对比。</p>"
            "<h3>失败列表</h3>"
            "<p>失败任务可一键重新处理；网络/密钥问题请先到设置页检查。</p>"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.addWidget(text)
        btn_close = QPushButton("关闭")
        btn_close.setObjectName("secondaryBtn")
        btn_close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(btn_close)
        layout.addLayout(row)


class TaskStatusLabel(QLabel):
    """底部状态栏任务摘要：进行中数量 + 平均进度 + 批次完成度。

    scheduler 信号驱动（跨线程信号经 QObject 接收者排队回主线程）。
    """

    _TERMINAL = frozenset(
        {"success", "failed", "canceled", "skipped", "interrupted"},
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("taskStatus")
        self.setTextFormat(Qt.TextFormat.RichText)
        self._progress: dict[str, float] = {}
        self._batch: tuple[int, int] = (0, 0)
        self._render()

    def attach(self, scheduler: Any) -> None:
        scheduler.task_submitted.connect(self._on_submitted)
        scheduler.task_state.connect(self._on_state)
        scheduler.task_progress.connect(self._on_progress)
        scheduler.queue_stats.connect(self._on_queue_stats)

    def _on_submitted(self, task_id: str) -> None:
        self._progress.setdefault(str(task_id), 0.0)
        self._render()

    def _on_state(self, task_id: str, state: str, _msg: str) -> None:
        task_id = str(task_id)
        if state in self._TERMINAL:
            self._progress.pop(task_id, None)
        elif state == "running":
            self._progress.setdefault(task_id, 0.0)
        self._render()

    def _on_progress(self, task_id: str, ratio: float) -> None:
        self._progress[str(task_id)] = max(0.0, min(1.0, float(ratio)))
        self._render()

    def _on_queue_stats(self, done: int, total: int) -> None:
        self._batch = (0, 0) if total and done >= total else (int(done), int(total))
        self._render()

    def _render(self) -> None:
        n = len(self._progress)
        batch_txt = ""
        if self._batch[1]:
            batch_txt = f" · 批次 {self._batch[0]}/{self._batch[1]}"
        if n == 0:
            self.setText(
                f'<span style="color:#2f9e6e;">●</span>&nbsp; 就绪{batch_txt}',
            )
            return
        avg = sum(self._progress.values()) / n
        self.setText(
            f'<span style="color:#4c6ef5;">●</span>&nbsp; 进行中 {n} 项'
            f" · 平均进度 {avg:.0%}{batch_txt}",
        )


class MainWindow(QMainWindow):
    """导航 + 页面栈；首次启动引导设置工作目录。"""

    def __init__(self, ctx: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self.setWindowTitle(self.tr("源重构 — 素材采集与智能去重"))
        self.resize(1280, 800)
        self.setMinimumSize(1000, 660)

        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        from PySide6.QtWidgets import QVBoxLayout as _V

        nav_box = QWidget()
        nav_box.setObjectName("sideBar")
        nav_box.setStyleSheet("#sideBar { background: #232937; }")
        nav_layout = _V(nav_box)
        nav_layout.setContentsMargins(0, 14, 0, 12)
        nav_layout.setSpacing(10)

        # ---- 侧栏头部：应用标识 ----
        head = QWidget()
        head_row = QHBoxLayout(head)
        head_row.setContentsMargins(18, 2, 12, 6)
        head_row.setSpacing(8)
        mark = QLabel("源")
        mark.setFixedSize(26, 26)
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setStyleSheet(
            "background: #4c6ef5; color: white; border-radius: 7px;"
            "font-weight: 700; font-size: 14px;"
        )
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        t = QLabel(self.tr("源重构"))
        t.setObjectName("sideTitle")
        sub = QLabel(self.tr("采集 · 预处理 · 去重"))
        sub.setObjectName("sideSub")
        title_col.addWidget(t)
        title_col.addWidget(sub)
        head_row.addWidget(mark)
        head_row.addLayout(title_col, 1)
        nav_layout.addWidget(head)

        self.nav_list = QListWidget()
        self.nav_list.setObjectName("navList")
        self.nav_list.setIconSize(_ICON_SIZE)
        for item, key in _NAV_KEYS:
            it = QListWidgetItem(self.tr(item))
            it.setIcon(_nav_icon(key))
            self.nav_list.addItem(it)
        btn_help = QPushButton(self.tr("使用说明"))
        btn_help.setObjectName("secondaryBtn")
        btn_help.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_help.clicked.connect(self._show_help)


        nav_layout.addWidget(self.nav_list, 1)
        nav_layout.addWidget(btn_help)
        ver = QLabel(f"v{__version__}")
        ver.setObjectName("sideVersion")
        ver.setAlignment(Qt.AlignmentFlag.AlignCenter)
        nav_layout.addWidget(ver)
        root.addWidget(nav_box)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self.nav_list.currentRowChanged.connect(self._on_nav_changed)
        self.nav_list.setCurrentRow(0)
        self._install_shortcuts()
        self._restore_geometry()

        # ---- 底部状态栏：任务进行中摘要（scheduler 经 attach_task_status 接入）----
        self._task_status = TaskStatusLabel()
        status_bar = self.statusBar()
        assert status_bar is not None
        status_bar.addPermanentWidget(self._task_status, 1)

    def attach_task_status(self, scheduler: Any) -> None:
        """接线调度器信号（app 装配时调用一次）。"""
        self._task_status.attach(scheduler)

    # ---- 快捷键 / 窗口几何 ----
    def _install_shortcuts(self) -> None:
        """Ctrl+1..5 切换工作台。"""
        for i in range(len(_NAV_KEYS)):
            shortcut = QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self)
            shortcut.activated.connect(
                lambda row=i: self.nav_list.setCurrentRow(row),
            )

    def _restore_geometry(self) -> None:
        """恢复上次窗口大小/位置；无记录用默认尺寸。"""
        try:
            raw = self._ctx.config().get("win_geometry")
        except Exception:
            return
        if isinstance(raw, str) and raw:
            self.restoreGeometry(QByteArray.fromHex(raw.encode("ascii")))

    def closeEvent(self, event) -> None:   # type: ignore[no-untyped-def]
        try:
            # PySide6 存根把 data() 标为 bytes|bytearray|memoryview 联合类型
            data = cast(bytes, self.saveGeometry().toHex().data())
            self._ctx.config().set("win_geometry", data.decode("ascii"))
        except Exception:
            logger.warning("窗口几何保存失败", exc_info=True)
        super().closeEvent(event)

    # ---- 页面装配 ----
    def add_page(self, widget: QWidget) -> None:
        self.stack.addWidget(widget)

    def _on_nav_changed(self, index: int) -> None:
        if 0 <= index < self.stack.count():
            self.stack.setCurrentIndex(index)

    # ---- 工作目录向导 ----
    def ensure_workdir(self) -> bool:
        """无工作目录时强制引导；返回是否就绪。"""
        workdirs = self._ctx.workdirs()
        if workdirs.is_set():
            return True
        while True:
            QMessageBox.information(
                self, "初始设置",
                "开始使用前，请先选择素材保存的工作目录。\n"
                "建议选择空间充足的磁盘分区。",
            )
            from PySide6.QtWidgets import QFileDialog

            chosen = QFileDialog.getExistingDirectory(
                self, "选择素材工作目录", "",
            )
            if not chosen:
                return False
            try:
                workdirs.set_workdir(chosen)
                return True
            except Exception as exc:
                logger.warning("工作目录无效：%s", exc)
                QMessageBox.warning(
                    self, "目录不可用",
                    f"该目录无法作为工作目录：\n{exc}",
                )

    def _show_help(self) -> None:
        HelpDialog(self).exec()
