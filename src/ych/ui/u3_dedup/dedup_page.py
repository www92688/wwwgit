# 去重工作台（U3）：素材勾选 / 三档方案卡片 / 自定义编辑 / 报告视图
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Protocol

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from ych.core.m3_dedup.techniques.registry import TechniqueRegistry
from ych.ui.u3_dedup.report_view import ReportView
from ych.ui.u3_dedup.scheme_editor import SchemeEditor
from ych.ui.u6_common.context_actions import (
    copy_to_clipboard,
    reveal_in_file_manager,
)
from ych.ui.u6_common.empty_state import attach_empty_state
from ych.ui.u6_common.toast import Toast


class _SchemeManagerLike(Protocol):
    def recommend(self, score: float) -> str: ...

    def instantiate(self, preset_id: str, seed: int | None = None) -> list[dict[str, object]]: ...

    def preset_to_custom(
        self, preset_id: str, seed: int | None = None,
    ) -> list[dict[str, object]]: ...

    def save_custom(self, name: str, config: list[dict[str, object]]) -> int: ...

    def load_custom(self, name: str) -> list[dict[str, object]] | None: ...


class DedupResultBar(QWidget):
    """去重结果持久展示条：成功/跳过/失败明细 + 输出目录打开入口。

    只在有一次去重结果后显示；toast 一闪而过的补充（任务完成但用户
    离开页面/错过 toast 时仍有据可查）。
    """

    regenerate_requested = Signal(list)   # 全部「输出已存在」跳过时的重跑入口

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._summary: dict[str, Any] = {}
        self._output_dir = ""
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel()
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.TextFormat.RichText)
        self.btn_open = QPushButton(self.tr("打开输出目录"))
        self.btn_open.setObjectName("secondaryBtn")
        self.btn_open.clicked.connect(self._open_dir)
        self.btn_regenerate = QPushButton(self.tr("重新生成"))
        self.btn_regenerate.setObjectName("secondaryBtn")
        self.btn_regenerate.setToolTip(self.tr(
            "删除「已去重」目录下的同名输出文件，并按当前勾选素材与方案重新去重"))
        self.btn_regenerate.clicked.connect(self._emit_regenerate)
        row.addWidget(self.label, 1)
        row.addWidget(self.btn_regenerate)
        row.addWidget(self.btn_open)
        self.hide()

    def set_summary(self, summary: dict[str, Any]) -> None:
        self._summary = dict(summary)
        self._output_dir = str(summary.get("output_dir") or "")
        self._render()

    def _regen_srcs(self) -> list[str]:
        """可重新生成的源素材路径（=「输出已存在」跳过的那批）。"""
        return [str(x) for x in (self._summary.get("skipped_srcs") or [])]

    def regen_outputs(self) -> list[Path]:
        """与 skipped_srcs 对应的已存在输出文件（存在才返回）。"""
        out_dir = self._output_dir
        if not out_dir:
            return []
        names = [str(x) for x in (self._summary.get("skipped_names") or [])]
        return [Path(out_dir) / n for n in names if (Path(out_dir) / n).exists()]

    def _emit_regenerate(self) -> None:
        srcs = self._regen_srcs()
        if srcs:
            self.regenerate_requested.emit(srcs)

    def _render(self) -> None:
        s = self._summary
        n_ok = len(s.get("outputs") or [])
        skipped = int(s.get("skipped") or 0)
        failed = int(s.get("failed") or 0)
        names = [str(x) for x in (s.get("skipped_names") or [])]
        msgs = [str(x) for x in (s.get("failed_msgs") or [])]
        color = "#e03131" if failed else ("#e8890c" if skipped else "#2f9e6e")
        text = f'<span style="color:{color};">{self.tr("去重结果")}</span>：' \
               + self.tr("成功 {n} 条").format(n=n_ok)
        if skipped:
            text += "，" + self.tr("跳过 {n} 条").format(n=skipped)
        if failed:
            text += "，" + self.tr("失败 {n} 条").format(n=failed)
        details: list[str] = []
        if names:
            shown = "、".join(names[:3])
            if len(names) > 3:
                shown += self.tr(" 等 {n} 个").format(n=len(names))
            details.append(self.tr("已存在未重处理：{names}").format(names=shown))
        if msgs:
            details.append(self.tr("失败原因：{msgs}").format(
                msgs="；".join(msgs[:3])))
        before = s.get("before_pct")
        after = s.get("after_pct")
        if before is not None and after is not None:
            details.append(self.tr("重复度 {a}% → {b}%").format(a=before, b=after))
        if details:
            text += "<br><span style='color:#a5aec0;'>" \
                    + "；".join(details) + "</span>"
        self.label.setText(text)
        self.btn_open.setVisible(bool(self._output_dir))
        self.btn_regenerate.setVisible(bool(self._regen_srcs()))
        self.show()

    def _open_dir(self) -> None:
        if not reveal_in_file_manager(self._output_dir):
            Toast.show_message(self, self.tr("打开失败：目录不存在或无法访问"),
                               error=True)

    def retranslate(self) -> None:
        self.btn_open.setText(self.tr("打开输出目录"))
        self.btn_regenerate.setText(self.tr("重新生成"))
        self.btn_regenerate.setToolTip(self.tr(
            "删除「已去重」目录下的同名输出文件，并按当前勾选素材与方案重新去重"))
        if self.isVisible():
            self._render()


