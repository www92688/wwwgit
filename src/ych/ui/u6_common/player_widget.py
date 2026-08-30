# 视频预览组件（QMediaPlayer；无解码后端时优雅降级为占位）
from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class PlayerWidget(QWidget):
    """本地视频预览：优先 QMediaPlayer，初始化失败退化为文本占位。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._fallback = QLabel("预览不可用")
        self._fallback.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._video = None
        self._player = None
        layout = QVBoxLayout(self)
        try:
            from PySide6.QtMultimedia import QMediaPlayer
            from PySide6.QtMultimediaWidgets import QVideoWidget

            self._video = QVideoWidget()
            self._player = QMediaPlayer()
            self._player.setVideoOutput(self._video)
            layout.addWidget(self._video)
        except Exception:
            layout.addWidget(self._fallback)

    def play(self, path: str | None) -> None:
        """播放本地文件；path 为空即停止。"""
        if not path:
            self.stop()
            return
        if self._player is None:
            self._fallback.setText(str(path))
            return
        self._player.setSource(QUrl.fromLocalFile(str(path)))
        self._player.play()

    def stop(self) -> None:
        if self._player is not None:
            self._player.stop()
