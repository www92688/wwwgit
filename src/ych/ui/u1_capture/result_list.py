# 结果列表（缩略图卡片：缩略图+时长+画质+大小；勾选批量下载）
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ych.common.schemas import VideoMeta


def _fmt_size(size: int | None) -> str:
    if not size:
        return "大小未知"
    if size >= 1024 * 1024:
        return f"{size / 1048576:.1f}MB"
    return f"{size / 1024:.0f}KB"


class ResultList(QWidget):
    """搜索结果卡片列表；download_requested(选中 metas, keyword)。"""

    download_requested = Signal(list, str)      # list[VideoMeta], keyword

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        self.keyword = ""

        top = QHBoxLayout()
        self.select_all = QCheckBox("全选")
        btn_download = QPushButton("下载选中")
        btn_download.clicked.connect(self._emit_download)
        top.addWidget(self.select_all)
        top.addStretch(1)
        top.addWidget(btn_download)
        root.addLayout(top)

        self.list = QListWidget()
        root.addWidget(self.list, 1)

        self.select_all.toggled.connect(self._toggle_all)

    # ---- 数据 ----
    def set_results(self, metas: list[VideoMeta], keyword: str,
                    unavailable: list[tuple[str, str]] | None = None) -> None:
        self.list.clear()
        self.keyword = keyword
        for meta in metas:
            item = QListWidgetItem(self._card_text(meta))
            item.setData(Qt.ItemDataRole.UserRole, meta)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.list.addItem(item)
        if unavailable:
            note = QListWidgetItem(
                "暂不可用平台：" + "、".join(pid for pid, _r in unavailable)
            )
            note.setFlags(note.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            self.list.addItem(note)

    @staticmethod
    def _card_text(meta: VideoMeta) -> str:
        title = meta.title or meta.video_key
        quality = f"{meta.width}x{meta.height}" if meta.height else ""
        watermark = "无水印" if meta.watermark_tag == "no" else (
            "有水印" if meta.watermark_tag == "yes" else "")
        parts = [f"{meta.plugin_id} · {title}",
                 f"时长 {meta.duration_s:.0f}s",
                 quality,
                 _fmt_size(meta.file_size_bytes)]
        if watermark:
            parts.append(watermark)
        return "   |   ".join(p for p in parts if p)

    def checked_metas(self) -> list[VideoMeta]:
        out: list[VideoMeta] = []
        for i in range(self.list.count()):
            item = self.list.item(i)
            data = item.data(Qt.ItemDataRole.UserRole)
            if data is not None and item.checkState() == Qt.CheckState.Checked:
                assert isinstance(data, VideoMeta)
                out.append(data)
        return out

    # ---- 槽 ----
    def _toggle_all(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                item.setCheckState(state)

    def _emit_download(self) -> None:
        metas = self.checked_metas()
        if metas:
            self.download_requested.emit(metas, self.keyword)
