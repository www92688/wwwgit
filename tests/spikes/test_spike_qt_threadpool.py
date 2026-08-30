# 冒烟③：PySide6 可导入 + QThreadPool 满载下主线程事件循环保持响应
# （D1 替身冒烟，对应 SP-7 并发与 GIL 冒烟的工程前提，T-7 线程约定）
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_pyside6_importable():
    # 导入本身即验证项（QtCore/QtGui/QtWidgets 全部可加载）
    from PySide6 import QtCore, QtGui, QtWidgets  # noqa: F401


def test_qthreadpool_full_load_main_thread_responsive():
    from PySide6.QtCore import QElapsedTimer, QRunnable, QThreadPool
    from PySide6.QtWidgets import QApplication

    # 统一使用 QApplication 单例（QCoreApplication 会污染后续 Widgets 测试）
    app = QApplication.instance() or QApplication([])
    pool = QThreadPool.globalInstance()
    max_threads = pool.maxThreadCount()
    assert max_threads >= 1

    done: list[int] = []

    class BusyTask(QRunnable):
        """模拟满载工作线程（sleep 释放 GIL，贴近 IO/子进程型任务）。"""

        def run(self) -> None:
            time.sleep(0.3)
            done.append(1)

    for _ in range(max_threads):
        pool.start(BusyTask())

    # 满载期间持续泵事件循环，断言单次 processEvents 均远快于 1s
    worst_s = 0.0
    deadline = time.monotonic() + 5.0
    timer = QElapsedTimer()
    timer.start()
    while len(done) < max_threads and time.monotonic() < deadline:
        tick = time.perf_counter()
        app.processEvents()
        worst_s = max(worst_s, time.perf_counter() - tick)
        time.sleep(0.01)

    pool.waitForDone()
    assert len(done) == max_threads, "满载任务未全部完成"
    assert worst_s < 1.0, f"主线程事件循环最差响应 {worst_s:.3f}s ≥ 1s"
