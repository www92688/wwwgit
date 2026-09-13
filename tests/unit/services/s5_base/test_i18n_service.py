# I18nService 单元测试（对照 5.4）
import pytest
from PySide6.QtCore import QCoreApplication

from ych.services.s5_base.i18n_service import I18nService


@pytest.fixture(scope="module")
def qcoreapp() -> QCoreApplication:
    # 必须创建 QApplication 而非裸 QCoreApplication：若本 fixture 先建了
    # 裸 QCoreApplication，后续 pytest-qt 再建 QApplication 会直接致命
    # （历史地雷：单跑 s5_base + ui 目录时进程段错误）
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_default_locale_zh_cn(qcoreapp) -> None:
    svc = I18nService()
    assert svc.current_locale() == "zh_CN"


def test_available_locales_scans_qm_dir(tmp_path, qcoreapp) -> None:
    (tmp_path / "en_US.qm").write_bytes(b"x")
    (tmp_path / "zh_CN.qm").write_bytes(b"y")
    svc = I18nService(i18n_dir=tmp_path)
    assert svc.available_locales() == ["en_US", "zh_CN"]


def test_available_locales_missing_dir_returns_empty(tmp_path, qcoreapp) -> None:
    svc = I18nService(i18n_dir=tmp_path / "no_such_dir")
    assert svc.available_locales() == []


def test_switch_locale_updates_state_and_emits_signal(qcoreapp) -> None:
    # 仓库 i18n 目录当前无 .qm 文件：走「缺失跳过加载」降级分支，
    # 但语言状态与信号照常生效（详设 5.2 容错语义）
    svc = I18nService()
    seen: list[str] = []
    svc.locale_changed.connect(seen.append)
    svc.switch_locale("en_US")
    assert svc.current_locale() == "en_US"
    assert seen == ["en_US"]


def test_switch_back_to_zh_cn_removes_translator(tmp_path, qcoreapp) -> None:
    # 用无效 qm 内容验证 load 失败不崩溃、状态仍切换
    (tmp_path / "en_US.qm").write_bytes(b"not-a-real-qm")
    svc = I18nService(i18n_dir=tmp_path)
    seen: list[str] = []
    svc.locale_changed.connect(seen.append)
    svc.switch_locale("en_US")
    svc.switch_locale("zh_CN")   # zh_CN.qm 不存在 → 跳过加载分支
    assert svc.current_locale() == "zh_CN"
    assert seen == ["en_US", "zh_CN"]
