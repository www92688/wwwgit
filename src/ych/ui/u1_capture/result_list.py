# 结果列表（缩略图卡片：缩略图+时长+画质+大小；勾选批量下载）
from __future__ import annotations

from typing import Any

from PySide6.QtCore import QCoreApplication, QPointF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QIcon,
    QImage,
    QPainter,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ych.common.schemas import VideoMeta
from ych.ui.u6_common.context_actions import copy_to_clipboard
from ych.ui.u6_common.empty_state import attach_empty_state
from ych.ui.u6_common.platform_labels import platform_label
from ych.ui.u6_common.thumb_fetcher import ThumbFetcher, thumb_key
from ych.ui.u6_common.toast import Toast

_THUMB_ROLE = int(Qt.ItemDataRole.UserRole + 1)   # item → 缩略图缓存键
_THUMB_SIZE = QSize(76, 46)


def _placeholder_icon() -> QIcon:
    """占位缩略图：半透明中性圆角块 + 播放三角（深浅主题通用）。"""
    pm = QPixmap(_THUMB_SIZE)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(127, 140, 170, 52))
    p.drawRoundedRect(pm.rect(), 6, 6)
    p.setBrush(QColor(127, 140, 170, 150))
    cx, cy, r = _THUMB_SIZE.width() / 2, _THUMB_SIZE.height() / 2, 9
    p.drawPolygon(QPolygonF([
        QPointF(cx - r * 0.6, cy - r),
        QPointF(cx - r * 0.6, cy + r),
        QPointF(cx + r, cy),
    ]))
    p.end()
    return QIcon(pm)


