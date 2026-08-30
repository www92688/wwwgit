# 协作式取消令牌与通用回调约定（详设 4.2，T-4 线程约定）
from __future__ import annotations

import threading
from collections.abc import Callable


class TaskCanceled(Exception):
    """取消令牌触发时抛出，由 M4 worker 捕获并置任务为 canceled。"""


class SkippedSignal(Exception):
    """批量 handler 内单条素材命中跳过条件（如目标已存在）时抛出，
    由 M4 worker 捕获并置该条为 skipped，不影响批内其余条目。"""


class CancellationToken:
    """协作式取消令牌：工作循环每帧/每分片调用一次 check()。"""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        """置位取消标志（线程安全，可重复调用）。"""
        self._event.set()

    @property
    def cancelled(self) -> bool:
        """是否已被取消。"""
        return self._event.is_set()

    def check(self) -> None:
        """已取消时抛出 TaskCanceled，未取消时静默通过。"""
        if self._event.is_set():
            raise TaskCanceled("任务已被取消")


# 进度回调：0.0~1.0
ProgressFn = Callable[[float], None]
# ffmpeg stderr 行回调
LineFn = Callable[[str], None]
