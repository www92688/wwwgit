# 搜索协调器重入防护：上一轮搜索线程存活时拒绝新调用（防两轮结果交错）
from __future__ import annotations

import threading
from types import SimpleNamespace

from PySide6.QtCore import QObject

from ych.common.schemas import SearchFilters
from ych.core.m1_capture.search_coordinator import SearchCoordinator
from ych.services.s5_base.config_service import ConfigService


def test_search_multi_refuses_reentrant_call(qtbot) -> None:
    coord = SearchCoordinator(
        SimpleNamespace(), SimpleNamespace(), ConfigService(),
    )
    assert isinstance(coord, QObject)
    assert coord._worker is None

    blocked = threading.Event()
    alive_thread = threading.Thread(
        target=blocked.wait, args=(5.0,), daemon=True)
    alive_thread.start()
    coord._worker = alive_thread

    # 拒绝重入必须发失败信号：UI 的"搜索中"状态只有收到 failed/finished
    # 才会复位，静默拒绝会让搜索按钮永远转圈（点击无响应类缺陷）
    received: list[tuple[str, str]] = []
    coord.search_failed.connect(lambda kw, msg: received.append((kw, msg)))

    coord.search_multi(["关键词"], SearchFilters(), 30)
    assert coord._worker is alive_thread   # 未被新线程覆盖
    assert len(received) == 1
    assert "上一轮搜索尚未结束" in received[0][1]

    blocked.set()      # 解除阻塞让守护线程退出，不污染后续用例
    alive_thread.join(timeout=5)