def _fmt_size(size: int | None) -> str:
    if not size:
        return QCoreApplication.translate("ResultList", "大小未知")
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
        self.select_all = QCheckBox(self.tr("全选"))
        self.btn_download = QPushButton(self.tr("下载选中"))
        self.btn_download.setEnabled(False)
        self.btn_download.clicked.connect(self._emit_download)
        top.addWidget(self.select_all)
        top.addStretch(1)
        top.addWidget(self.btn_download)
        root.addLayout(top)

        self.list = QListWidget()
        self.list.setIconSize(_THUMB_SIZE)
        self._thumbs = ThumbFetcher(self)
        self._thumbs.fetched.connect(self._on_thumb_fetched)
        self._empty = attach_empty_state(
            self.list, self.tr("还没有搜索结果"),
            self.tr("在顶部输入关键词，点击「搜索」试试"),
        )
        root.addWidget(self.list, 1)

        self.select_all.toggled.connect(self._toggle_all)
        self.list.itemChanged.connect(lambda _item: self._refresh_footer())
        self.list.itemDoubleClicked.connect(self._open_source_page)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._show_item_menu)

    # ---- 数据 ----
    def set_results(self, metas: list[VideoMeta], keyword: str,
                    unavailable: list[tuple[str, str]] | None = None) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        self.list.blockSignals(False)
        self.keyword = keyword
        for meta in metas:
            self.list.addItem(self._make_item(meta))
        self._add_unavailable_note(unavailable)
        self._refresh_footer()
        self._refresh_empty()

    def clear_results(self) -> None:
        """新一轮搜索开始前清空上一轮结果。"""
        self.list.blockSignals(True)
        self.list.clear()
        self.list.blockSignals(False)
        self.keyword = ""
        self._refresh_footer()
        self._refresh_empty()

    def append_results(self, metas: list[VideoMeta], keyword: str,
                       unavailable: list[tuple[str, str]] | None = None) -> None:
        """批量搜索逐关键词追加（不 clear）；暂不可用提示合并去重。"""
        self.keyword = keyword
        for meta in metas:
            self.list.addItem(self._make_item(meta))
        self._add_unavailable_note(unavailable)
        self._refresh_footer()
        self._refresh_empty()

    def _make_item(self, meta: VideoMeta) -> QListWidgetItem:
        item = QListWidgetItem(self._card_text(meta))
        item.setData(Qt.ItemDataRole.UserRole, meta)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked)
        item.setSizeHint(QSize(0, 54))
        item.setToolTip(
            f"{meta.title or meta.video_key}\n"
            f"{self.tr('双击打开素材来源页')}",
        )
        item.setIcon(_placeholder_icon())
        if meta.thumbnail_url:
            key = thumb_key(meta.thumbnail_url)
            item.setData(_THUMB_ROLE, key)
            self._thumbs.fetch(key, meta.thumbnail_url)
        return item

    def _on_thumb_fetched(self, key: str, image: object) -> None:
        """后台缩略图到达：按缓存键回填对应卡片图标。"""
        assert isinstance(image, QImage)
        icon = QIcon(
            image.scaled(
                _THUMB_SIZE, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ),
        )
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.data(_THUMB_ROLE) == key:
                item.setIcon(icon)

    def _add_unavailable_note(
        self, unavailable: list[tuple[str, str]] | None,
    ) -> None:
        if not unavailable:
            return
        note = QListWidgetItem(
            self.tr("暂不可用平台：{}").format(
                "、".join(pid for pid, _r in unavailable),
            ),
        )
        note.setFlags(
            note.flags()
            & ~Qt.ItemFlag.ItemIsUserCheckable
            & ~Qt.ItemFlag.ItemIsSelectable
        )
        note.setForeground(QColor("#9aa3b2"))
        self.list.addItem(note)

    def _card_text(self, meta: VideoMeta) -> str:
        title = meta.title or meta.video_key
        quality = f"{meta.width}x{meta.height}" if meta.height else ""
        watermark = (
            self.tr("无水印") if meta.watermark_tag == "no"
            else self.tr("有水印") if meta.watermark_tag == "yes" else ""
        )
        detail = " · ".join(
            p for p in (platform_label(meta.plugin_id),
                        self.tr("时长 {d}s").format(d=f"{meta.duration_s:.0f}"),
                        quality, _fmt_size(meta.file_size_bytes), watermark)
            if p
        )
        return f"{title}\n{detail}"

    def checked_metas(self) -> list[VideoMeta]:
        out: list[VideoMeta] = []
        for i in range(self.list.count()):
            item = self.list.item(i)
            data = item.data(Qt.ItemDataRole.UserRole)
            if data is not None and item.checkState() == Qt.CheckState.Checked:
                assert isinstance(data, VideoMeta)
                out.append(data)
        return out

    # ---- 内部刷新 ----
    def _refresh_footer(self) -> None:
        """下载按钮：选中计数 + 无选中时禁用。"""
        n = len(self.checked_metas())
        self.btn_download.setText(
            self.tr("下载选中（{}）").format(n) if n else self.tr("下载选中"),
        )
        self.btn_download.setEnabled(n > 0)
        if self.select_all.signalsBlocked():
            return
        self.select_all.blockSignals(True)
        self.select_all.setChecked(
            n > 0 and n == self._checkable_count(),
        )
        self.select_all.blockSignals(False)

    def _checkable_count(self) -> int:
        return sum(
            1
            for i in range(self.list.count())
            if self.list.item(i).flags() & Qt.ItemFlag.ItemIsUserCheckable
        )

    def _refresh_empty(self) -> None:
        refresh = getattr(self.list, "_refresh_empty_state", None)
        if refresh is not None:
            refresh()

    def _show_item_menu(self, pos: Any) -> None:
        """右键卡片：打开来源页 / 复制下载链接（无对应链接的项置灰）。"""
        item = self.list.itemAt(pos)
        data = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if not isinstance(data, VideoMeta):
            return
        from PySide6.QtCore import QUrl

        menu = QMenu(self)
        act_open = menu.addAction(self.tr("打开来源页"))
        act_open.setEnabled(bool(data.page_url))
        act_copy = menu.addAction(self.tr("复制下载链接"))
        act_copy.setEnabled(bool(data.download_url))
        chosen = menu.exec(self.list.viewport().mapToGlobal(pos))
        if chosen is act_open and data.page_url:
            QDesktopServices.openUrl(QUrl(data.page_url))
        elif chosen is act_copy and data.download_url:
            copy_to_clipboard(data.download_url)

    # ---- 槽 ----
    def _open_source_page(self, item: QListWidgetItem) -> None:
        """双击卡片：浏览器打开素材来源页（无 page_url 时给出提示）。"""
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        data = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(data, VideoMeta):
            return
        if not data.page_url:
            Toast.show_message(self, self.tr("该素材没有来源页链接"))
            return
        QDesktopServices.openUrl(QUrl(data.page_url))

    def _toggle_all(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                item.setCheckState(state)

    def _emit_download(self) -> None:
        metas = self.checked_metas()
        if not metas:
            Toast.show_message(self, self.tr("请先勾选要下载的结果"))
            return
        self.download_requested.emit(metas, self.keyword)

    def retranslate(self) -> None:
        """语言切换：静态文案 + 已有卡片文本/Tooltip 重翻译。"""
        self.select_all.setText(self.tr("全选"))
        self._refresh_footer()
        self._empty.set_texts(
            self.tr("还没有搜索结果"),
            self.tr("在顶部输入关键词，点击「搜索」试试"),
        )
        for i in range(self.list.count()):
            item = self.list.item(i)
            data = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(data, VideoMeta):
                item.setText(self._card_text(data))
                item.setToolTip(
                    f"{data.title or data.video_key}\n"
                    f"{self.tr('双击打开素材来源页')}",
                )
