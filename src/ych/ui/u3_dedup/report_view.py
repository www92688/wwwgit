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

    def set_pct(self, value: float) -> None:
        pct = max(0.0, min(value, 1.0)) * 100.0
        self.bar.setValue(int(pct))
        color = _risk_color(pct)
        self.bar.setStyleSheet(f"QProgressBar::chunk {{ background: {color}; }}")


def _pct(v: float | None) -> str:
    return f"{v:.1f}%" if isinstance(v, (int, float)) else "—"


class ReportView(QWidget):
    """render(report, before_pct=None, after_pct=None)。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        top = QHBoxLayout()

        ring_box = QVBoxLayout()
        self.overall_label = QLabel("—%")
        self.overall_label.setStyleSheet(
            "font-size:34px; font-weight:600; color:#a5aec0;",
        )
        cap = QLabel("综合重复度")
        cap.setObjectName("muted")
        ring_box.addWidget(self.overall_label)
        ring_box.addWidget(cap)
        top.addLayout(ring_box)

        dims_box = QVBoxLayout()
        self.dim_comp = _DimBar("构图")
        self.dim_motion = _DimBar("运镜")
        self.dim_rhythm = _DimBar("节奏")
        for bar in (self.dim_comp, self.dim_motion, self.dim_rhythm):
            dims_box.addWidget(bar)
        top.addLayout(dims_box, 1)
        root.addLayout(top)

        self.compare_label = QLabel("处理前 → 处理后：—")
        root.addWidget(self.compare_label)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["来源", "平台", "标题", "相似度%", "状态"]
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        self.table.setColumnWidth(0, 70)
        self.table.setColumnWidth(1, 90)
        self.table.setColumnWidth(2, 320)
        self.table.setColumnWidth(3, 80)
        root.addWidget(self.table, 1)

    # ---- 渲染 ----
    def show_report(
        self,
        report: CompareReport | None,
        before_pct: float | None = None,
        after_pct: float | None = None,
    ) -> None:
        if report is None:
            self.overall_label.setText("—%")
            self.overall_label.setStyleSheet(
                "font-size:34px; font-weight:600; color:#a5aec0;",
            )
            self.table.setRowCount(0)
            self.compare_label.setText("处理前 → 处理后：—")
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
            self.compare_label.setText(
                f"处理前 → 处理后：{_pct(before_pct)} → {_pct(after_pct)}"
            )
        else:
            self.compare_label.setText("处理前 → 处理后：—")

        self.table.setRowCount(len(report.targets))
        for i, t in enumerate(report.targets):
            source = "手动" if t.source == "manual" else "自动"
            values = [
                source,
                t.platform_id or "-",
                t.title or t.video_key or "-",
                (f"{t.scores.overall * 100:.1f}"
                 if t.scores is not None else "-"),
                t.status_reason or t.status,
            ]
            for col, v in enumerate(values):
                self.table.setItem(i, col, QTableWidgetItem(str(v)))
