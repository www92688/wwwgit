# 筛选面板（时长/画质/大小/水印四件套）→ SearchFilters
from __future__ import annotations

from typing import cast

from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QWidget,
)

from ych.common.schemas import SearchFilters, WatermarkTag

_QUALITY_ITEMS: list[tuple[str, int]] = [
    ("原始画质", 0), ("720p 及以上", 720), ("1080p 及以上", 1080),
]
_WATERMARK_ITEMS: list[tuple[str, WatermarkTag]] = [
    ("不限制", "unknown"), ("无水印", "no"), ("有水印", "yes"),
]


class FilterPanel(QGroupBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("筛选条件", parent)
        form = QFormLayout(self)

        self.dur_min = QDoubleSpinBox()
        self.dur_min.setRange(0.0, 36000.0)
        self.dur_min.setValue(0.0)
        self.dur_min.setSuffix(" s")
        self.dur_max = QDoubleSpinBox()
        self.dur_max.setRange(1.0, 36000.0)
        self.dur_max.setValue(180.0)
        self.dur_max.setSuffix(" s")
        form.addRow("时长范围", self._row(self.dur_min, self.dur_max))

        self.quality = QComboBox()
        for q_label, q_value in _QUALITY_ITEMS:
            self.quality.addItem(q_label, q_value)
        form.addRow("画质要求", self.quality)

        self.size_min = QDoubleSpinBox()
        self.size_min.setRange(0.0, 100000.0)
        self.size_min.setSuffix(" MB")
        self.size_max = QDoubleSpinBox()
        self.size_max.setRange(0.5, 100000.0)
        self.size_max.setValue(500.0)
        self.size_max.setSuffix(" MB")
        form.addRow("文件大小", self._row(self.size_min, self.size_max))

        self.watermark = QComboBox()
        for w_label, w_value in _WATERMARK_ITEMS:
            self.watermark.addItem(w_label, w_value)
        form.addRow("水印情况", self.watermark)

    @staticmethod
    def _row(left: QWidget, right: QWidget) -> QWidget:
        from PySide6.QtWidgets import QHBoxLayout

        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(left)
        row.addWidget(right)
        return box

    def to_filters(self) -> SearchFilters:
        quality = self.quality.currentData()
        watermark = self.watermark.currentData()
        wm_tag = cast(WatermarkTag, watermark if isinstance(watermark, str) else "unknown")
        return SearchFilters(
            duration_min_s=float(self.dur_min.value()),
            duration_max_s=float(self.dur_max.value()),
            min_height=int(quality) if isinstance(quality, int) else 0,
            size_min_mb=float(self.size_min.value()),
            size_max_mb=float(self.size_max.value()),
            watermark=wm_tag,
        )
