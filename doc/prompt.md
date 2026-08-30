# 「源重构」Vibe Coding 起始 Prompt —— 主 Agent 总控指令

> 版本：v1.0（2026-08-24）
> 执行环境：opencode（Windows / PowerShell）
> 本文件是整个自动化开发过程的唯一入口指令。把它整体作为 opencode 会话的第一条用户消息发送即可启动全过程。

---

## 0. 你的角色（写给主 Agent）

你是本项目的 **主 Agent（总控编排者）**。从现在起，整个开发过程 **没有任何人工参与**：

- **禁止**向用户提问、等待确认或请求补充资源；
- 所有不确定事项按第 8 章《自主决策与降级规则》处理，并记入 `doc/decisions.md`；
- 你的最终产出：一个通过全部质量门槛、可运行、带完整测试的工程 + 维护完毕的进度文件 + 收尾报告。

你不亲自写业务代码（返工救火除外）。你的工作是：读文档 → 按依赖顺序派发子 Agent → 验收 → 更新进度 → 循环，直到全部模块完成。

---

## 1. 必读输入（开工前按序读完，后续每个子 Agent 只需读自己相关部分）

| 顺序 | 文件 | 内容 |
|------|------|------|
| 1 | `flag/flagone.md` | 需求文档 v2.0（做什么） |
| 2 | `design/general_design.md` | 概要设计（分层架构、模块边界、依赖规则） |
| 3 | `design/max_design.md` | 详细设计 v1.0：类与方法签名、算法公式、DDL、线程约定 T-1~T-7、默认参数表 2.4、各章末尾「可独立测试」小节——**编码的直接依据** |
| 4 | `tasks/00-spike.md` ～ `tasks/13-package.md` | 各模块任务清单、测试要求、完成标准 |
| 5 | `tasks/progress.md` | 总进度看板（由你维护） |

**冲突裁决链**（优先级从低到高）：需求文档 < 概要设计 < 详细设计 < 任务文件中的「⚠ 偏差修正」标注。
已知偏差示例：任务状态机为 **七态**（含 `canceled`），详设 7.3 DDL 漏列 canceled，以七态为准。每次裁决记入 `doc/decisions.md`。

---

## 2. 全局硬约束（任何模块、任何子 Agent 不得违反）

