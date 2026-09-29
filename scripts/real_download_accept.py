# 真实下载终验脚本（手动运行）：会真实下载素材到工作目录。
# 用法：.venv/Scripts/python.exe scripts/real_download_accept.py
# 步骤①搜索「粘土」走免费平台下载；②粘贴抖音主页链接搜索并下载。
# 注意：抖音受平台风控影响，列表被拒时请过几小时再试（脚本会显示平台原因）。
import os, sys, time, sqlite3, traceback
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, "src")

from PySide6.QtWidgets import QApplication

RESULTS = []
URL = ("https://www.douyin.com/user/MS4wLjABAAAAJODiOcX1x4rU6DWcG2uSM4CV"
       "EsgrMA7fTsW09CJCz8CYIZE0rhqaq40mMupXwtnu")

def step(name, fn):
    try:
        detail = fn()
        RESULTS.append((name, True, ""))
        print(f"[PASS] {name}" + (f"  -> {detail}" if detail else ""), flush=True)
    except Exception as exc:
        traceback.print_exc()
        RESULTS.append((name, False, str(exc)))
        print(f"[FAIL] {name}: {exc}", flush=True)

def pump(seconds):
    app = QApplication.instance()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents(); time.sleep(0.02)

def wait_until(pred, timeout, what):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QApplication.instance().processEvents()
        if pred(): return
        time.sleep(0.05)
    raise AssertionError(f"等待超时({timeout}s)：{what}")

def newest_task(con=None):
    own = con is None
    if own: con = sqlite3.connect(os.environ["LOCALAPPDATA"] + r"/YuChongGou/app.db")
    row = con.execute(
        "select platform_id, status, dest_path, error_code from download_task "
        "order by id desc limit 1").fetchone()
    if own: con.close()
    return row

def _exec(self):
    from ych.ui.u0_main.main_window import MainWindow
    from ych.ui.u1_capture.capture_page import CapturePage
    win = next(w for w in QApplication.topLevelWidgets() if isinstance(w, MainWindow))
    win.nav_list.setCurrentRow(0); pump(0.5)
    cap = win.stack.currentWidget()
    assert isinstance(cap, CapturePage)
    UserRole = 0x0100

    def cards():
        out = []
        for i in range(cap.result_list.list.count()):
            it = cap.result_list.list.item(i)
            m = it.data(UserRole)
            if m is not None:
                out.append((m, it))
        return out

    def do_search(kw, timeout, want_plugin=None):
        cap.keyword_edit.setText(kw)
        cap.btn_search.click()
        wait_until(lambda: cap.btn_search.isEnabled(), timeout, f"搜索完成 {kw[:30]}")
        pump(0.5)
        got = [m for m, _ in cards()]
        if want_plugin:
            got = [m for m in got if m.plugin_id == want_plugin]
        assert got, (f"搜索「{kw[:40]}」无结果卡片；"
                     f"提示行={[it.text() for it in [cap.result_list.list.item(i) for i in range(cap.result_list.list.count())] if not it.data(UserRole)][:2]}")
        return len(got)

    def do_download_one(tag, timeout):
        cap.limit_spin.setValue(1)
        cap.result_list.select_all.setChecked(True); pump(0.2)
        assert cap.result_list.btn_download.isEnabled()
        cap.result_list.btn_download.click()
        wait_until(lambda: "success" in cap.queue_view._states.values(),
                   timeout, f"{tag} 下载队列出现成功项")
        pump(1.0)
        plat, status, dest, err = newest_task()
        assert status == "success", f"任务未成功：{plat}/{status}/{err}"
        assert dest and os.path.isfile(dest) and os.path.getsize(dest) > 0, f"未落盘：{dest}"
        assert ":" not in os.path.basename(dest) and "?" not in os.path.basename(dest), f"文件名含非法字符：{dest}"
        return dest

    def stepA():
        n = do_search("粘土", 180)
        print(f"    「粘土」结果卡片 {n} 张", flush=True)
        return do_download_one("粘土", 240)

    step("① 搜索「粘土」→ 免费平台真实下载落盘", stepA)

    def stepB():
        cap.stock_checks["pexels"].setChecked(False)
        cap.stock_checks["pixabay"].setChecked(False)
        cap.cn_checks["douyin"].setChecked(True); pump(0.2)
        n = do_search(URL, 600, want_plugin="douyin")
        print(f"    抖音主页作品卡片 {n} 张", flush=True)
        return do_download_one("抖音", 600)

    step("② 抖音主页链接 → 真实搜索并下载落盘", stepB)

    try:
        wait_until(lambda: all(s in ("success", "failed", "cancelled", "skipped")
                               for s in cap.queue_view._states.values()),
                   300, "全部任务终态")
    except Exception as exc:
        print("    终态等待：", exc, flush=True)
    pump(1.0)
    QApplication.quit()
    return 0

QApplication.exec = _exec
import ych.app as app_mod
app_mod.main()
failed = [r for r in RESULTS if not r[1]]
print(f"\n==== 真实下载终验：{len(RESULTS)-len(failed)}/{len(RESULTS)} 通过 ====")
for name, ok, err in RESULTS:
    print(("  PASS " if ok else "  FAIL ") + name + (f"  <{err}>" if err else ""))
sys.exit(1 if failed else 0)
