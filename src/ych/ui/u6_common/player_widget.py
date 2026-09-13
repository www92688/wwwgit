# 视频预览组件（QMediaPlayer；无解码后端时优雅降级为占位）
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class PlayerWidget(QWidget):
    """本地视频预览：优先 QMediaPlayer，初始化失败退化为文本占位。

    播放安全：文件不存在不进解码器（此前黑屏无反馈）；解码错误经
    playback_error 信号上报，宿主可据此 Toast；无后端时占位文本可见。
    """

    playback_error = Signal(str)     # 播放失败原因（宿主负责提示用户）

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._fallback = QLabel(self.tr("预览不可用"))
        self._fallback.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._fallback.setWordWrap(True)
        self._video = None
        self._player = None
        layout = QVBoxLayout(self)
        try:
            from PySide6.QtMultimedia import QMediaPlayer
            from PySide6.QtMultimediaWidgets import QVideoWidget

            self._video = QVideoWidget()
            self._player = QMediaPlayer()
            self._player.setVideoOutput(self._video)
            self._player.errorOccurred.connect(self._on_media_error)
            layout.addWidget(self._video)
        except Exception:
            layout.addWidget(self._fallback)

    def retranslate(self) -> None:
        self._fallback.setText(self.tr("预览不可用"))

    def play(self, path: str | None) -> None:
        """播放本地文件；path 为空即停止。"""
        if not path:
            self.stop()
            return
        if self._player is None:
            self._fallback.setText(str(path))
            return
        if not Path(path).is_file():
            # 不存在的文件送进解码器只会黑屏：给出可见反馈并上报
            self._player.stop()
            self._fallback.setText(self.tr("文件不存在：{path}").format(path=path))
            self.playback_error.emit(str(path))
            return
        self._player.setSource(QUrl.fromLocalFile(str(path)))
        self._player.play()

    def stop(self) -> None:
        if self._player is not None:
            self._player.stop()

    def _on_media_error(
        self, _err: object, err_str: str,
    ) -> None:
        """解码/容器错误：上报宿主（Toast），无后端时占位文本同步更新。"""
        self.playback_error.emit(err_str)
