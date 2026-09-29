# 任务进度备忘（2026-09-13 深夜更新）

> 上一轮两项修复（排版 / 模型下载）已完成待提交；本轮「去重没反馈」已修复完毕。
> 9-13 二轮：「分析重复度假 0 分」根因修复（本地素材池兜底）+ 去重前后真实对比，待用户确认后一并提交。

## 2026-09-27 晚：采集页五项反馈修复 + 真实链路验证（未提交）

用户五项反馈全部修复，并按用户要求做了真实（非 mock）重复测试：

**修复清单**
1. **GBK 崩溃（"搜索后提示不可用"的真凶之一）**：`run_cli` 子进程在中文
   Windows 管道下默认 GBK 标准流，vendored CLI 打印 ℹ️ 即
   UnicodeEncodeError 崩溃。修复：子进程环境强制 `PYTHONIOENCODING=utf-8`，
   父进程按 utf-8 解码（`_douyin_backend._cli_env`）。
2. **滚动参数运行态失配**：模板已调优 60/4 但用户机 `config_plugin.yml`
   残留 240/8（read_live_config 不会回读模板）→ 滚动没完吃满 300s 超时。
   修复：`build_run_config` 按代码常量覆写 max_scrolls/idle_rounds。
3. **拉空重试**：抖音间歇性风控/限流会让 CLI 退出码 0 但作品列表拉空
   （实测 96s 全程滚动 0 产出）。修复：`DouyinPlugin.search` 拉空退避
   20s 重试一次（`_EMPTY_RESULT_RETRIES/_EMPTY_RETRY_BACKOFF_S`），
   硬失败不重试。
4. **死代理自动直连兜底**：用户机 Clash 关闭后，HttpClient 硬走
   127.0.0.1:7897 死代理 → Pexels/Pixabay 全灭（18:55 日志实证）。
   修复：HttpClient 统一 `send()` 入口（get/post/head/probe 全走它），
   ProxyError → 标记 5 分钟死亡窗口并直连重试，窗口自愈；代理配置变更
   清窗口。`plugin_base.api_get` 改走统一入口。
5. **UI 三处**：
   - 采集页关键词输入与历史合并为一个可编辑下拉（长 URL 历史项不再把
     输入框挤成 40px）；搜索完成后历史即时刷新。
   - 结果列表「暂不可用平台」带中文原因（码→文案映射 + 明细截断 +
     Tooltip 全文）；协调器把 AppError 明细随码一起传给 UI。
   - 去重工作台待去重素材由扁平列表改为与预处理一致的三级树
     （复用 AssetTree；app.py 喂完整 AssetRow；纯路径串归「未分类」组）。
   - 登录态小字补充 Tooltip（Cookie 本机持久，重启无需重登）。
6. i18n 补齐（上次会话 lupdate 漏扫 main_window.py 导致 251 条 vanished，
   已按全量 UI 文件重跑 lupdate→fill→lrelease，417 条全 finished）；
   settings_page 协议补 `spec`（上次会话遗留 mypy 错误）。

**真实测试记录（2026-09-27 晚，用户主页链接）**
- Pexels/Pixabay 直连（代理关）：各 30 条 × 4 次 ✓
- Pexels/Pixabay 死代理开启（内存配置模拟 Clash 未运行，真 Key）：
  自动直连兜底各 30 条 ✓
- 抖音主页链接（插件全路径）：python 子进程 18 条 × 2 次 ✓（其中一次
  为拉空重试后成功）；pythonw 子进程（模拟应用启动）18 条 ✓；
  另一次持续风控重试仍空（服务端限流，属平台侧）
- 手动 CLI 对照：18/18 成功（配置与插件路径一致）
- 单测 429 全过；ruff/mypy 干净

**遗留**：抖音持续风控窗口（约数分钟）内重试也无效，报错文案已明示
"请稍后再试/完成浏览器验证"；用户机代理设置当前为关闭状态。

## 2026-09-18 项目规范落地（已提交 9679189 / 5a2a65e）

按项目自身约定（CONTRIBUTING.md + design/max_design.md）补齐工程规范，未改动业务逻辑：

1. `pyproject.toml` 补全元数据：authors / classifiers / urls / `ych` 命令行入口 /
   coverage 配置（branch + source=ych + omit 测试与脚本）。
2. `README.md` 开发者段补：ruff/mypy 检查命令、覆盖率门禁口径、release tag 流程、
   项目目录结构图（对应 design/max_design.md 第三章）。
3. `.gitignore` 新增 `runtime/models/*.onnx`：模型体积大、由
   `model_downloader` 按需拉取，不入仓库。
4. `scripts/env_check.py`（新）：启动前环境预检——ffmpeg/ffprobe 可执行性 +
   必备模型存在性 + 可选外网探测；所有探测异常被捕获，绝不抛出；
   支持 `--verbose` / `--json`（CI 解析）/ `--net`。
