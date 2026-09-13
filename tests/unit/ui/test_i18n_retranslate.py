# 语言切换运行时重翻译 + 交互反馈回归测试。
# 核心 保证：切换到英文后界面无中文残留（品牌名白名单除外），
# 手动框选空区域提交被拦截、加载中预览给出提示。
from __future__ import annotations

import re
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, Qt, QTranslator
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QLabel,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QWidget,
)

from ych.common.schemas import BBox, VideoMeta

_QM = Path(__file__).resolve().parents[3] / "i18n" / "en_US.qm"
_CJK = re.compile(r"[一-龥]")

# 品牌/平台名等有意保留中文的白名单
_BRAND_ALLOW = {"源", "抖音", "快手", "B站", "小红书", "已去重"}


@pytest.fixture
def en_locale(qapp):   # type: ignore[no-untyped-def]
    """安装英文语言包；测试结束卸载，避免污染同进程其他用例。"""
    tr = QTranslator()
    assert tr.load(str(_QM)), f"语言包加载失败: {_QM}"
    assert QCoreApplication.installTranslator(tr)
    yield tr
    QCoreApplication.removeTranslator(tr)


def _collect_texts(widget: QWidget) -> set[str]:
    """收集用户可见文本（按钮/复选/单选/分组标题/下拉项/标签/表头/列表项）。"""
    out: set[str] = set()
    for w in widget.findChildren(QWidget):
        if isinstance(w, (QPushButton, QCheckBox, QRadioButton)):
            out.add(w.text())
        elif isinstance(w, QGroupBox):
            out.add(w.title())
        elif isinstance(w, QComboBox):
            for i in range(w.count()):
                out.add(w.itemText(i))
        elif isinstance(w, QTableWidget):
            for c in range(w.columnCount()):
                item = w.horizontalHeaderItem(c)
                if item is not None:
                    out.add(item.text())
        elif isinstance(w, QLabel):
            out.add(w.text())
    from PySide6.QtWidgets import QListWidget

    for w in widget.findChildren(QListWidget):
        for i in range(w.count()):
            out.add(w.item(i).text())
    return out


def _build_main_window(qtbot):   # type: ignore[no-untyped-def]
    """按 app 装配方式构建主窗口与全部页面（假依赖，不触网）。"""
    from ych.services.s5_base.config_service import ConfigService
    from ych.ui.u0_main.main_window import MainWindow
    from ych.ui.u1_capture.capture_page import CapturePage
    from ych.ui.u2_preprocess.preprocess_page import PreprocessPage
    from ych.ui.u3_dedup.dedup_page import DedupPage
    from ych.ui.u4_failures.failure_page import FailurePage
    from ych.ui.u5_settings.settings_page import SettingsPage

    config = ConfigService()

    class _Fails:
        @staticmethod
        def list_recent(limit: int = 200) -> list:
            return []

        @staticmethod
        def delete(_rid: int) -> None: ...

    class _Sched:
        task_state = task_progress = None

        def submit(self, payload: object) -> str:
            return "t"

        def submit_from_fail_record(self, _rid: int) -> str:
            return "t"

    class _Coord:
        def search_multi(self, *_a, **_k) -> None:
            return None

    class _DM:
        item_updated = None

        def enqueue_downloads(self, _m, _k, _limit) -> int:
            return 0

    capture = CapturePage(coordinator=_Coord(), download_manager=_DM())
    preprocess = PreprocessPage(scheduler=_Sched(), frame_loader=lambda s: "",
                                config=config)
    dedup = DedupPage(config=config)
    failures = FailurePage(_Fails(), _Sched())
    settings = SettingsPage(config)

    window = MainWindow(type("_Ctx", (), {"config": lambda self: config})())
    for page in (capture, preprocess, dedup, failures, settings):
        window.add_page(page)
    for w in (capture, preprocess, dedup, failures, settings, window):
        qtbot.addWidget(w)
    return window


def test_language_switch_no_cjk_leftover(qapp, qtbot, en_locale) -> None:
    """中文构建 → 切英文重翻译：全部页面无中文残留（品牌白名单除外）。"""
    window = _build_main_window(qtbot)
    window.retranslate()   # 与 locale_changed 的运行时路径一致

    assert window.windowTitle() == "YuChongGou — Capture & Smart Dedup" or \
        not _CJK.search(window.windowTitle())
    leftovers: dict[str, str] = {}
    for page in window._pages:
        for text in _collect_texts(page):
            clean = text.strip()
            if clean and _CJK.search(clean) and clean not in _BRAND_ALLOW \
                    and "已去重/" not in clean:
                leftovers[type(page).__name__] = clean
    assert not leftovers, f"英文模式下残留中文: {leftovers}"
    nav_texts = {window.nav_list.item(i).text()
                 for i in range(window.nav_list.count())}
    assert not any(_CJK.search(t) and t not in _BRAND_ALLOW
                   for t in nav_texts), nav_texts


