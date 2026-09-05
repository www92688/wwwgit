# 国际化服务（详设 5.2）：语言包加载与切换，默认中文
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, QTranslator, Signal

from ych.common.fsutil import bundle_data_root

logger = logging.getLogger("ych.s5")


def _default_i18n_dir() -> Path:
    """源码运行 → 仓库根 i18n/；打包运行 → _internal/i18n。"""
    return bundle_data_root() / "i18n"


class I18nService(QObject):
    """界面多语言：卸载旧翻译 → 加载 <locale>.qm → 安装 → 发信号。"""

    locale_changed = Signal(str)

    def __init__(self, i18n_dir: Path | None = None) -> None:
        super().__init__()
        self._dir = i18n_dir if i18n_dir is not None else _default_i18n_dir()
        self._locale = "zh_CN"
        self._translator: QTranslator | None = None

    def current_locale(self) -> str:
        return self._locale

    def available_locales(self) -> list[str]:
        """扫描 i18n 目录下的 .qm 文件名作为可用语言列表。"""
        if not self._dir.exists():
            return []
        return sorted(p.stem for p in self._dir.glob("*.qm"))

    def switch_locale(self, locale: str) -> None:
        """切换语言；.qm 缺失时跳过加载但仍切换并发信号（降级不崩溃）。"""
        from PySide6.QtCore import QCoreApplication

        if self._translator is not None:
            QCoreApplication.removeTranslator(self._translator)
            self._translator = None
        qm = self._dir / f"{locale}.qm"
        if qm.exists():
            translator = QTranslator()
            if translator.load(str(qm)):
                self._translator = translator
                QCoreApplication.installTranslator(translator)
            else:  # 加载失败按缺失处理
                logger.warning("语言包加载失败：%s", qm)
        else:
            logger.info("语言包不存在，跳过加载：%s", qm)
        self._locale = locale
        self.locale_changed.emit(locale)