5. `src/ych/app.py:main()` 启动时静默调用 env_check（仅 logger 提示），
   不阻断启动；模型缺失时降级为经典特征，功能不崩溃（与 D2 降级策略一致）。
6. 开源配套：`CHANGELOG.md`（Keep a Changelog）、`SECURITY.md`、
   `.gitattributes`（onnx/ico 走 Git LFS）、`.github/ISSUE_TEMPLATE/` 与
   `.github/PULL_REQUEST_TEMPLATE.md`。
7. 校验：`ruff check` 全绿；`pytest tests/unit` 连跑 5 遍全绿（424 例）；
   `python -m scripts.env_check --verbose` 退出码 0（模型缺失仅提示）。

遗留：`test_robustness_regression` 仍为合跑偶发失败（单跑全过），未根治；
`compare` 全平台不可用时报告恒 0 分/空列表（体验问题，未修）。

## 9-13 二轮：重复度分析真实性修复（均未提交）

**查实结论**（用户质疑"假数据"属实）：
- 去重本身是真的：9-6 任务 #25 已对 7 条素材做镜像+裁切+调色，输出在
  `已去重/<大类>/<关键词>/<日期>/`；帧级像素对比验证输出与源差异显著。
  9-13 的「成功 0 条跳过 N 条」= 输出已存在按设计跳过，非失败。
- 分析重复度此前**恒为空**：auto 模式只搜在线平台（douyin/kuaishou/bilibili），
  用户环境全部探测不可用 → compare_report 7 条记录 targets 全空、overall 恒 0；
  去重前后对比依赖该报告 → 恒 (None, None)。

**修复**：
1. `core/m3_dedup/local_pool.py`（新）：同关键词本地素材池（回退同大类），
   剥"已去重"层、排除自身与 _deduped/_cleaned 产物、同日期优先、limit 截断。
2. `candidate_searcher.py`：auto/both 模式先给本地池逐条打分（source="local"，
   platform_id="local"），在线平台作为补充；_keyword_from_archive 委托
   keyword_of（顺带修了无日期层时取错段的 bug）。
3. `dedup_pipeline._compare_scores`：历史报告无可比 targets 时回退本地池，
   before/after 用同一批对象真实重算（此前依赖报告，无报告恒空）。
4. `feature_extractor.py`：(path, mtime_ns, size) 键 LRU 特征缓存（64 条），
   分析+去重前后共用特征不重复抽帧。
5. UI 诚实化：report_view 无有效对比对象时总分显示"—"并明示原因
   （在线平台不可用/素材不在归档结构）+「0 分不代表重复度低」；
   有 unavailable 时注明结果基于其余对象（本地库 m 个）；来源列新增 本地库/
   本地素材库。app.py 分析完成 toast 带真实数字（最高相似度/对比总数/本地+在线）。
6. dedup summary 新增 skipped_srcs；DedupResultBar 新增「重新生成」按钮
   （确认后删旧输出按当前方案重跑）——解决改方案后点开始静默跳过的死循环。
7. compare 任务接 on_progress → 状态栏有进度；schema CompareTarget.source
   加 "local"；config 新增 compare_local_pool_max=8。
8. i18n 12 条新文案走 lupdate→fill→lrelease 全流程（397 条全 finished）。
9. 测试：新增 tests/unit/core/m3/test_dedup_local_analysis.py（13 例：池规则/
   搜索器兜底/前后对比/缓存）；test_ui_smoke compare 分支扩两态；unit 424 过
   （连跑 2 遍全绿）+ integration 14 过 + ruff/mypy 干净。
10. **顺手修掉 m5 archive_race flaky**：并发首连时 journal_mode=WAL 切换在
    某些争用路径不进 busy_timeout 而即时 SQLITE_BUSY → database.py connection()
    对 locked/busy 类错误做 3 次小退避重连（此前"SQLite 并发首连"加固的补刀），
    竞态测试连跑 6 次全过。
11. 真实数据端到端实测（砍木头视频_002，CLIP 缺失降级经典特征）：
    本地池 3 条同关键词素材；原始素材重复度 95.0%（构图 .904/运镜 .991/节奏 1.0），
    已去重输出 94.1%。**暴露真实短板**：缺 CLIP 判别力弱（分数虚高），且
    镜像/裁切/调色基本不动运镜与节奏维度 → 中度方案对同关键词相似度降幅 <1 分。

## 上一轮已完成（本次会话，均未提交 git）

1. **前端排版修复**（已验证 ✅）
   - `src/ych/ui/u3_dedup/scheme_editor.py` 表单包进常驻 QScrollArea，高分屏 + 矮窗口不再叠压。

