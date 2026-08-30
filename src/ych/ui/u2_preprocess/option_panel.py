# 五处理项面板（U2）→ PreprocessOps
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QPushButton,
    QWidget,
)

from ych.common.schemas import BBox, ManualRegions
from ych.core.m2_preprocess.ops import PreprocessOps


class OptionPanel(QGroupBox):
    """去水印/去字幕 三态（off/auto/manual）+ 裁剪 + 比例 + 去原声。"""

    clear_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(self.tr("处理项"), parent)
        form = QFormLayout(self)

        self.wm_mode = QComboBox()
        for label, val in (("关闭", "off"), ("自动检测", "auto"),
                           ("手动框选", "manual")):
            self.wm_mode.addItem(label, val)
        form.addRow("去水印", self.wm_mode)

        self.sub_mode = QComboBox()
        for label, val in (("关闭", "off"), ("自动检测", "auto"),
                           ("手动框选", "manual")):
            self.sub_mode.addItem(label, val)
        form.addRow("去字幕", self.sub_mode)

        self.crop_enabled = QCheckBox("启用裁剪")
        self.crop_x = QDoubleSpinBox()
        self.crop_y = QDoubleSpinBox()
        self.crop_w = QDoubleSpinBox()
        self.crop_h = QDoubleSpinBox()
        for spin in (self.crop_x, self.crop_y, self.crop_w, self.crop_h):
            spin.setRange(0.0, 1.0)
            spin.setSingleStep(0.05)
        crop_form = QFormLayout()
        crop_form.setContentsMargins(0, 0, 0, 0)
        crop_form.addRow(self.crop_enabled)
        crop_form.addRow("裁剪框 X / Y", self._pair(self.crop_x, self.crop_y))
        crop_form.addRow("裁剪框 宽 / 高", self._pair(self.crop_w, self.crop_h))
        form.addRow(crop_form)

        self.aspect = QComboBox()
        self.aspect.addItem("不调整", None)
        self.aspect.addItem("9:16 竖屏", (9, 16))
        self.aspect.addItem("16:9 横屏", (16, 9))
        self.aspect.addItem("1:1 方形", (1, 1))
        self.aspect_strategy = QComboBox()
        self.aspect_strategy.addItem("裁切", "crop")
        self.aspect_strategy.addItem("黑边", "pad")
        form.addRow("目标比例 / 策略",
                    self._pair(self.aspect, self.aspect_strategy))

        self.strip_audio = QCheckBox("去除原声")
        form.addRow("", self.strip_audio)

        btn_clear = QPushButton(self.tr("清空手动框选"))
        btn_clear.setObjectName("secondaryBtn")
        btn_clear.clicked.connect(self.clear_requested.emit)
        form.addRow("", btn_clear)

    @staticmethod
    def _pair(left: QWidget, right: QWidget) -> QWidget:
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(left)
        row.addWidget(right)
        return box

    # ---- 输出 ----
    def to_ops(self, manual_regions: ManualRegions | None = None) -> PreprocessOps:
        wm = str(self.wm_mode.currentData())
        sub = str(self.sub_mode.currentData())
        regions = manual_regions if (wm == "manual" or sub == "manual") else None
        ops = PreprocessOps(
            remove_watermark_mode=wm,          # type: ignore[arg-type]
            watermark_regions=regions if wm == "manual" else None,
            remove_subtitle_mode=sub,          # type: ignore[arg-type]
            subtitle_regions=regions if sub == "manual" else None,
            strip_audio=self.strip_audio.isChecked(),
        )
        if self.crop_enabled.isChecked():
            ops.crop_rect = BBox(
                x=float(self.crop_x.value()), y=float(self.crop_y.value()),
                w=max(float(self.crop_w.value()), 0.05),
                h=max(float(self.crop_h.value()), 0.05),
            )
        target = self.aspect.currentData()
        if isinstance(target, tuple):
            ops.aspect_target = (int(target[0]), int(target[1]))
            ops.aspect_strategy = str(self.aspect_strategy.currentData())  # type: ignore[assignment]
        return ops

    @staticmethod
    def manual_from_boxes(boxes: list[BBox]) -> ManualRegions:
        return ManualRegions(rects=list(boxes))
