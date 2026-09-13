# 交接：i18n 运行时切换 + 交互"点了没反应"全面修复（2026-09-12 晚）

> **2026-09-13 更新：第三节收尾已全部完成并提交。** 收尾中离屏验证（真实
> 运行时顺序：中文构建→装包→retranslate）另发现并修复 3 类 retranslate
> 漏网：① 画布占位提示（PySide6 ≥6.7 `pixmap()` 返回 null QPixmap 而非
> None，`is None` 判断恒 False）；② DedupPage 未调 report_view/editor 的
> retranslate（且编辑器重建改用 collect() 保用户已调参数）；③ SettingsPage
> AI 预设 chip/表单标签/服务页按钮等 10 余处构造期 tr 漏更新（已留引用 +
> 动态占位源串跟踪），MANUAL 词典补 4 条变量 tr 源串。新增运行时顺序回归
> 用例 test_retranslate_runtime_order_no_stale_chinese。ruff/mypy/pytest
> 全绿，三主题渲染 0 QCssParser 警告。

## 一、任务背景（用户需求）

用户要求：把上一轮审计发现的问题"完美解决"，并举一反三清除所有同类问题，
做到 **没有任何"点击没反应"或"货不对板"**。发现的问题：

1. 语言切换运行中点了没反应（无重翻译机制、无提示）→ **已修**
2. i18n "tr() 全覆盖" 名不符实（大量可见文案未包 tr，英文模式混排中文）→ **已修**
3. 选项记忆恢复"手动框选"但画布框选不持久 → 提交静默空转 → **已修（提交前校验）**
4. 双击预览加载中再次双击被静默忽略 → **已修（Toast 提示）**
5. "跟随系统"主题不实时跟随系统深浅色 → **已修（colorSchemeChanged）**
6. 举一反三新发现并已修：
   - **theme.qss 令牌前缀覆盖 bug**（@primary 抢占 @primary_h → `#5d7bf9_h`
     非法颜色，双主题多处被污染）→ theme.py 按令牌长度降序替换
   - 导航栏文案从未被翻译（`self.tr(变量)` lupdate 提取不到）
   - AI 预设供应商名（阿里云百炼等）未翻译
   - 手法编辑器参数标签未翻译（经 MANUAL 手工条目进 .ts）
   - result_list 双击无来源页链接静默无反应 → Toast
   - 右键菜单"打开来源页/复制下载链接"无对应链接时仍可点 → 按可用性置灰
   - 去重列表"用系统播放器打开"文件不存在仍可点 → 置灰
   - 搜索/下载/查看文件的 `_xxx is None` 分支静默 return → Toast
   - 设置页 config 外部变更不回填 lang/theme 下拉 → 补回显
   - AssetTree 重建（重翻译路径）丢失勾选状态 → 保留勾选 + 父级三态校准
   - download_queue_view 死代码 `_STATE_TEXT` 清除；行状态记录支持重翻译

## 二、已完成的改动（未提交，git status 可见）

### 核心机制
- `src/ych/ui/u6_common/theme.py`：colorSchemeChanged 实时跟随（mode=system 时）；
  render_theme 按 token 长度降序替换修前缀覆盖 bug
- `src/ych/ui/u6_common/empty_state.py`：EmptyState.set_texts()
- `src/ych/ui/u6_common/step_hint.py`：StepHint.set_steps()
- `src/ych/ui/u2_preprocess/box_select_canvas.py`：占位提示 tr + retranslate
- `src/ych/ui/u2_preprocess/asset_tree.py`：tr 化 + _rows 缓存 + 重建保留勾选 +
  _recompute_parent_states
- `src/ych/ui/u1_capture/result_list.py`：全量 tr + 菜单置灰 + 无链接 Toast +
  retranslate（含卡片文本重翻译）
- `src/ych/ui/u1_capture/download_queue_view.py`：tr + 行状态记录 + retranslate
- `src/ych/ui/u1_capture/filter_panel.py`：tr + retranslate
- `src/ych/ui/u1_capture/capture_page.py`：Toast/对话框 tr + _searching 状态 +
  None 分支 Toast + retranslate
- `src/ych/ui/u2_preprocess/option_panel.py`：表单标签 tr 化 + retranslate
- `src/ych/ui/u2_preprocess/preprocess_page.py`：**manual 空框选拦截
  （_manual_without_regions）** + 加载中 Toast + retranslate
- `src/ych/ui/u3_dedup/dedup_page.py`：档位卡片标签重构（_preset_base_names/
  _preset_descriptions/_refresh_preset_labels，替换硬编码中文的
  mark_recommended）+ retranslate
- `src/ych/ui/u3_dedup/scheme_editor.py`：标题/手法名/参数标签走 tr()
- `src/ych/ui/u3_dedup/report_view.py`：全量 tr + _last 缓存重渲染 + retranslate
- `src/ych/ui/u4_failures/failure_page.py`：表头可翻译（FailRecordModel.headers
  实例化 + retranslate）+ 按钮引用 + retranslate
- `src/ych/ui/u5_settings/settings_page.py`：残留文案 tr + 全部控件引用 +
  retranslate + _on_config_changed 补 theme/language 回显