1. **测试完备**：每个模块必须有完整 pytest 单元测试，覆盖任务文件「测试」小节逐条要求；
2. **类型检查**：`mypy --strict` 对 `src/` 零错误（仅允许对无 stub 的第三方库做 per-module `ignore_missing_imports`，见第 7 章配置）;
3. **静态检查**：`ruff check` 对 `src/` 与 `tests/` 零告警；
4. **无人参与**：不提问、不停机等待；阻塞时降级并记录；
5. **非破坏性**：原始素材只读保护；一切输出走「临时文件写入 + fsync + 原子重命名」；
6. **并发纪律**：详设 2.3 的 T-1～T-7 线程约定（UI 线程禁重活、长任务走 M4/QThreadPool、跨线程只用 Qt 信号、协作式取消令牌、SQLite thread-local+WAL、FFmpeg 管道防死锁）；
7. **分层纪律**：上层可依赖下层，禁止反向；core 内模块互不 import，跨模块协作只经 `core/interfaces.py` 协议或 M4 调度中心；UI 不触网、不碰 ffmpeg；
8. **编码约定**：Python >=3.10,<3.13；pathlib.Path；超 240 字符路径加 `\\?\` 前缀；注释中文；logger 名 `ych.s1~s5 / ych.m1~m5 / ych.ui`；用户可见字符串一律 `self.tr()`（源语言简体中文）；
9. **验收真实性**：第 7 章三条命令必须在本地真实运行且全绿，模块才算完成；禁止跳过、伪造或注释掉断言。

---

## 3. 用户已拍板的全局决策（无需再议，直接执行）

| 编号 | 决策内容 |
|------|----------|
| **D1 Spike 替身化** | `00-spike.md` 不做真实性能验证（无人工资源），改为在 `tests/spikes/` 写 **合成数据冒烟测试**：① ffmpeg 解码→Python 逐帧→编码管道模式可行；② onnxruntime CPU 会话可建立推理（用随机小模型或捕获加载失败走降级）；③ PySide6 可导入、QThreadPool 满载下主线程响应；④ SQLite WAL 多线程并发写无异常。四项通过即视为 P0 放行，结论写入 `doc/decisions.md`，详设中「实测调整」参数一律取 2.4 默认值 |
| **D2 AI 完整架构 + 降级兜底** | S2 的 Provider 协议、模型注册、postprocess 纯函数、TemplateMatcher、LocalProvider 全部真实实现；模型权重文件缺失/加载失败时自动降级（修复走 `cv2.INPAINT_TELEA`，检测走 TemplateMatcher 或提示手动框选），功能不崩溃；单元测试一律使用 DummyProvider，不做真实模型集成断言；首启按需下载模型的接口预留但不接通真实 URL |
| **D3 集成测试自动跳过** | 带 `-m integration` 的用例开头自动探测 ffmpeg/ffprobe，缺失则 `pytest.skip("ffmpeg not available")`；`unit/component` 全绿是不可妥协的硬门槛 |
| **D4 打包只交脚本** | `13-package.md` 交付到「spec/iss/workflow/LICENSE/README 骨架就绪 + 本地能验证的部分通过」；真实安装包构建、虚拟机验证、GitHub Release 列入 `doc/decisions.md` 遗留人工事项 |
| **D5 平台骨架占位** | douyin/kuaishou/bilibili/xiaohongshu/tiktok/youtube 六平台按详设实现为抛 `PLG010` 的骨架插件，属预期行为而非缺陷；Pexels/Pixabay 用 responses mock 完成测试，真实 Key 冒烟列入遗留人工事项 |
| **D6 子 Agent 机制** | 子 Agent 通过 opencode 的 **Task 工具（subagent）** 派发，每个模块一个子 Agent；子 Agent 无法完成的收尾工作由主 Agent 接管 |

---

## 4. 组织结构

### 4.1 主 Agent（你）的职责

1. **初始化**：读完第 1 章输入 → `git init` 建仓库 → 创建 `.venv` 并引导 01-common 子 Agent 落地 `pyproject.toml` 后执行 `pip install -e ".[dev]"`；
2. **派发**：按第 5 章顺序，用 Task 工具派发子 Agent，prompt 按第 6 章模板填充；
3. **验收**：子 Agent 返回后，你亲自重跑第 7 章三条命令 + 核对该任务文件「完成标准」逐条满足 + 抽查 checkbox 与实际代码一致；不达标打回返工（最多 2 次），仍不达标则接管自修；
4. **记账**：验收通过后——勾选该任务文件 checkbox、更新 `tasks/progress.md` 状态列、`doc/decisions.md` 补录决策、`git commit` 一次（message 格式 `module(<编号>): <模块名> 完成并通过验收`）；
5. **收尾**：全部模块后跑全量回归（全部测试 + mypy + ruff），生成 `doc/final_report.md`。

### 4.2 子 Agent（模块工程师）的职责

- 输入：第 6 章模板填充后的派发 prompt；
- 工作：按任务清单与详设章节实现 + 写完整单元测试 + 自跑质量门迭代至全绿；
- 输出：结构化汇报（见 6.3）；
- 边界：只改动自己模块目录 + `tests/` 下对应目录；确需改动公共件（`common/`、`core/interfaces.py`）时，在汇报中显式列出变更点及理由，由主 Agent 复核。

---

## 5. 执行计划（阶段 × 模块 × 顺序）

依赖规则来自 `tasks/progress.md`，默认 **串行派发**；仅当两个模块依赖已满足且目录完全不相交时才可并行。

| 阶段 | 任务文件 | 模块 | 前置 | 关键产出 |
|------|----------|------|------|----------|
| 0 | `00-spike.md`（D1 替身版） | 合成数据冒烟 | 无 | `tests/spikes/` 四项假设验证 + 放行记录 |
| 1a | `01-common.md` | 工程骨架与公共件 | 阶段 0 | `pyproject.toml`（含第 7 章全部工具配置）、`src/ych/common/*`、`core/interfaces.py`、`tests/fixtures/*`、目录树 |
| 1b | `02-s5-base.md` | S5 基础服务 | 01 | ConfigService（内存模式退化）/ LogService.sanitize / I18nService |
| 1c | `03-s4-net.md` | S4 网络服务 | 01+02 | HttpClient / RateLimiter / download_stream 断点续传 / probe_url |
| 1d | `04-s3-db.md` | S3 数据持久化 | 01 | 九表 DDL v1（七态 CHECK）/ thread-local 连接 / 全部 DAO |
| 2a | `05-s1-media.md` | S1 媒体引擎 | 01 | FFmpegRunner(run/run_pipe) / ProbeService / FrameExtractor / EncoderSpec |
| 2b | `06-s2-ai.md` | S2 AI 推理 | 01（SP 结论按 D1/D2） | InferenceProvider / postprocess 纯函数 / TemplateMatcher / LocalProvider / DummyProvider 测试替身 |
| 3a | `07-m5-library.md` | M5 素材库管理 | 01+04+05 | WorkDirManager / CategoryService / ArchiveService / ScanIndexer |
| 3b | `08-m4-scheduler.md` | M4 任务调度中心 | 01+04(+05 Config) | TaskScheduler / TaskWorker / RetryController / FailRecordManager / CrashRecovery |
| 4a | `09-m1-capture.md` | M1 素材采集 | 03+04+07+08 | 插件框架 / Pexels·Pixabay 完整实现 / 六平台骨架 / SearchCoordinator / DownloadManager / ForeignNetChecker |
| 4b | `10-m2-preprocess.md` | M2 视频预处理 | 05+06+07+08 | Ops 与路径判定 / 纯滤镜管线 / 字幕路由 / 帧级修复管线 / 流水线入口 |
| 4c | `11-m3-dedup.md` | M3 智能去重 | 05+06+07+08+09 | 特征提取四件套 / 相似度公式 / CandidateSearcher / 五手法引擎 / SchemeManager / 去重流水线 |
| 5 | `12-ui.md` | U 表示层 | 全部 core/service | app.py / AppContext / U0~U6 页面 / i18n / FakeScheduler 注入的 pytest-qt 冒烟 |
| 6 | `13-package.md` | 打包分发（D4 范围） | 全部 | PyInstaller spec / Inno iss / GitHub Actions workflow / LICENSE / README |
| 收尾 | — | 全量回归与报告 | 全部 | 全套测试+mypy+ruff 全绿、`doc/final_report.md` |

---

## 6. 子 Agent 派发 Prompt 模板（主 Agent 每次复制填充 `{占位符}`）

### 6.1 模板正文

```text
你是「源重构」项目的模块工程师子 Agent，负责实现模块 [{模块编号}] {模块名称}。
项目根目录：{仓库根}。Windows + PowerShell 环境。全程无人工参与，不要提问，
遇到不确定事项按降级规则处理后继续，并把决定写进你的汇报。

【第一步：阅读】
1. tasks/{任务文件}.md            ← 你的任务清单与完成标准（唯一验收依据）
2. design/max_design.md 第 {相关章节} 章 ← 类签名/算法公式/参数/「可独立测试」小节
3. design/general_design.md 相关模块小节  ← 模块边界与依赖规则（可选回顾）
4. 你所依赖模块的已有代码入口：{依赖模块的公共 API 清单}

【第二步：实现】
- 逐条落实任务清单 checkbox；签名、算法、参数、错误码严格照抄详设，不自创 API；
- 遵守全局硬约束（分层/T 线程约定/非破坏性/中文注释/logger 命名/tr() 包装）；
- 输出位置：src/ych/{模块目录}/；测试位置：tests/{unit|integration}/{模块目录}/；
- 公共数据结构与错误码从 ych.common 引入，禁止重复定义；
- 若发现任务清单与详设矛盾：以任务文件「⚠ 偏差修正」为准；若无标注，按详设；
  两种情况都要在汇报 deviations 中记录。

【第三步：测试】
- 任务文件「测试」小节逐条落地为 pytest 用例，一条不落；
- unit/component 为硬门槛；integration 用例开头探测 ffmpeg，缺失即 pytest.skip；
- 禁止 mock 被测模块自身逻辑；替身（fake_ffmpeg/FakePlugin/DummyProvider/
  responses/SyncFakeHandler）按任务文件指定使用；
- 新增 fixture 优先放入 tests/fixtures 或共享 conftest，避免复制粘贴。

【第四步：质量门（必须真实运行，全绿才算完）】
.venv\Scripts\python -m pytest -m "not integration" -q
.venv\Scripts\python -m pytest -m integration -q        # 无 ffmpeg 时全部 skip 属正常
.venv\Scripts\python -m mypy src
.venv\Scripts\python -m ruff check src tests
失败则修复并重跑，直到四条全绿。同一问题迭代 3 轮未解决：对非关键路径用例可
@pytest.mark.xfail(reason="...") 并在汇报说明；关键路径禁止 xfail，改为记录阻塞。

【第五步：收尾】
- 勾选 tasks/{任务文件}.md 中已真实完成的 checkbox（未完成的不许勾）；
- 按下述格式汇报。

【汇报格式】
status: done | partial
files_added: [...]
files_modified: [...]（含公共件变更点及理由，若有）
tests_added: {数量与覆盖的任务清单测试条目}
gate_results: pytest_unit=X passed X skipped / pytest_integration=X passed X skipped / mypy=clean / ruff=clean
deviations: [对设计或任务清单的每一处偏离及理由]
blockers: [无法自行解决的遗留问题，无则空]
checklist_done: 任务文件中已勾选条目数/总数
```

### 6.2 主 Agent 验收清单（每个子 Agent 返回后执行）

1. 重跑四条质量门命令，确认输出与汇报一致；
2. 对照任务文件「完成标准」小节逐条核实；
3. 抽查 2~3 个测试用例确有断言且确实在跑（防止空壳测试）；
4. 检查是否越权修改了其他模块/公共件；
5. 全部通过 → 记账 + commit；否则 → 带着具体失败信息打回返工。

### 6.3 特殊阶段的模板增补

- **阶段 0（Spike 替身）**：按 D1 的四项假设改写任务描述，产出物是 `tests/spikes/` 下的可运行冒烟测试与 `doc/decisions.md` 中的放行记录；
- **阶段 1a（01-common）**：额外要求——一次性把第 7 章的 ruff/mypy/pytest 完整配置写进 `pyproject.toml`，并保证 `pip install -e ".[dev]"` 一次成功；fake_ffmpeg/fake_ffprobe 用纯 Python 脚本实现（带 shebang 或 .bat 包装），保证 Windows 可执行；
- **阶段 5（12-ui）**：额外要求——所有 UI 测试经 FakeScheduler/内存 DAO 注入，绝不触网、绝不调用真实 ffmpeg；pytest-qt 的 qtbot 使用规范遵从详设十五章；
- **阶段 6（13-package）**：按 D4 范围裁剪，能本地验证的只有 spec/iss 语法检查与 workflow YAML 校验，其余列入遗留人工事项。

---

## 7. 质量门槛与标准命令

### 7.1 工具配置基线（由 01-common 一次性写入 `pyproject.toml`）

```toml
[tool.ruff]
line-length = 100
target-version = "py310"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "UP", "B", "SIM", "RUF"]

[tool.mypy]
strict = true
python_version = "3.10"
files = ["src"]

[[tool.mypy.overrides]]          # 仅豁免无 stub 的第三方库，业务代码全量严格
module = ["cv2.*", "onnxruntime.*", "keyring.*", "keyring.errors",
          "pytestqt.*", "responses.*"]
ignore_missing_imports = true

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
markers = [
  "integration: 需要真实 ffmpeg 的端到端用例（缺失自动跳过）",
]
```

> 说明：mypy 采用 `--strict` 全量（用户指定）；上述 override 只解决第三方库缺 stub 的导入报错，不允许用于豁免业务代码的类型错误。若开发中发现新的无 stub 依赖，按同样方式追加 override 并记入 `doc/decisions.md`。

### 7.2 标准命令（仓库根，PowerShell）

```powershell
.venv\Scripts\python -m pytest -m "not integration" -q   # 硬门槛：必须全绿
.venv\Scripts\python -m pytest -m integration -q         # 无 ffmpeg 时全 skip 属正常
.venv\Scripts\python -m mypy src                          # 硬门槛：零错误
.venv\Scripts\python -m ruff check src tests              # 硬门槛：零告警
```

覆盖率参考门禁（收尾回归时用 `--cov` 实测并在报告中给出，不达标模块优先补测）：
services ≥80%、core ≥75%、关键算法（similarity/postprocess/scene/motion/rhythm）≥90%。

### 7.3 模块完成的定义（DoD）

1. 任务文件 checkbox 全部可勾选且有对应真实产出；
2. 7.2 四条命令全绿；
3. 任务文件「完成标准」小节逐条满足；
4. `tasks/progress.md` 状态更新 + git commit 完成。

---

## 8. 自主决策与降级规则（无人参与时的兜底）

| 情形 | 处理 |
|------|------|
| 文档间矛盾 | 按 1 章裁决链裁决，记入 `doc/decisions.md` |
| 外部资源缺失（ffmpeg / 模型权重 / API Key / 外网） | 按 D1～D5 既定降级路径执行；不在表内的：最小可用实现 + 代码内 `# TODO(需人工): ...` 标注 + 记录 |
| 测试同题 3 轮修不过 | 非关键路径 xfail(reason) 保留断言；关键路径记 blockers 继续（不得删除断言掩盖） |
| 子 Agent 两次返工仍不达标 | 主 Agent 接管该模块亲自修复 |
| 依赖包安装失败 | 锁定更保守版本重试一次；仍失败则换等效库并记录（不得静默去掉依赖） |
| 任何情况下 | 不得违反第 2 章硬约束；不得为了过检而弱化断言或放宽工具配置 |

---

## 9. 进度维护规范

- `tasks/progress.md`：模块状态列取值 `未开始 / 进行中 / 已完成 / 已降级`；开工即置「进行中」，验收通过置「已完成」；Spike 行置「已降级（替身冒烟）」；
- 各 `tasks/*.md`：checkbox 只在真实完成后勾选；
- `doc/decisions.md`：追加式决策日志，字段：`日期 | 编号 | 背景 | 决定 | 影响`；
- `doc/final_report.md`（收尾）：模块完成清单、四条质量门最终结果、测试数量与覆盖率、遗留人工事项汇总（真实 Key 冒烟、真实模型下载、打包发版、Win10 虚拟机验证等）。

---

## 10. 立即开始

按顺序执行：

1. 读第 1 章五个输入（通读需求与概要，详设至少掌握第二/三/四章全局约定与目录树）；
2. `git init` 并完成首次提交（baseline）；
3. 创建 `.venv`；
4. 按第 6 章模板派发 **阶段 0** 子 Agent（Spike 替身冒烟），验收通过后依表派发后续阶段；
5. 每个模块严格执行 6.2 验收清单与 7.3 DoD；
6. 全部完成后执行收尾回归，生成 `doc/final_report.md`，并向会话输出最终总结（完成了什么、测试与检查结果、遗留人工事项清单）。

现在开始，不要再等待任何确认。
