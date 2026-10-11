# 通用 LLM 调用工作线程：同步网关 API 放后台执行，结果/错误经信号回 UI
from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import QThread, Signal

logger = logging.getLogger("ych.ui")

# 运行中的 worker 全局强引用：持有方（页面）销毁/GC 时，线程对象绝不中途
# 被回收——"QThread: Destroyed while thread is still running" abort 的根源
# 就是引用全失。结束后经 finished 统一安全销毁并移出注册表。
_ACTIVE: set[LlmWorker] = set()


class LlmWorker(QThread):
    """执行 task() 并发 done/failed；调用方持有引用（防 GC 由本类兜底）。"""

    done = Signal(object)          # task() 的返回值
    failed = Signal(str)           # 错误摘要（含错误码）

    def __init__(self, task: Callable[[], object]) -> None:
        super().__init__()
        self._task = task
        _ACTIVE.add(self)
        self.finished.connect(self.deleteLater)
        self.finished.connect(self._unregister)

    def _unregister(self) -> None:
        _ACTIVE.discard(self)

    def run(self) -> None:
        try:
            self.done.emit(self._task())
        except Exception as exc:  # 后台线程兜底：任何异常转信号
            self.failed.emit(str(exc))


def wait_all(timeout_ms: int = 10000) -> None:
    """等待全部在途 worker 结束（应用退出前调用，防退出时 abort）。"""
    for w in list(_ACTIVE):
        if not w.wait(timeout_ms):
            logger.warning("等待后台任务结束超时（%d ms），放弃", timeout_ms)