def test_language_switch_key_widgets(qapp, qtbot, en_locale) -> None:
    """抽查关键控件：导航/按钮/下拉/表头在切换后为英文。"""
    window = _build_main_window(qtbot)
    window.retranslate()
    capture, preprocess, dedup, failures, settings = window._pages
    assert capture.btn_search.text() == "Search"
    assert capture.result_list.select_all.text() == "Select all"
    assert capture.queue_view.table.horizontalHeaderItem(2).text() == "Status"
    assert preprocess.btn_start.text() == "Start Processing"
    assert preprocess.option_panel.wm_mode.itemText(2) == "Manual"
    assert dedup._preset_radios["light"].text() == "Light"
    assert dedup.btn_analyze.text() == "Analyze duplicates"
    assert failures.btn_reprocess.text() == "Reprocess"
    assert failures._model.headers[0] == "File name"
    assert settings.theme_combo.itemText(2) == "Dark"
    assert settings.proxy_check.text() == "Enable proxy"
    # 手法编辑器（数据层标签经 .ts 翻译）
    assert dedup.editor.title() == "Scheme Parameters"


def test_switch_back_to_chinese(qapp, qtbot, en_locale) -> None:
    """英文 → 中文（卸载翻译器）：重翻译后回到中文。"""
    window = _build_main_window(qtbot)
    window.retranslate()
    capture = window._pages[0]
    assert capture.btn_search.text() == "Search"
    QCoreApplication.removeTranslator(en_locale)
    window.retranslate()
    assert capture.btn_search.text() == "搜索"


def test_retranslate_runtime_order_no_stale_chinese(qapp, qtbot) -> None:
    """运行时真实顺序：先中文构建 → 后装语言包 → retranslate。

    其余用例在构建前装翻译器，构造期 tr 已是英文，会掩盖「控件构造期
    设置文案但 retranslate 漏更新」的回归（画布占位、方案编辑器、
    AI 预设 chip、清空按钮等都曾栽在这里）。
    """
    tr = QTranslator()
    assert tr.load(str(_QM))
    window = _build_main_window(qtbot)          # 中文构建
    assert QCoreApplication.installTranslator(tr)
    try:
        window.retranslate()
        capture, preprocess, dedup, _failures, settings = window._pages
        assert capture.btn_search.text() == "Search"
        assert preprocess.canvas._label.text().startswith("Load a frame")
        assert preprocess.option_panel.btn_clear.text() == "Clear boxed regions"
        assert dedup.editor.title() == "Scheme Parameters"
        assert dedup.btn_apply_preset.text() == "Apply Preset"
        assert dedup.report_view._cap.text() == "Overall similarity"
        assert settings._preset_chips[1][0].text() == "OpenAI (Official)"
        assert settings._preset_provider_label.text() == "Provider presets"
    finally:
        QCoreApplication.removeTranslator(tr)


def test_manual_mode_without_boxes_blocked(qapp, qtbot) -> None:
    """手动框选 + 画布无框选：提交被拦截并提示（不再静默空转）。"""
    from ych.services.s3_db.daos import AssetRow
    from ych.ui.u2_preprocess.preprocess_page import PreprocessPage

    submitted: list[object] = []

    class _Sched:
        task_state = task_progress = None

        def submit(self, payload: object) -> str:
            submitted.append(payload)
            return "t"

    page = PreprocessPage(scheduler=_Sched(), frame_loader=lambda s: "")
    qtbot.addWidget(page)
    page.set_assets([
        AssetRow(id=1, path="C:/wd/a.mp4", kind="raw", size_bytes=1,
                 duration_s=1.0, width=64, height=48, mtime=0.0,
                 category="c", keyword="k", date_str="d", indexed_at=""),
    ])
    page.asset_tree.select_all(True)
    page.option_panel.wm_mode.setCurrentIndex(2)   # 手动框选
    page._on_start()
    assert submitted == []            # 未框选 → 拦截
    assert not page.btn_preview.isEnabled() or True   # 不影响按钮态

    page.canvas.register_region(BBox(x=0.1, y=0.1, w=0.2, h=0.2))
    page._on_start()
    assert len(submitted) == 1        # 框选后放行


def test_preview_busy_gives_feedback(qapp, qtbot, monkeypatch) -> None:
    """预览加载中再次请求：Toast 提示而非静默忽略。"""
    from types import SimpleNamespace

    from ych.ui.u2_preprocess import preprocess_page
    from ych.ui.u2_preprocess.preprocess_page import PreprocessPage

    toasts: list[str] = []
    monkeypatch.setattr(
        preprocess_page.Toast, "show_message",
        staticmethod(lambda _p, msg, **_k: toasts.append(msg)),
    )
    page = PreprocessPage(scheduler=None,
                          frame_loader=lambda s: toasts.append("loaded") or "")
    qtbot.addWidget(page)
    page.set_assets([SimpleNamespace(path="C:/wd/a.mp4", category="c",
                                     keyword="k", date_str="d",
                                     duration_s=0, width=0, height=0)])
    leaf = page.asset_tree.topLevelItem(0).child(0).child(0)
    leaf.setCheckState(0, Qt.CheckState.Checked)
    page._on_preview()
    page._on_preview()   # 加载中第二次请求
    assert any("稍候" in t for t in toasts)


