# 工作台选项记忆：预处理选项 / 去重档位的保存与恢复
from __future__ import annotations

from ych.services.s5_base.config_service import ConfigService


def test_option_panel_persist_roundtrip(qapp, qtbot) -> None:
    from ych.ui.u2_preprocess.option_panel import OptionPanel

    config = ConfigService()
    panel = OptionPanel(config=config)
    qtbot.addWidget(panel)

    panel.wm_mode.setCurrentIndex(2)          # 手动框选
    panel.sub_mode.setCurrentIndex(1)         # 自动检测
    panel.aspect.setCurrentIndex(1)           # 9:16 竖屏
    panel.crop_enabled.setChecked(True)
    panel.crop_x.setValue(0.1)
    panel.strip_audio.setChecked(True)

    saved = config.get("preprocess_options")
    assert isinstance(saved, dict)
    assert saved["wm_mode"] == "manual"
    assert saved["sub_mode"] == "auto"
    assert saved["aspect"] == "9x16"          # 元组经 JSON 落库为字符串
    assert saved["crop_enabled"] is True
    assert saved["strip_audio"] is True

    panel2 = OptionPanel(config=config)       # 新面板恢复上次值
    qtbot.addWidget(panel2)
    assert panel2.wm_mode.currentData() == "manual"
    assert panel2.sub_mode.currentData() == "auto"
    assert panel2.aspect.currentData() == (9, 16)
    assert panel2.crop_enabled.isChecked()
    assert panel2.crop_x.value() == 0.1
    assert panel2.strip_audio.isChecked()
    assert panel2.crop_w.isEnabled()          # 启用裁剪 → 数值框可用


def test_option_panel_without_config_noop(qapp, qtbot) -> None:
    from ych.ui.u2_preprocess.option_panel import OptionPanel

    panel = OptionPanel()                     # 无 config：默认值可用即可
    qtbot.addWidget(panel)
    assert panel.wm_mode.currentData() == "off"
    assert not panel.crop_w.isEnabled()       # 默认未启用裁剪


def test_dedup_preset_memory(qapp, qtbot) -> None:
    from ych.ui.u3_dedup.dedup_page import DedupPage

    config = ConfigService()
    page = DedupPage(config=config)
    qtbot.addWidget(page)
    page._preset_radios["heavy"].setChecked(True)
    assert config.get("dedup_preset") == "heavy"

    page2 = DedupPage(config=config)          # 新页面恢复上次档位
    qtbot.addWidget(page2)
    assert page2._preset_radios["heavy"].isChecked()
