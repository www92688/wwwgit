# 通用 LLM 调用工作线程：同步网关 API 放后台执行，结果/错误经信号回 UI
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QThread, Signal


class LlmWorker(QThread):
    """执行 task() 并发 done/failed；调用方持有引用并在回调里 deleteLater。"""

    done = Signal(object)          # task() 的返回值
    failed = Signal(str)           # 错误摘要（含错误码）

    def __init__(self, task: Callable[[], object]) -> None:
        super().__init__()
        self._task = task

    def run(self) -> None:
        try:
            self.done.emit(self._task())
        except Exception as exc:  # 后台线程兜底：任何异常转信号
            self.failed.emit(str(exc))