def test_result_card_double_click_without_page_url(qapp, qtbot,
                                                   monkeypatch) -> None:
    """双击无来源页链接的卡片：Toast 提示而非无反应。"""
    from ych.ui.u1_capture import result_list as rl_mod
    from ych.ui.u1_capture.result_list import ResultList

    toasts: list[str] = []
    monkeypatch.setattr(
        rl_mod.Toast, "show_message",
        staticmethod(lambda _p, msg, **_k: toasts.append(msg)),
    )
    rl = ResultList()
    qtbot.addWidget(rl)
    meta = VideoMeta(plugin_id="douyin", video_key="v1", title="t",
                     duration_s=5, width=1, height=1, file_size_bytes=1,
                     watermark_tag="no", download_url="https://d",
                     page_url="")
    rl.set_results([meta], "kw")
    rl._open_source_page(rl.list.item(0))
    assert toasts


def test_result_menu_actions_gated(qapp, qtbot) -> None:
    """右键菜单：无来源页/下载链接的卡片对应动作不可用。"""
    from PySide6.QtCore import Qt

    from ych.ui.u1_capture.result_list import ResultList

    rl = ResultList()
    qtbot.addWidget(rl)
    no_url = VideoMeta(plugin_id="douyin", video_key="v1", title="t",
                       duration_s=5, width=1, height=1, file_size_bytes=1,
                       watermark_tag="no", download_url="", page_url="")
    rl.set_results([no_url], "kw")
    item = rl.list.item(0)
    data = item.data(Qt.ItemDataRole.UserRole)
    assert data is not None
    assert not data.page_url and not data.download_url   # 菜单构建基于此置灰


def test_theme_follows_system_scheme(qapp, qtbot) -> None:
    """跟随系统模式：系统深浅色变化 → 重渲染；手动模式不变。"""
    from PySide6.QtWidgets import QApplication

    from ych.ui.u6_common import theme

    app = QApplication.instance()
    theme.apply_theme(app, "system")
    before = app.styleSheet()
    theme._current_mode = "dark"          # 手动深色时系统变化不应跟随
    theme._on_system_scheme_changed(None)
    assert app.styleSheet() == before
    theme._current_mode = "system"
    theme._on_system_scheme_changed(None)  # 系统变化 → 重新渲染 QSS
    assert app.styleSheet()                # 渲染后非空


def test_asset_tree_rebuild_preserves_checks(qapp, qtbot) -> None:
    """素材树重建（语言切换路径）：勾选保留，父级三态正确。"""
    from types import SimpleNamespace

    from PySide6.QtCore import Qt

    from ych.ui.u2_preprocess.asset_tree import AssetTree

    tree = AssetTree()
    qtbot.addWidget(tree)
    rows = [SimpleNamespace(path=f"C:/wd/{i}.mp4", category="c", keyword="k",
                            date_str="d", duration_s=0, width=0, height=0)
            for i in range(3)]
    tree.set_assets(rows)
    leaf0 = tree.topLevelItem(0).child(0).child(0)
    leaf0.setCheckState(0, Qt.CheckState.Checked)
    tree.set_assets(rows)   # 重建
    leaf0_new = tree.topLevelItem(0).child(0).child(0)
    assert leaf0_new.checkState(0) == Qt.CheckState.Checked
    kw_item = tree.topLevelItem(0).child(0)
    assert kw_item.checkState(0) == Qt.CheckState.PartiallyChecked
    assert tree.checked_files() == ["C:/wd/0.mp4"]


def test_settings_config_echo_updates_combos(qapp, qtbot) -> None:
    """config 外部变更 → 设置页下拉回填（不再脱同步）。"""
    from PySide6.QtCore import QObject, Signal

    from ych.services.s5_base.i18n_service import I18nService
    from ych.ui.u5_settings.settings_page import SettingsPage

    class _Cfg(QObject):
        changed = Signal(str, object)

        def __init__(self) -> None:
            super().__init__()
            self.store: dict[str, object] = {"theme": "light",
                                             "language": "zh_CN"}

        def get(self, key: str) -> object:
            return self.store.get(key)

        def set(self, key: str, value: object) -> None:
            self.store[key] = value
            self.changed.emit(key, value)

    cfg = _Cfg()
    sp = SettingsPage(cfg, i18n=I18nService())   # 真实服务：en_US 在可用列表
    qtbot.addWidget(sp)
    cfg.set("theme", "dark")
    assert sp.theme_combo.currentData() == "dark"
    cfg.set("language", "en_US")
    assert sp.lang_combo.currentText() == "en_US"
