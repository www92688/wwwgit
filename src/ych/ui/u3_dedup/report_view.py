# 对比报告视图（U3）：总分环 + 三维度条 + 相似视频列表（before→after）
from __future__ import annotations

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ych.common.schemas import CompareReport


def _risk_color(pct: float) -> str:
    """重复度语义色：<30% 绿（安全）/ <60% 橙 / 其余红（高重复）。"""
    if pct < 30:
        return "#2f9e6e"
    if pct < 60:
        return "#e8890c"
    return "#e03131"


class _DimBar(QWidget):
    def __init__(self, title: str) -> None:
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        label = QLabel(title)
        label.setMinimumWidth(56)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        row.addWidget(label)
        row.addWidget(self.bar, 1)
        self._label = label
        self._title_src = title

    def set_src_title(self, title: str) -> None:
        """更新标题源串并显示（语言切换时传 tr 后的新文案）。"""
        self._title_src = title
        self._label.setText(title)

    def set_pct(self, value: float) -> None:
        pct = max(0.0, min(value, 1.0)) * 100.0
        self.bar.setValue(int(pct))
        color = _risk_color(pct)
        self.bar.setStyleSheet(f"QProgressBar::chunk {{ background: {color}; }}")


def _pct(v: float | None) -> str:
    return f"{v:.1f}%" if isinstance(v, (int, float)) else "—"


def _source_label(source: str) -> str:
    from PySide6.QtCore import QCoreApplication

    return {
        "manual": QCoreApplication.translate("ReportView", "手动"),
        "auto": QCoreApplication.translate("ReportView", "自动"),
        "local": QCoreApplication.translate("ReportView", "本地库"),
    }.get(source, source)


def _platform_label(platform_id: str) -> str:
    if platform_id == "local":
        from PySide6.QtCore import QCoreApplication

        return QCoreApplication.translate("ReportView", "本地素材库")
    return platform_id