2. **模型下载修复**（已验证 ✅）
   - `model_downloader.py` 修正 subtitle/inpaint 源 URL；`http_client.py` 拦截非 200/206 状态码；补 3 个回归测试。
   - 副产品：subtitle 模型已下载到位并校验通过。

3. **去重零反馈修复**（本轮新增，已验证 ✅）
   - **实测结论**：真实 pipeline 对 6.9s/331KB 素材完整去重仅 **1.66s**（ffmpeg ~1s + 特征提取 0.45s）；
     任务库 #25（9-6）7 条素材共 6s。**去重本身很快，「去重这么久」是零反馈造成的感知问题。**
   - 附带查清：用户环境 CLIP 模型缺失 → 构图特征降级经典特征（可正常运行）；
     compare 报告 targets 全空（采集平台全不可用），故去重前后对比恒为空——属另一问题，本轮未动。
   - **修进度回报**（`context.py _handle_dedup`）：on_progress 由 None 改为按条映射批次进度
     `(idx+ratio)/total`，0.25s 限频，经 `scheduler.emit_progress` 上报 → 底部状态栏平均进度实时可见
     （此前恒 0%）。条目收尾（含跳过/失败/取消）强制补发 `(idx+1)/total`。
   - **修完成反馈**（`app.py _dedup_done` + `dedup_page.py` 新增 DedupResultBar）：
     - 全部「输出已存在」跳过 → toast 明确文案：「输出已存在，未重新处理：<文件名>（如需重新生成，
       请删除「已去重」目录下的同名文件）」，timeout 8000ms；
     - 部分跳过/失败 → toast 逐条带文件名/原因（取消型跳过不计入「已存在」，避免误导）；
     - 新增页面内持久结果条（结果不随 toast 消失）：成功/跳过/失败计数 + 明细 + 「打开输出目录」按钮
       （定位到 `已去重/...` 目录）。
   - summary 新增键：`skipped_names` / `failed_msgs` / `output_dir`（落库 result_summary，向后兼容）。
   - **i18n**：14 条新文案走 lupdate → fill_i18n_translations.py（补 EN 词典）→ lrelease 全流程，
     英文模式无中文残留（test_i18n_retranslate 验证过）。
   - **测试**：新增 `tests/unit/core/m4/test_m4_dedup_handler.py`（进度透传/全跳过明细/失败明细 3 例）；
     `test_ui_smoke.test_wire_task_feedback` 扩展全跳过分支断言。回归 m3/m4/UI/集成共 138 项全过；
     ruff/mypy 干净。

## 下一步

1. **待用户确认后提交**：改动文件清单
   - `src/ych/context.py`、`src/ych/app.py`、`src/ych/ui/u3_dedup/dedup_page.py`
   - `src/ych/ui/u3_dedup/scheme_editor.py`、`src/ych/services/s2_ai/model_downloader.py`、
     `src/ych/services/s4_net/http_client.py`
   - `tests/unit/services/s4_net/test_download_stream_errors.py`、`tests/unit/core/m4/test_m4_dedup_handler.py`、
     `tests/unit/ui/test_ui_smoke.py`
   - `i18n/` 四件套（zh/en 的 .ts/.qm）、`scripts/fill_i18n_translations.py`
   - 本备忘文件
2. 已知遗留（用户未要求修，不动）：
   - test_robustness_regression 合跑 6 个既有失败（测试隔离问题，单跑全过）；
   - compare 全平台不可用 → 分析报告恒 0 分/空列表（体验问题，可另行处理）；
   - `runtime/models/` 两个 onnx 为运行时下载产物，已在 .gitignore 范围外（未跟踪），提交时注意别误加。

## 环境备忘

- 启动：`启动应用.bat`（.venv pythonw -m ych.app）；工作目录 `D:\tmp_ych_workdir`；素材在 `木头/木头/2026-09-05/`。
- 日志：`C:/Users/rememberme/AppData/Local/YuChongGou/logs/app.log`（不是项目内 logs/）。
- 任务库：`C:/Users/rememberme/AppData/Local/YuChongGou/app.db`（process_task / download_task / compare_report）。
- 显示缩放 150%（DPR 1.5）；离屏渲染无 CJK 字体（截图显方块，靠文本断言 + 几何验证）。
- i18n 维护流程（改任何 tr 文案必走）：lupdate（显式列文件，勿 -recursive）→ fill 脚本 → lrelease。
- 已知失效源：HuggingFace 上 `RapidAI/RapidOCR` 已转鉴权仓库；`Carve/LaMa-ONNX` 的 lama_fp32_512.onnx 已下架。
- 9-13 晚观察：`已去重/` 下 9-6 的输出文件仍在（mtime 9-6），备忘录早前「目录为空」应为回收站还原前状态。
