# 橡皮筋多区域框选画布（U2）：视频帧截图上框选水印/字幕区域 → 归一化 BBox 列表
from __future__ import annotations

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ych.common.schemas import BBox


class _CanvasLabel(QLabel):
    """内部画布：处理鼠标橡皮筋。"""

    region_added = Signal(object)     # BBox（归一化）

    def __init__(self) -> None:
        super().__init__()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(320, 180)
        self.setStyleSheet("background:#20242b; color:#8a8f98;")
        self._origin = None
        self._current: QRect | None = None
        self.setText("加载素材帧后在此框选区域\n（左键拖拽，可多次框选）")

    def mousePressEvent(self, ev) -> None:   # type: ignore[no-untyped-def]
        if ev.button() == Qt.MouseButton.LeftButton and self.pixmap():
            self._origin = ev.position().toPoint()
            self._current = None

    def mouseMoveEvent(self, ev) -> None:   # type: ignore[no-untyped-def]
        if self._origin is not None:
            self._current = QRect(self._origin, ev.position().toPoint()).normalized()
            self.update()

    def mouseReleaseEvent(self, ev) -> None:   # type: ignore[no-untyped-def]
        if self._origin is not None and self._current is not None:
            rect = self._current
            self._origin = None
            self._current = None
            self.update()
            if rect.width() >= 8 and rect.height() >= 8:
                bbox = self._to_normalized(rect)
                if bbox is not None:
                    self.region_added.emit(bbox)
                    self._draw_persisted(bbox)

    def paintEvent(self, ev) -> None:   # type: ignore[no-untyped-def]
        super().paintEvent(ev)
        if self._current is not None:
            painter = QPainter(self)
            pen = QPen(QColor(55, 66, 250), 2)
            painter.setPen(pen)
            painter.drawRect(self._current)

    # ---- 内部 ----
    def _to_normalized(self, rect: QRect) -> BBox | None:
        pm = self.pixmap()
        if pm is None or pm.width() == 0 or pm.height() == 0:
            return None
        offset_x = max((self.width() - pm.width()) // 2, 0)
        offset_y = max((self.height() - pm.height()) // 2, 0)
        x0 = max(rect.x() - offset_x, 0) / pm.width()
        y0 = max(rect.y() - offset_y, 0) / pm.height()
        # QRect.right()/bottom() 是闭区间末像素（x+w-1），归一化右/下边界需 +1
        x1 = min(rect.right() + 1 - offset_x, pm.width()) / pm.width()
        y1 = min(rect.bottom() + 1 - offset_y, pm.height()) / pm.height()
        w, h = x1 - x0, y1 - y0
        if w <= 0 or h <= 0:
            return None
        return BBox(x=round(x0, 4), y=round(y0, 4), w=round(w, 4), h=round(h, 4))

    def _draw_persisted(self, bbox: BBox) -> None:
        pm = self.pixmap()
        if pm is None:
            return
        painter = QPainter(pm)
        pen = QPen(QColor(255, 159, 67), 2)
        painter.setPen(pen)
        painter.drawRect(
            int(bbox.x * pm.width()), int(bbox.y * pm.height()),
            int(bbox.w * pm.width()), int(bbox.h * pm.height()),
        )
        painter.end()
        self.setPixmap(pm)


class BoxSelectCanvas(QWidget):
    """对外：set_image(path) 加载帧；regions() 返回已框选归一化区域；clear_regions()。"""

    regions_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self._label = _CanvasLabel()
        root.addWidget(self._label)
        # 框选结果先登记进 _boxes（提交时读取），再通知变化
        self._label.region_added.connect(self.register_region)
        self._label.region_added.connect(lambda _b: self.regions_changed.emit())
        self._boxes: list[BBox] = []

    def set_image(self, image_path: str | None) -> None:
        if not image_path:
            return
        pm = QPixmap(image_path)
        if not pm.isNull():
            scaled = pm.scaled(
                self._label.width(), self._label.height(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._boxes.clear()
            self._label.setPixmap(scaled)
            self.regions_changed.emit()

    def regions(self) -> list[BBox]:
        return list(self._boxes)

    def clear_regions(self) -> None:
        self._boxes.clear()
        self.regions_changed.emit()

    def register_region(self, bbox: BBox) -> None:
        self._boxes.append(bbox)
