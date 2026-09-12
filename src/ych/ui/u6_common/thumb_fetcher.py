# 搜索结果缩略图异步加载（QNAM + 磁盘缓存；失败静默保持占位图）
from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtGui import QImage
from PySide6.QtNetwork import (
    QNetworkAccessManager,
    QNetworkReply,
    QNetworkRequest,
)

_CACHE_DIR = Path(tempfile.gettempdir()) / "YuChongGou" / "thumbs"
_TIMEOUT_MS = 8000


def thumb_key(url: str) -> str:
    """缩略图缓存键：URL 的 sha1（跨搜索稳定去重）。"""
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


class ThumbFetcher(QObject):
    """fetch(key, url) → 完成后发 fetched(key, image)；磁盘缓存命中不发网络。"""

    fetched = Signal(str, QImage)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._nam = QNetworkAccessManager(self)
        self._nam.finished.connect(self._on_finished)
        self._pending: dict[QNetworkReply, str] = {}
        self._requested: set[str] = set()

    def fetch(self, key: str, url: str) -> bool:
        """请求缩略图；空 url 或已请求过的 key 返回 False。"""
        if not url or key in self._requested:
            return False
        self._requested.add(key)
        cached = _CACHE_DIR / f"{key}.jpg"
        if cached.exists():
            img = QImage(str(cached))
            if not img.isNull():
                self.fetched.emit(key, img)
                return True
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        req = QNetworkRequest(QUrl(url))
        req.setTransferTimeout(_TIMEOUT_MS)
        reply = self._nam.get(req)
        self._pending[reply] = key
        return True

    def _on_finished(self, reply: QNetworkReply) -> None:
        key = self._pending.pop(reply, None)
        try:
            if key is None:
                return
            # 超时/断网等失败：静默，占位图保持原样
            if reply.error() != QNetworkReply.NetworkError.NoError:
                return
            img = QImage()
            if not img.loadFromData(reply.readAll()):
                return
            img.save(str(_CACHE_DIR / f"{key}.jpg"), b"JPG")
            self.fetched.emit(key, img)
        finally:
            reply.deleteLater()