class ReportView(QWidget):
    """render(report, before_pct=None, after_pct=None)。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._last: tuple[CompareReport | None, float | None, float | None] = (
            None, None, None,
        )
        root = QVBoxLayout(self)
        top = QHBoxLayout()

        ring_box = QVBoxLayout()
        self.overall_label = QLabel("—%")
        self.overall_label.setStyleSheet(
            "font-size:34px; font-weight:600; color:#a5aec0;",
        )
        self._cap = QLabel(self.tr("综合重复度"))
        self._cap.setObjectName("muted")
        ring_box.addWidget(self.overall_label)
        ring_box.addWidget(self._cap)
        top.addLayout(ring_box)

        dims_box = QVBoxLayout()
        self.dim_comp = _DimBar(self.tr("构图"))
        self.dim_motion = _DimBar(self.tr("运镜"))
        self.dim_rhythm = _DimBar(self.tr("节奏"))
        for bar in (self.dim_comp, self.dim_motion, self.dim_rhythm):
            dims_box.addWidget(bar)
        top.addLayout(dims_box, 1)
        root.addLayout(top)

        self.compare_label = QLabel(self.tr("处理前 → 处理后：—"))
        root.addWidget(self.compare_label)

        self.note_label = QLabel()
        self.note_label.setObjectName("muted")
        self.note_label.setWordWrap(True)
        self.note_label.hide()
        root.addWidget(self.note_label)

        self.table = QTableWidget(0, 5)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        self.table.setColumnWidth(0, 70)
        self.table.setColumnWidth(1, 90)
        self.table.setColumnWidth(2, 320)
        self.table.setColumnWidth(3, 80)
        self._set_headers()
        root.addWidget(self.table, 1)

    def _set_headers(self) -> None:
        self.table.setHorizontalHeaderLabels([
            self.tr("来源"), self.tr("平台"), self.tr("标题"),
            self.tr("相似度%"), self.tr("状态"),
        ])

    def retranslate(self) -> None:
        """语言切换：静态标签/表头重翻译；有历史报告则整体重渲染。"""
        self._cap.setText(self.tr("综合重复度"))
        self.dim_comp.set_src_title(self.tr("构图"))
        self.dim_motion.set_src_title(self.tr("运镜"))
        self.dim_rhythm.set_src_title(self.tr("节奏"))
        self._set_headers()
        report, before_pct, after_pct = self._last
        self.show_report(report, before_pct, after_pct)

    # ---- 渲染 ----
    def show_report(
        self,
        report: CompareReport | None,
        before_pct: float | None = None,
        after_pct: float | None = None,
    ) -> None:
        self._last = (report, before_pct, after_pct)
        if report is None:
            self.overall_label.setText("—%")
            self.overall_label.setStyleSheet(
                "font-size:34px; font-weight:600; color:#a5aec0;",
            )
            self.table.setRowCount(0)
            self.compare_label.setText(self.tr("处理前 → 处理后：—"))
            self.note_label.hide()
            return

        scored = [t for t in report.targets
                  if t.scores is not None and t.status == "ok"]
        unavailable = list(report.unavailable_platforms or [])
        if not scored:
            # 无有效对比对象：0 分会误导（0 分 ≠ 内容原创），明示原因
            self.overall_label.setText("—%")
            self.overall_label.setStyleSheet(
                "font-size:34px; font-weight:600; color:#a5aec0;",
            )
            self.dim_comp.bar.setValue(0)
            self.dim_motion.bar.setValue(0)
            self.dim_rhythm.bar.setValue(0)
            if unavailable and "no_keyword" not in unavailable:
                self.note_label.setText(self.tr(
                    "未找到可比对的视频：在线平台 {p} 均不可用，本地素材库中"
                    "也没有同关键词的其它素材。此处的 0 分不代表重复度低。",
                ).format(p="、".join(unavailable)))
            else:
                self.note_label.setText(self.tr(
                    "未找到可比对的视频：素材不在工作目录归档结构中"
                    "（需要 大类/关键词/日期/ 文件路径），且没有在线平台可用。"
                    "此处的 0 分不代表重复度低。",
                ))
            self.note_label.show()
            self.table.setRowCount(0)
            self.compare_label.setText(self.tr("处理前 → 处理后：—"))
            return

        best_dims = report.dims
        score = float(report.overall_score)
        self.overall_label.setText(f"{score:.1f}%")
        self.overall_label.setStyleSheet(
            f"font-size:34px; font-weight:600; color:{_risk_color(score)};",
        )
        self.dim_comp.set_pct(best_dims.composition)
        self.dim_motion.set_pct(best_dims.motion)
        self.dim_rhythm.set_pct(best_dims.rhythm)

        if before_pct is not None or after_pct is not None:
            self.compare_label.setText(self.tr(
                "处理前 → 处理后：{a} → {b}",
            ).format(a=_pct(before_pct), b=_pct(after_pct)))
        else:
            self.compare_label.setText(self.tr("处理前 → 处理后：—"))

        n_local = sum(1 for t in scored if t.source == "local")
        if unavailable and "no_keyword" not in unavailable:
            self.note_label.setText(self.tr(
                "在线平台 {p} 不可用，以上结果基于其余 {n} 个对比对象"
                "（其中本地素材库 {m} 个）。",
            ).format(p="、".join(unavailable), n=len(scored), m=n_local))
            self.note_label.show()
        else:
            self.note_label.hide()

        self.table.setRowCount(len(report.targets))
        for i, t in enumerate(report.targets):
            source = _source_label(t.source)
            values = [
                source,
                _platform_label(t.platform_id) if t.platform_id else "-",
                t.title or t.video_key or "-",
                (f"{t.scores.overall * 100:.1f}"
                 if t.scores is not None else "-"),
                t.status_reason or t.status,
            ]
            for col, v in enumerate(values):
                self.table.setItem(i, col, QTableWidgetItem(str(v)))
