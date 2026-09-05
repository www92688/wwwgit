# 生成应用图标 resources/app.ico（重复执行覆盖）
# 用法：python scripts/make_icon.py
# 说明：Qt 离屏绘制 256x256 PNG，再手工包一层 ICO 容器
#     （Vista+ 支持 PNG 压缩条目，Explorer/快捷方式均可渲染）。
from __future__ import annotations

import os
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "resources" / "app.ico"

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QIODevice, Qt  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QColor,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)

SIZE = 256


def draw() -> QImage:
    img = QImage(SIZE, SIZE, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    # 圆角底板：靛蓝渐变（与应用主题按钮同色系）
    grad = QLinearGradient(0.0, 0.0, 0.0, float(SIZE))
    grad.setColorAt(0.0, QColor("#6366F1"))
    grad.setColorAt(1.0, QColor("#4338CA"))
    p.setPen(QPen(Qt.PenStyle.NoPen))
    p.setBrush(grad)
    p.drawRoundedRect(8, 8, SIZE - 16, SIZE - 16, 56, 56)
    # 白色下载箭头（竖杆 + 三角头）+ 底部托盘：对应“采集下载素材”
    p.setBrush(QColor("#FFFFFF"))
    cx = SIZE // 2
    p.drawRoundedRect(cx - 14, 58, 28, 66, 13, 13)
    head = QPainterPath()
    head.moveTo(cx - 50, 118)
    head.lineTo(cx + 50, 118)
    head.lineTo(cx, 170)
    head.closeSubpath()
    p.drawPath(head)
    p.setPen(QPen(QColor("#FFFFFF"), 18, Qt.PenStyle.SolidLine,
                  Qt.PenCapStyle.RoundCap))
    p.drawLine(76, 196, SIZE - 76, 196)
    p.end()
    return img


def to_ico(img: QImage) -> bytes:
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    png = bytes(buf.data())
    # ICO 头(6) + 目录项(16)：宽/高字节 0 表示 256
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(png), 22)
    return header + entry + png


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(to_ico(draw()))
    print(f"icon written: {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
