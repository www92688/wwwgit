# 真实应用整链路验收驱动（手动运行，勿入单测）：会发起真实网络搜索、
# 真实下载 1 条素材到工作目录。用法：.venv/Scripts/python.exe scripts/acceptance_clickthrough.py
# 覆盖：逐页切换 / 树数据 / 登录装配 / 历史下拉 / 真实搜索→结果卡片 /
#       不可用原因提示 / 入队下载→队列成功→归档落盘 / 空选防呆
import os, sys, time, traceback
from PySide6.QtWidgets import QApplication

RESULTS: list[tuple[str, bool, str]] = []

def step(name, fn):
    try:
        fn()
        RESULTS.append((name, True, ""))
        print(f"[PASS] {name}", flush=True)
    except Exception as exc:
        traceback.print_exc()
        RESULTS.append((name, False, str(exc)))
        print(f"[FAIL] {name}: {exc}", flush=True)

def pump(seconds: float) -> None:
    app = QApplication.instance()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)

def wait_until(pred, timeout: float, what: str) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QApplication.instance().processEvents()
        if pred():
            return
        time.sleep(0.05)
    raise AssertionError(f"等待超时：{what}")

# ---- 替换 exec：main() 装配完成后进入驱动 ----
def _exec(self):
    from ych.ui.u0_main.main_window import MainWindow
    from ych.ui.u1_capture.capture_page import CapturePage
    from ych.ui.u2_preprocess.preprocess_page import PreprocessPage
    from ych.ui.u3_dedup.dedup_page import DedupPage
    from ych.ui.u4_failures.failure_page import FailurePage
    from ych.ui.u5_settings.settings_page import SettingsPage

    win = next(w for w in QApplication.topLevelWidgets()
               if isinstance(w, MainWindow))
    pages = {}
    for i in range(win.nav_list.count()):
        win.nav_list.setCurrentRow(i)
        pump(0.3)
        cur = win.stack.currentWidget()
        pages[i] = cur
    step("逐页切换（5 页）", lambda: (
        [pages[i] for i in range(5)],
        (AssertionError("页数不足") if len(pages) < 5 else None),
    ))

    capture = next(w for w in win.findChildren(QWidget) if isinstance(w, CapturePage)) if False else None
    # 直接按类型在 stack 页里找
    def page_of(cls):
        for i in range(5):
            w = pages[i]
            if isinstance(w, cls):
                return w
        # 有些页被容器包裹：递归 findChildren
        for i in range(5):
            found = pages[i].findChildren(cls)
            if found:
                return found[0]
        raise AssertionError(f"未找到页面 {cls.__name__}")

    cap = page_of(CapturePage)
    pre = page_of(PreprocessPage)
    ded = page_of(DedupPage)
    fail_pg = page_of(FailurePage)
    settings = page_of(SettingsPage)

    def check_pages_populated():
        assert cap is not None and pre is not None and ded is not None
        # 去重树有真实数据（用户库里有 木头/砍木头视频）
        assert ded.asset_tree.topLevelItemCount() > 0, "去重树为空"
        # 预处理树有数据
        assert pre.asset_tree.topLevelItemCount() > 0, "预处理树为空"
    step("预处理/去重树有真实数据", check_pages_populated)

    def check_dedup_buttons():
        # 新素材默认全选 → 按钮可用且计数正确
        n = len(ded.checked_paths())
        assert n > 0, "去重页勾选数为 0"
        assert ded.btn_start.isEnabled() and ded.btn_analyze.isEnabled()
        assert str(n) in ded.btn_start.text()
    step("去重页按钮状态与计数", check_dedup_buttons)

    def check_douyin_login_wired():
        assert cap._douyin_login is not None, "登录抖音未装配"
        assert cap.btn_douyin_login.isEnabled()
        assert "已登录" in cap.douyin_name_label.text() or \
               cap.douyin_name_label.text() == ""
    step("登录抖音按钮已装配", check_douyin_login_wired)

    def check_history_combo():
        assert cap.history_combo.count() > 0, "历史下拉为空"
    step("历史下拉有记录", check_history_combo)

    # ---- 真实搜索：森林（Pexels/Pixabay 直连） ----
    def do_search():
        win.nav_list.setCurrentRow(0); pump(0.2)
        cap.keyword_edit.setText("森林")
        cap.btn_search.click()
        wait_until(lambda: cap.btn_search.isEnabled(), 180, "搜索完成（按钮恢复）")
        pump(0.5)
        cards = [i for i in range(cap.result_list.list.count())
                 if cap.result_list.list.item(i).data(0x0100) is not None]  # UserRole
        assert len(cards) > 0, f"搜索无结果卡片（总行 {cap.result_list.list.count()}）"
    step("真实搜索「森林」→ 结果卡片出现", do_search)

    def check_unavailable_note():
        # 占位平台（B站/快手/小红书）应给出带原因的"暂不可用"行（若勾选状态没排除）
        texts = [cap.result_list.list.item(i).text()
                 for i in range(cap.result_list.list.count())]
        notes = [t for t in texts if t.startswith("暂不可用平台")]
        print("    不可用提示：", notes[:1], flush=True)
    step("不可用提示存在（信息性）", check_unavailable_note)

    # ---- 真实下载：只取 1 条，等归档落盘 ----
    def do_download():
        cap.limit_spin.setValue(1)
        cap.result_list.select_all.setChecked(True)
        cap.btn_download = cap.result_list.btn_download
        assert cap.result_list.btn_download.isEnabled(), "下载选中不可点"
        cap.result_list.btn_download.click()
        # 等队列出现成功项（真实 HTTP 下载 + 归档落盘）
        wait_until(
            lambda: "success" in cap.queue_view._states.values(),
            180, "下载完成（队列出现成功项）")
        pump(0.5)
    step("真实下载 1 条 → 队列成功", do_download)

    def check_dest_file():
        from ych.context import user_data_dir  # noqa: F401
        # dest 路径来自队列成功项 tooltip/状态列；直接扫工作目录最新文件
        import sqlite3, glob
        # 从 app.db 最新 download_task 行拿 dest_path
        base = os.environ["LOCALAPPDATA"] + r"/YuChongGou/app.db"
        con = sqlite3.connect(base)
        row = con.execute(
            "select dest_path, status from download_task order by id desc limit 1"
        ).fetchone()
        con.close()
        assert row and row[1] == "success", f"最新任务状态异常：{row}"
        assert os.path.isfile(row[0]) and os.path.getsize(row[0]) > 0, f"落盘失败：{row[0]}"
        print("    已归档：", row[0], flush=True)
    step("下载文件真实落盘（归档校验）", check_dest_file)

    # ---- 全选/空选防呆 ----
    def check_empty_guard():
        cap.result_list.select_all.setChecked(False)
        pump(0.2)
        assert not cap.result_list.btn_download.isEnabled(), "空选时下载按钮应禁用"
        cap.result_list.select_all.setChecked(True); pump(0.1)
    step("空选禁用下载按钮（防呆）", check_empty_guard)

    # 等全部任务到终态再退出，避免销毁在跑对象时的信号噪音
    try:
        wait_until(
            lambda: all(st in ("success", "failed", "cancelled", "skipped",
                               "interrupted")
                        for st in cap.queue_view._states.values()),
            300, "全部下载任务到终态")
    except Exception as exc:
        print("    终态等待：", exc, flush=True)
    pump(1.0)
    QApplication.quit()
    return 0

QApplication.exec = _exec

from PySide6.QtWidgets import QWidget  # noqa: E402
import ych.app as app_mod  # noqa: E402

code = app_mod.main()
failed = [r for r in RESULTS if not r[1]]
print(f"\n==== 驱动结果：{len(RESULTS) - len(failed)}/{len(RESULTS)} 通过 ====")
for name, ok, err in RESULTS:
    print(("  PASS " if ok else "  FAIL ") + name + (f"  <{err}>" if err else ""))
sys.exit(1 if failed else 0)