class DedupPage(QWidget):
    """选素材 → 选方案 → 开始，三步约束。"""

    analyze_requested = Signal(list)          # 选中的 srcs（compare 任务）
    dedup_requested = Signal(list, list)      # (srcs, technique_params)

    def __init__(
        self,
        registry: TechniqueRegistry | None = None,
        scheme_manager: _SchemeManagerLike | None = None,
        config: Any | None = None,          # 档位记忆（可选）
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._registry = registry or make_registry()
        self._schemes = scheme_manager
        self._config = config
        self._last_submit_key: tuple[object, ...] | None = None
        self._last_submit_ts = 0.0

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # ---- 步骤引导 ----
        from ych.ui.u6_common.step_hint import StepHint

        self._step_hint = StepHint(self._step_texts())
        root.addWidget(self._step_hint)

        split = QHBoxLayout()

        # ---- 左：素材勾选列表 ----
        left_box = QVBoxLayout()
        self._asset_label = QLabel(self.tr("待去重素材"))
        left_box.addWidget(self._asset_label)
        self.asset_list = QListWidget()
        self._empty = attach_empty_state(
            self.asset_list, self.tr("暂无素材"),
            self.tr("先到「采集工作台」下载素材，\n或把视频文件放入工作目录"),
        )
        left_box.addWidget(self.asset_list, 1)
        self.btn_all = QPushButton(self.tr("全选"))
        self.btn_all.setObjectName("secondaryBtn")
        self.btn_all.clicked.connect(self._select_all)
        left_box.addWidget(self.btn_all)
        split.addLayout(left_box, 1)

        # ---- 右：方案区 ----
        right_box = QVBoxLayout()
        self._scheme_label = QLabel(self.tr("去重方案"))
        right_box.addWidget(self._scheme_label)
        self.radio_group = QButtonGroup(self)
        self._preset_radios: dict[str, QRadioButton] = {}
        self._desc_labels: dict[str, QLabel] = {}
        self._recommended_id = ""
        cards_row = QHBoxLayout()
        cards_row.setSpacing(8)
        for pid in ("light", "mid", "heavy"):
            card = QWidget()
            card.setObjectName("presetCard")
            card_lay = QVBoxLayout(card)
            card_lay.setContentsMargins(10, 8, 10, 8)
            card_lay.setSpacing(4)
            radio = QRadioButton()
            radio.setProperty("preset_id", pid)
            self.radio_group.addButton(radio)
            radio.setChecked(pid == "mid")
            # 点击档位立即套用对应预设，而非等"套用预设"按钮；并记忆档位
            radio.toggled.connect(
                lambda on, p=pid: self._apply_preset() if on else None,
            )
            radio.toggled.connect(
                lambda on, p=pid: self._save_preset(p) if on else None,
            )
            radio.toggled.connect(
                lambda on, c=card: _set_card_checked(c, on),
            )
            desc = QLabel(self._preset_descriptions()[pid])
            desc.setObjectName("muted")
            desc.setWordWrap(True)
            card_lay.addWidget(radio)
            card_lay.addWidget(desc)
            _set_card_checked(card, radio.isChecked())
            cards_row.addWidget(card, 1)
            self._preset_radios[pid] = radio
            self._desc_labels[pid] = desc
        self._refresh_preset_labels()
        right_box.addLayout(cards_row)
        self.editor = SchemeEditor(self._registry)
        right_box.addWidget(self.editor, 1)

        row_btns = QHBoxLayout()
        self.btn_apply_preset = QPushButton(self.tr("套用预设"))
        self.btn_apply_preset.setObjectName("secondaryBtn")
        self.btn_apply_preset.clicked.connect(self._apply_preset)
        self.btn_to_custom = QPushButton(self.tr("预设→自定义微调"))
        self.btn_to_custom.setObjectName("secondaryBtn")
        self.btn_to_custom.clicked.connect(self._to_custom)
        row_btns.addWidget(self.btn_apply_preset)
        row_btns.addWidget(self.btn_to_custom)
        right_box.addLayout(row_btns)

        row_analyze = QHBoxLayout()
        self.btn_analyze = QPushButton(self.tr("分析重复度"))
        self.btn_analyze.clicked.connect(self._emit_analyze)
        self.btn_start = QPushButton(self.tr("开始去重"))
        self.btn_start.clicked.connect(self._emit_dedup)
        row_analyze.addWidget(self.btn_analyze)
        row_analyze.addWidget(self.btn_start)
        right_box.addLayout(row_analyze)
        split.addLayout(right_box, 1)
        root.addLayout(split, 1)

        # ---- 底：去重结果条 + 报告 ----
        self.result_bar = DedupResultBar()
        self.result_bar.regenerate_requested.connect(self._regenerate)
        root.addWidget(self.result_bar)
        self.report_view = ReportView()
        root.addWidget(self.report_view, 1)

        # 勾选数量反馈到开始按钮
        self.asset_list.itemChanged.connect(lambda _item: self._refresh_btns())
        # 右键：定位/复制/系统播放器打开
        self.asset_list.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu,
        )
        self.asset_list.customContextMenuRequested.connect(
            self._show_asset_menu,
        )
        # 初始套用默认档（中度），保证编辑器非空；有记忆则恢复上次档位
        self._apply_preset()
        if config is not None:
            saved = self._saved_preset()
            if saved and saved in self._preset_radios:
                self._preset_radios[saved].setChecked(True)
        self._refresh_btns()

    def _saved_preset(self) -> str:
        if self._config is None:
            return ""
        try:
            saved = self._config.get("dedup_preset")
        except Exception:
            return ""
        return str(saved) if isinstance(saved, str) else ""

    def _step_texts(self) -> list[str]:
        """步骤条文案（语言切换时重取 tr）。"""
        return [
            self.tr("左侧勾选素材"),
            self.tr("「分析重复度」后选方案（轻/中/重度或自定义）"),
            self.tr("「开始去重」提交"),
        ]

    def _save_preset(self, pid: str) -> None:
        if self._config is not None:
            self._config.set("dedup_preset", pid)

    def _refresh_btns(self) -> None:
        n = len(self.checked_paths())       # 实际勾选数
        self.btn_analyze.setText(
            self.tr("分析重复度（{}）").format(n) if n else self.tr("分析重复度"),
        )
        self.btn_start.setText(
            self.tr("开始去重（{}）").format(n) if n else self.tr("开始去重"),
        )
        self.btn_analyze.setEnabled(n > 0)
        self.btn_start.setEnabled(n > 0)

    # ---- 数据 ----
    def set_assets(self, paths: list[str]) -> None:
        self.asset_list.blockSignals(True)
        self.asset_list.clear()
        for p in paths:
            # 只显示文件名，完整路径放 Tooltip（长路径更易读）
            item = QListWidgetItem(Path(p).name)
            item.setData(Qt.ItemDataRole.UserRole, p)
            item.setToolTip(p)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.asset_list.addItem(item)
        self.asset_list.blockSignals(False)
        refresh_empty = getattr(self.asset_list, "_refresh_empty_state", None)
        if refresh_empty is not None:
            refresh_empty()
        self._refresh_btns()

    def checked_paths(self) -> list[str]:
        out = []
        for i in range(self.asset_list.count()):
            item = self.asset_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                p = item.data(Qt.ItemDataRole.UserRole)
                out.append(str(p) if p else item.text())
        return out

    def _show_asset_menu(self, pos: Any) -> None:
        item = self.asset_list.itemAt(pos)
        if item is None:
            return
        path = str(item.data(Qt.ItemDataRole.UserRole) or item.text())
        menu = QMenu(self)
        act_reveal = menu.addAction(self.tr("打开所在文件夹"))
        act_copy = menu.addAction(self.tr("复制路径"))
        act_play = menu.addAction(self.tr("用系统播放器打开"))
        act_play.setEnabled(Path(path).is_file())   # 文件已不存在则置灰
        chosen = menu.exec(self.asset_list.viewport().mapToGlobal(pos))
        if chosen is act_reveal:
            if not reveal_in_file_manager(path):
                Toast.show_message(
                    self, self.tr("打开文件管理器失败"), error=True,
                )
        elif chosen is act_copy:
            copy_to_clipboard(path)
        elif chosen is act_play:
            # 菜单构建时文件存在，但打开前可能被移动/删除，或系统无
            # 关联播放器——失败必须提示而非静默
            opened = QDesktopServices.openUrl(QUrl.fromLocalFile(path))
            if not opened:
                Toast.show_message(
                    self,
                    self.tr("打开失败：文件不存在或系统没有关联的播放器"),
                    error=True,
                )


    def render_report(self, report, before_pct=None, after_pct=None):   # type: ignore[no-untyped-def]
        self.report_view.show_report(report, before_pct, after_pct)
        if report is not None and self._schemes is not None:
            # ReportBuilder 落库的 overall_score 恒为 0~100 百分数
            recommended = self._schemes.recommend(float(report.overall_score) / 100.0)
            self.mark_recommended(recommended)

    def show_dedup_result(self, summary: dict[str, Any]) -> None:
        """任务终态回填：页面内持久展示去重结果（toast 之外的第二通道）。"""
        self.result_bar.set_summary(summary)

    def _regenerate(self, srcs: list[str]) -> None:
        """「输出已存在」跳过后的重跑：确认 → 删旧输出 → 按当前方案重新提交。"""
        if not srcs:
            return
        outputs = self.result_bar.regen_outputs()
        n = len(srcs)
        answer = QMessageBox.question(
            self,
            self.tr("重新生成去重输出"),
            self.tr(
                "将删除「已去重」目录下 {n} 个同名输出文件，"
                "并按当前方案重新去重。继续？",
            ).format(n=len(outputs) or n),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        errors: list[str] = []
        for out in outputs:
            try:
                out.unlink()
            except OSError as exc:
                errors.append(f"{out.name}：{exc}")
        if errors:
            Toast.show_message(
                self,
                self.tr("删除旧输出失败：{msgs}").format(
                    msgs="；".join(errors[:3])),
                error=True,
            )
            return
        params = self.current_params()
        self._last_submit_key = (tuple(srcs), str(params))
        self._last_submit_ts = time.monotonic()
        self.dedup_requested.emit(srcs, params)

    def mark_recommended(self, preset_id: str) -> None:
        """推荐档徽标：记录推荐档并刷新三张卡片标题/说明文案。"""
        self._recommended_id = preset_id
        self._refresh_preset_labels()

    def _preset_base_names(self) -> dict[str, str]:
        return {"light": self.tr("轻度"), "mid": self.tr("中度"),
                "heavy": self.tr("重度")}

    def _preset_descriptions(self) -> dict[str, str]:
        return {
            "light": self.tr("保守调整：轻度镜像/微裁切/轻调色，画质损失最小"),
            "mid": self.tr("多手法组合：推荐日常使用，重复度下降明显"),
            "heavy": self.tr("强力规避：全部手法叠加，适合重复度很高的素材"),
        }

    def _refresh_preset_labels(self) -> None:
        """卡片标题（含推荐档徽标）与说明文案统一刷新（语言切换也走这里）。"""
        names = self._preset_base_names()
        descs = self._preset_descriptions()
        for pid, radio in self._preset_radios.items():
            badge = self.tr("（推荐档）") if pid == self._recommended_id else ""
            radio.setText(names[pid] + badge)
            desc = self._desc_labels.get(pid)
            if desc is not None:
                desc.setText(descs[pid])

    def current_params(self) -> list[dict[str, object]]:
        return self.editor.collect()

    # ---- 槽 ----
    def _select_all(self) -> None:
        # blockSignals：逐条 setCheckState 会各触发一次 itemChanged→
        # _refresh_btns，量大时明显卡顿；收尾统一刷一次
        self.asset_list.blockSignals(True)
        try:
            for i in range(self.asset_list.count()):
                self.asset_list.item(i).setCheckState(Qt.CheckState.Checked)
        finally:
            self.asset_list.blockSignals(False)
        self._refresh_btns()

    def _current_preset(self) -> str:
        button = self.radio_group.checkedButton()
        return str(button.property("preset_id")) if button is not None else "mid"

    def _apply_preset(self) -> None:
        pid = self._current_preset()
        params = (self._schemes.instantiate(str(pid)) if self._schemes
                  else default_params(str(pid)))
        self.editor.set_items(params)

    def _to_custom(self) -> None:
        pid = self._current_preset()
        params = (self._schemes.preset_to_custom(str(pid)) if self._schemes
                  else default_params(str(pid)))
        self.editor.set_items(params)

    def _emit_analyze(self) -> None:
        srcs = self.checked_paths()
        if not srcs:
            Toast.show_message(
                self, self.tr("请先在左侧勾选素材，再分析重复度"),
            )
            return
        if self._is_duplicate_submit((tuple(srcs), "compare")):
            return
        self._last_submit_key = (tuple(srcs), "compare")
        self._last_submit_ts = time.monotonic()
        self.analyze_requested.emit(srcs)

    def _emit_dedup(self) -> None:
        srcs = self.checked_paths()
        if not srcs:
            Toast.show_message(self, self.tr("请先在左侧勾选素材，再开始去重"))
            return
        params = self.current_params()
        # 防连点重复提交：同批素材同参数 3 秒内重复点击直接拦截
        key = (tuple(srcs), str(params))
        if self._is_duplicate_submit(key):
            return
        self._last_submit_key = key
        self._last_submit_ts = time.monotonic()
        self.dedup_requested.emit(srcs, params)

    def _is_duplicate_submit(self, key: tuple[object, ...]) -> bool:
        """3 秒内同 key 的提交视为连点（防重复入队整批任务）。"""
        if (self._last_submit_key == key
                and time.monotonic() - self._last_submit_ts < 3.0):
            Toast.show_message(self, self.tr("该批任务已提交，请勿重复点击"))
            return True
        return False

    def retranslate(self) -> None:
        """语言切换：静态文案 + 卡片标题/说明 + 动态按钮重翻译。"""
        self._step_hint.set_steps([
            self.tr("左侧勾选素材"),
            self.tr("「分析重复度」后选方案（轻/中/重度或自定义）"),
            self.tr("「开始去重」提交"),
        ])
        self._asset_label.setText(self.tr("待去重素材"))
        self._scheme_label.setText(self.tr("去重方案"))
        self.btn_all.setText(self.tr("全选"))
        self._empty.set_texts(
            self.tr("暂无素材"),
            self.tr("先到「采集工作台」下载素材，\n或把视频文件放入工作目录"),
        )
        self._refresh_preset_labels()
        self._refresh_btns()
        self.btn_apply_preset.setText(self.tr("套用预设"))
        self.btn_to_custom.setText(self.tr("预设→自定义微调"))
        self.editor.retranslate()
        self.result_bar.retranslate()
        self.report_view.retranslate()


def make_registry() -> TechniqueRegistry:
    from ych.core.m3_dedup.techniques.registry import make_default_registry

    return make_default_registry()


def _set_card_checked(card: QWidget, checked: bool) -> None:
    """卡片选中态 → 动态属性 + 重刷 QSS（presetCard[checked] 规则）。"""
    card.setProperty("checked", checked)
    style = card.style()
    style.unpolish(card)
    style.polish(card)


def default_params(preset_id: str) -> list[dict[str, object]]:
    """无 SchemeManager 实例时的静态参数兜底（取各区间中值）。"""
    from ych.core.m3_dedup.scheme_manager import PRESETS

    preset = PRESETS.get(preset_id)
    if preset is None:
        return []
    out: list[dict[str, object]] = []
    for tid, param_ranges in preset.techniques:
        params: dict[str, object] = {}
        for key, (lo, hi) in param_ranges.items():
            params[key] = round((float(lo) + float(hi)) / 2.0, 3)
        out.append({"id": tid, "params": params})
    return out