- `src/ych/ui/u0_main/main_window.py`：add_page 记录页面列表 + **MainWindow.retranslate()
  级联所有页面** + HelpDialog/ensure_workdir 对话框 tr
- `src/ych/ui/u5_settings/ai_presets.py`：未改（品牌名经 MANUAL 词典翻译）
- `src/ych/ui/u6_common/toast.py`、`player_widget.py`：tr + retranslate
- `src/ych/app.py`：**ctx.i18n().locale_changed.connect(window.retranslate)**；
  wire_task_feedback 全部 Toast tr 化（_Bridge 上下文）；_tr 助手

### 语言包链路（重要！维护流程）
- 工具：`.venv/Scripts/pyside6-lupdate.exe` / `pyside6-lrelease.exe`
- **再生成命令（顺序执行）**：
  1. `.venv/Scripts/pyside6-lupdate.exe src/ych/ui/*.py src/ych/ui/u0_main/*.py
     src/ych/ui/u1_capture/*.py src/ych/ui/u2_preprocess/*.py
     src/ych/ui/u3_dedup/*.py src/ych/ui/u4_failures/*.py
     src/ych/ui/u5_settings/*.py src/ych/ui/u6_common/*.py src/ych/app.py
     -ts i18n/zh_CN.ts i18n/en_US.ts`
     （注意：`-recursive src/ych/ui` 形式会漏子目录，必须显式列文件！）
  2. `.venv/Scripts/python.exe scripts/fill_i18n_translations.py`
  3. `.venv/Scripts/pyside6-lrelease.exe i18n/zh_CN.ts i18n/en_US.ts`
- `scripts/fill_i18n_translations.py`（新增）：
  - EN 词典：(context, source) → 译文，填 unfinished
  - MANUAL 词典：`self.tr(变量)` 类调用 lupdate 提取不到，手工维护条目
    （MainWindow 导航、OptionPanel 三态、FilterPanel 下拉项、SchemeEditor
    手法名/参数标签、SettingsPage AI 预设名）；每次 lupdate 会把这些标为
    vanished，脚本强制重置为 finished —— **所以每次改文案后必须重跑脚本**
  - FIXUPS：修正 lupdate 保留的旧差译文（如 分析重复度→"Analyze duplicates"）
  - zh_CN 恒等填充
- 当前 .qm：348 条全部 finished

### 测试
- `tests/unit/ui/test_i18n_retranslate.py`（新增，10 个用例全过）：
  语言切换无中文残留巡检（品牌白名单：源/抖音/快手/B站/小红书/已去重）/
  关键控件抽查/切回中文/manual 空框选拦截/加载中 Toast/无链接双击 Toast/
  右键菜单门控/主题跟随系统/素材树重建保留勾选/config 回填

## 三、明天要做的收尾（按顺序）

1. **修 ruff 4 个错误**：
   - `scripts/fill_i18n_translations.py:96` E501（手动框选长 key 拆行）
   - `scripts/ui_preview.py:87` E501（需确认是否误报——ruff 输出位置可疑，
     内容像测试文件里的 `__import__("PySide6.QtCore"...)` 行；先重跑 ruff 看）
   - `src/ych/ui/u5_settings/settings_page.py:951` B007（`val` 未用 → `_val`）
   - `tests/unit/ui/test_i18n_retranslate.py:45` SIM114（合并 if 分支）
   - 顺手：test_i18n_retranslate.py 里
     `leaf.setCheckState(0, __import__("PySide6.QtCore", fromlist=["Qt"]).Qt.CheckState.Checked)`
     改成顶部正常 import Qt
2. **确认全量测试通过**：
   `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/ -q`
   （中断前最后跑疑似全绿但被截断未确认数字）
3. **mypy**：`.venv/Scripts/python.exe -m mypy src/ych`（之前提交声称过 mypy
   干净，本次改动可能新增类型问题，重点看 retranslate 新方法）
4. **最终离屏验证**（一次脚本）：
   - 构建 MainWindow+pages → 装 en_US.qm → window.retranslate() →
     无 QCssParser 警告（验证 theme token 修复）+ 无中文残留
   - 切 deep 主题无警告
5. **git 提交**（单 commit 即可）建议 message：
   `语言运行时切换即时生效 + 交互无死点全面修复 + 深色主题令牌污染修复`
6. 向用户交付总结（问题1-5 全部闭环 + 举一反三清单）

## 四、给明天会话的提示

- 用户中文交流；语言包源语言是中文（zh 恒等），英文走 .ts
- 测试跑法：必须 `QT_QPA_PLATFORM=offscreen`，解释器用 `.venv/Scripts/python.exe`
- 不要用 `-recursive` 跑 lupdate（会漏文件）；改了任何 tr 文案都要重跑
  fill 脚本 + lrelease，然后跑 test_i18n_retranslate.py 验证
- 本轮未动核心层（core/）业务逻辑，只动了 UI 层与 app 装配
- git：改动未 add/commit；`git status` 应显示 ~20 个文件修改 + 2 个新增
  （fill_i18n_translations.py、test_i18n_retranslate.py）+ i18n 四件套
