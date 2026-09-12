# 五处理项面板（U2）→ PreprocessOps
from __future__ import annotations

from typing import Any

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
    """去水印/去字幕 三态（off/auto/manual）+ 裁剪 + 比例 + 去原声。

    传入 config 时：选项变化即时持久化（preprocess_options），
    重建时恢复上次使用值——批量处理同类素材无需每次重设。
    """

    clear_requested = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        config: Any | None = None,      # ConfigService（可选）
    ) -> None:
        super().__init__(self.tr("处理项"), parent)
        self._config = config
        self._restoring = False
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
        self._crop_spins = (self.crop_x, self.crop_y, self.crop_w, self.crop_h)
        for spin in self._crop_spins:
            spin.setRange(0.0, 1.0)
            spin.setSingleStep(0.05)
        crop_form = QFormLayout()
        crop_form.setContentsMargins(0, 0, 0, 0)
        crop_form.addRow(self.crop_enabled)
        crop_form.addRow("裁剪框 X / Y", self._pair(self.crop_x, self.crop_y))
        crop_form.addRow("裁剪框 宽 / 高", self._pair(self.crop_w, self.crop_h))
        form.addRow(crop_form)
        # 未启用裁剪时禁用数值框（避免"改了却无效"的困惑）
        self.crop_enabled.toggled.connect(self._sync_crop_enabled)
        self._sync_crop_enabled()

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

        # ---- 选项记忆：恢复上次值 + 变化即时保存 ----
        if config is not None:
            self._restoring = True
            self._restore()
            self._restoring = False
            self.wm_mode.currentIndexChanged.connect(self._save)
            self.sub_mode.currentIndexChanged.connect(self._save)
            self.crop_enabled.toggled.connect(self._save)
            for spin in self._crop_spins:
                spin.valueChanged.connect(self._save)
            self.aspect.currentIndexChanged.connect(self._save)
            self.aspect_strategy.currentIndexChanged.connect(self._save)
            self.strip_audio.toggled.connect(self._save)

    # ---- 选项记忆 ----
    def _snapshot(self) -> dict[str, object]:
        target = self.aspect.currentData()
        return {
            "wm_mode": self.wm_mode.currentData(),
            "sub_mode": self.sub_mode.currentData(),
            "crop_enabled": self.crop_enabled.isChecked(),
            "crop": [float(s.value()) for s in self._crop_spins],
            "aspect": (f"{target[0]}x{target[1]}"
                       if isinstance(target, tuple) else ""),
            "aspect_strategy": str(self.aspect_strategy.currentData()),
            "strip_audio": self.strip_audio.isChecked(),
        }

    def _restore(self) -> None:
        assert self._config is not None
        try:
            data = self._config.get("preprocess_options")
        except Exception:
            return
        if not isinstance(data, dict) or not data:
            return
        for key, combo in (("wm_mode", self.wm_mode),
                           ("sub_mode", self.sub_mode),
                           ("aspect_strategy", self.aspect_strategy)):
            value = data.get(key)
            if isinstance(value, str):
                idx = combo.findData(value)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
        aspect = data.get("aspect")
        if isinstance(aspect, str) and "x" in aspect:
            try:
                w, h = (int(v) for v in aspect.split("x", 1))
            except ValueError:
                w = h = 0
            if w and h:
                # findData 对元组数据的变体比较不可靠，手动遍历匹配
                for i in range(self.aspect.count()):
                    d = self.aspect.itemData(i)
                    if isinstance(d, tuple) and tuple(d) == (w, h):
                        self.aspect.setCurrentIndex(i)
                        break
        crop = data.get("crop")
        if (isinstance(crop, list) and len(crop) == 4
                and all(isinstance(v, (int, float)) for v in crop)):
            for spin, value in zip(self._crop_spins, crop, strict=True):
                spin.setValue(float(value))
        if isinstance(data.get("crop_enabled"), bool):
            self.crop_enabled.setChecked(data["crop_enabled"])
        self._sync_crop_enabled()
        if isinstance(data.get("strip_audio"), bool):
            self.strip_audio.setChecked(data["strip_audio"])

    def _save(self, *_args: object) -> None:
        if self._restoring or self._config is None:
            return
        self._config.set("preprocess_options", self._snapshot())

    @staticmethod
    def _pair(left: QWidget, right: QWidget) -> QWidget:
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(left)
        row.addWidget(right)
        return box

    def _sync_crop_enabled(self, enabled: bool | None = None) -> None:
        checked = self.crop_enabled.isChecked() if enabled is None else enabled
        for spin in self._crop_spins:
            spin.setEnabled(checked)

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
