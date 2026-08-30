# 「源重构」开发最终报告

> 版本：v0.1.0 ｜ 日期：2026-08-25
> 依据：`flag/flagone.md`（需求 v2.0）+ `design/max_design.md`（详设 v1.0）
> 范围：阶段 0~6 全量交付；阶段 6 按 SP-8 D4 剪裁范围执行

---

## 一、交付总结

| 阶段 | 模块 | 状态 | 说明 |
|------|------|------|------|
| 0 | Spike 技术验证 SP-1~SP-8 | ✅ 已降级放行 | DEC-004：替身冒烟替代实机验证，P0 放行 |
| 1 | 公共件 + S5/S4/S3 服务层 | ✅ 完成 | schemas/errors/cancellation/fsutil、Config(keyring)/I18n/Log、HttpClient(断点续传/probe)/限速器、SQLite WAL 九表+DAO |
| 2 | S1 媒体引擎 + S2 AI 推理 | ✅ 完成 | FFmpegRunner(run_pipe 双进程流式)、Probe、抽帧；Provider 协议/LocalProvider/postprocess 纯函数/LaMa 修复 |
| 3 | M5 素材库 + M4 调度中心 | ✅ 完成 | 工作目录/三级归档/扫描索引/类目管理；七态状态机/指数退避重试/失败记录/崩溃恢复 |
| 4 | M1 采集 / M2 预处理 / M3 去重 | ✅ 完成 | 见下文模块明细 |
| 5 | U 表示层（U0~U6） | ✅ 完成 | 主窗口导航+六页面+公共组件+i18n(.ts/.qm)+pytest-qt 冒烟 |
| 6 | 打包分发 | ✅ 完成（D4 剪裁） | PyInstaller spec+语法校验、Inno Setup 脚本、CI/Release 工作流、开源材料 |

## 二、阶段 4~6 本轮交付明细

### M1 素材采集（tasks/09）

- 插件框架：`PlatformPlugin` ABC（api_get 统一错误映射 401/403→PLG002、429→PLG003）、
  `SkeletonPlugin` 六平台占位（PLG010/not_implemented）、`PluginManager` 自动发现 +
  enabled_plugins 覆盖 + foreign_platforms_enabled 总开关 + 可用性 TTL 缓存。
- Pexels/Pixabay 完整实现：可用性检查、搜索字段映射、清晰度选型
  （高度≥min_height 中体积最小优先，tiny gif 过滤）、限频 18s/2s。
- 搜索协调：`SearchCoordinator` 多关键词异步执行、单平台异常隔离进 unavailable_platforms、
  `ResultFilter` 纯函数筛选排序、历史搜索词服务。
- 下载队列：limit 截断 + DL010 提示任务、BoundedSemaphore 并发闸门、断点续传
  （resume.temp_path 为数据权威路径）、进度 ≥1s 节流回写、M5 归档原子落盘。
- 外网检测：三站探测三态映射（OK/BLOCKED/OFFLINE）、TTL 缓存、app_settings 跨重启持久化。

### M2 视频预处理（tasks/10）

- `PreprocessOps` 五处理项 + `decide_path` 纯函数路径分派
  （帧级 > 纯滤镜 > remux > skip；偏差修正：仅去原声以流复制 remux 承接）。
- `build_vf_chain` 纯函数滤镜链（crop 归一化→像素 clamp、偶对齐、比例 crop/pad 分支）。
- `SubtitleHandler.route`：auto 有软轨→soft（流复制 -sn 剥离）；无软轨抽样 ≤6 帧 OCR
  检出底部文字带→hard；manual 视为硬字幕；均无→none 跳过不报错。
- 帧级管线：阶段A 抽样检测（fps≈2、cap=24）→ 时域聚类稳定 → RegionSpan；
  阶段B run_pipe 流式修复（静态区缓存每 30 帧刷新）；crop/scale/pad 挂编码端一次转码；
  阶段C >2×时长 WARN 守卫。
- 批量 handler 部分失败隔离（AppError→失败列表→continue），SkippedSignal 计入 skipped。

### M3 智能去重（tasks/11）

- 特征提取 v1：HSV 直方图差切分（μ+2.5σ 自适应阈值+±3 帧邻域极大值+0.8s 最短镜头）、
  Farneback 光流→相似变换→pan/zoom 曲线（L=32, clip[-1,1]）、log2 十档节奏直方图
  （补零至16维）+ 滑窗切换率曲线（32 点归一）、CLIP 构图嵌入（>32 镜头均匀下采样）。
- 相似度公式逐条落地并表驱动手算验证：双向 Chamfer 最大匹配余弦（空镜头记 0）、
  S_motion=1−½·mean|Δ|、S_rhythm=0.5·直方图交+0.5·S_rate、加权 clip 综合、
  version 不一致抛 AI003、值域 [0,1] 随机 200 组自检。
- 五手法引擎：mirror/crop_scale/color_filter/speed/border，zorder 10~50 注册表排序，
  validate_params 统一缺省补全+clamp+枚举白名单；lut3d 缺失时 colorbalance 退化。
- 策略管理：light/mid/heavy 三档 PRESETS 区间均匀随机实例化（seed 可复现）、
  recommend 边界（<0.50 / 0.50~0.80 含端点 / >0.80）、SchemeDao 自定义方案存取、
  preset_to_custom 支撑"预设→自定义微调"。
- 流水线：已去重/ 镜像层级输出 `_deduped.mp4`、手法链单条 ffmpeg（setpts/atempo 注入）、
  前后重复度对比（复用 ReportDao.latest_for 同批 targets 重算）、>3×时长 WARN。
- CandidateSearcher/CandidateCache：手动参考+自动候选（抖音/快手/B站必选，
  TikTok/YouTube 可选，不含小红书——需求 3.1+详设 18.2-6）、宽筛≤90s K=20、
  低清缓存下载（TTL 7 天惰性清理）、单候选特征预算 60s 超时 skip、
  全平台不可用标注 unavailable 提示手动兜底。

### U 表示层（tasks/12）

- 应用骨架：`context.py` AppContext 全懒加载服务定位器（支撑冷启动 ≤5s）、
  `app.py` main() 组装（主题/翻译/handler 注册/工作目录引导）。
- U0 主窗口导航+页面栈、首次启动工作目录强制向导（validate 失败循环重选）、使用说明对话框
  （三大工作台三步指引）。
- U1 采集：多关键词逗号分隔+历史词下拉、国内默认开/国外总开关接 ForeignNetChecker
  （BLOCKED/OFFLINE 回弹弹窗）、筛选四件套、结果卡片勾选批量下载（上限 SpinBox 默认 20）、
  信号驱动下载队列视图+DL010 提示行。
- U2 预处理：素材树三级勾选汇总、五处理项面板、橡皮筋多区域框选画布（归一化 BBox）、
  build_payload → M4.submit。
- U3 去重：素材勾选列表、三档方案卡（recommend 徽标渲染）、param_schema 动态编辑器、
  预设套用/转自定义微调、报告视图（总分+三维度条+targets 表+before→after 展示）。
- U4 失败列表：QTableView 四列模型、重新处理（rebuild payload 重提交）/清除/刷新。
- U5 设置：通用/网络/密钥/高级分组、控件↔ConfigService.changed 双向绑定、
  Key password 输入入 keyring、「检测外网」按钮。
- i18n：lupdate 提取 30 条 tr() 源串 → zh_CN/en_US.ts → lrelease 编译 .qm；
  I18nService 切换发射 locale_changed（冒烟测试覆盖）。

### 打包分发（tasks/13，D4 剪裁范围）

- `scripts/build_exe.spec`：src 入口 + runtime ffmpeg/ffprobe + models/*.onnx（缺失告警跳过）
  + i18n/*.qm + templates/* datas；hiddenimports（QtMultimedia/keyring Windows 后端）；
  excludes 精简体积。**AST 语法校验通过**。
- `scripts/installer.iss`：Inno Setup 脚本（开始菜单/桌面快捷方式、卸载保留用户数据、
  Win10+ MinVersion、签名位预留）。
- CI：`.github/workflows/ci.yml`（windows-latest：ruff+mypy 门禁、unit 必跑、
  integration 夜间/发版触发）+ `release.yml`（tag 触发构建→产物附 GitHub Release）。
- 开源材料：LICENSE(MIT)、README（安装/教程/FAQ/平台可用性降级口径/合规声明）、
  CONTRIBUTING（两大插件扩展点接入指南）。

## 三、质量与验收对照

### 测试统计（最终三轮回归）

```
289 tests × 3 连续运行：failures=0 errors=0 skipped=0 exit=0
分层：unit（纯算法/DAO/调度/UI 冒烟）+ integration（真实 ffmpeg，自动 skip 兜底）
工具链：ruff All checks passed ／ mypy strict Success (111 files)
```

覆盖率重点（关键算法单测密度）：similarity 公式逐条手算用例+值域随机自检、
postprocess/scene/motion/rhythm 合成序列断言——满足"关键算法 ≥90%"门禁设计
（CI 中以 --cov-fail-under=90 单独卡口）。

### 需求验收锚点对照

| 锚点 | 结论 | 证据 |
|------|------|------|
| 性能：预处理 ≤2×时长 | ✅ 设计保证 | 路径分派纯函数（秒级 remux/纯滤镜路径优先）；帧级管线阶段C 超预算 WARN 守卫（frame_level_processor.py）|
| 性能：去重 ≤3×时长 | ✅ 设计保证 | dedup_pipeline execute_item 耗时守卫 |
| 性能：下载 ≥3 并行 | ✅ 已测 | DownloadManager BoundedSemaphore(config=3)，并发闸门计时断言 max_active==并发数 |
| 启动 ≤5s / 界面响应 ≤1s | ✅ 设计保证 | AppContext 全懒加载；SP-7 offscreen 冒烟通过；HDD 放宽 8s 已在 README 标注 |
| 稳定性：原文件不破坏 | ✅ 已测 | 只读保护+原子写出；m2/m3 集成断言 src.exists 且内容不变 |
| 稳定性：崩溃不丢文件 | ✅ 已测 | CrashRecovery scan+resume_row_ids 续传重建（m4 用例）；.part 保留语义 |
| 稳定性：失败一键重新处理 | ✅ 已测 | FailRecordManager.rebuild_payload + U4 页面 reprocess |
| 兼容：Win10+/MP4 输出/五种导入格式 | ✅ 实现 | EncoderSpec 全局唯一输出规范；SUPPORTED_EXTENSIONS 白名单 |
| 易用：中英文切换默认中文 | ✅ 已测 | i18n .qm 编译+locale_changed 断言；默认 zh_CN |
| 易用：主要功能 ≤3 步 | ✅ 实现 | 三大工作台步骤约束自检（README 教程表）|
| 错误提示含解决建议 | ✅ 实现 | Toast「查看日志」按钮；外网不可达弹窗含代理检查指引 |
| 可扩展：插件式架构 | ✅ 已测 | PluginManager 文件扫描发现零注册；六骨架 PLG010 灰显不阻塞 |
| 可扩展：去重手法模块化 | ✅ 已测 | TechniqueRegistry zorder 排序+注册重复校验 |

### 已知限制与遗留事项

1. **六平台采集未开放**（首版承诺口径）：抖音/快手/B站/小红书/TikTok/YouTube 为统一骨架占位，
   UI 灰显"暂不可用"，符合需求风险对策与详设 12.1.5 降级设计。
2. **AI 模型按需分发**：基础包不带 onnx 权重（体积控制），缺失时 AI 功能提示不可用，
   其余功能不受影响；下载流程接口已预留（URL/sha256/续传复用 S4）。
3. **D4 剪裁项进展**：PyInstaller 本地实机构建已通过（dist/YuChongGou 313 文件，
   .qm/templates 正确入包，ffmpeg 缺失按设计告警降级），离屏启动冒烟 12s 存活；
   **Inno Setup 已实装编译通过**（YuChongGou_Setup_0.1.0.exe，80.5MB），并完成
   本机安装生命周期冒烟：静默安装→安装版离屏启动 12s 存活→静默卸载→
   应用目录清除、用户数据保留语义验证通过；
   剩余人工项：干净 Win10 虚拟机安装冒烟、真实 Key 手工闭环、安装包签名证书
   （CI Release 工作流已就绪）。
4. **i18n 覆盖面**：当前 .ts 目录收录 30 条高频界面串；长尾文案回退显示中文源语言
   （QTranslator 缺失条目行为），后续版本随 UI 迭代补齐 en_US 词条。
5. **本轮修复的既有缺陷**（回归中暴露并修复，均已收口）：
   - S1 ProbeService 中文路径 MED002：subprocess 未显式 utf-8 解码 ffprobe 输出
     （GBK 区域产生非法 JSON 转义），已修复并加注释；
     **回归测试已补**（fake_ffprobe 对齐真实口径恒定 UTF-8 输出并回显输入路径；
     突变验证：移除 encoding 后用例稳定复现崩溃）；
   - S4 HttpClient 传输层重试包含 429 与业务层 PLG003 退避冲突，已从 status_forcelist 移除；
     **回归测试已补**（策略断言 + 行为断言"429 仅 1 次请求"；突变验证：
     加回 429 后恰好打出 1+max_retry=3 次、双用例变红）；
   - m4 崩溃恢复测试断言竞态（worker 异步翻转状态），轮询等待加固；
   - spikes 过早创建 QCoreApplication 污染 Widgets 测试进程，统一 QApplication 单例；
   - **⑤ M4 调度器终态可见性竞态**（第三轮回归暴露）：`_finish` 先翻转内存
     task.state 后落库，外部观察者可能在 SQLite 写入完成前读到终态内存态，
     造成"任务 success 但行仍 running"假象。已改为**持久化副作用先提交、
     内存终态后翻转**；覆盖率追踪放大时序窗口后又暴露同根残留——
     `_fails.record` 亦在状态翻转后才写，已一并前移（失败记录先于终态可见），
     m1/m4 两处测试助手同步轮询加固；
   - **⑥ 安装包中文语言文件缺失**（实机编译暴露）：Inno Setup 6.3 起官方安装包
     不再捆绑非官方翻译，installer.iss 引用的 ChineseSimplified.isl 在新装机/
     CI 上必致编译失败。已随仓库分发该语言包（scripts/languages/，含来源与
     许可说明），iss 改相对引用；release.yml 同步改为 choco 显式安装 Inno Setup
     并对 ISCC 退出码硬失败（不再静默吞错）；
   - **⑦ CI 关键算法覆盖率门禁从未可绿**：pytest-cov 对点号单模块 --cov 目标
     （如 ych.core.m3_dedup.similarity）的解析会提前导入目标模块，在 numpy+cv2
     组合下双重初始化直接崩溃（包级/目录/文件路径目标均正常）。CI 门禁改用
     包级目标采集 + scripts/check_cov_gate.py 按文件逐个卡 ≥90%
     （实测 similarity 92.7 / scene_detector 92.5 / motion_analyzer 94.6 /
     rhythm_analyzer 97.7 / postprocess 100）。

## 四、风险跟踪收口（需求第六章）

| 风险 | 对策落地 |
|------|----------|
| 平台反爬策略升级 | 插件式架构：新增/更新 *_plugin.py 即可适配（CONTRIBUTING 指南） |
| 各平台接口变动 | parse_json 统一抛 PLG020 结构变更预警，隔离单平台不影响整体 |
| AI 去水印效果不完美 | 手动框选兜底 + 处理预览（U2 画布/播放器） |
| 联网比对依赖外部服务 | 手动参考视频路径完整实现（CompareTarget source=manual） |
| 外网访问受限 | ForeignNetChecker 启动检测 + 设置页手动检测按钮 + 开关回弹 |

## 五、结论

项目按详设分阶段推进完毕：**289 项自动化测试三轮全绿、静态检查零告警**，
功能模块（采集/预处理/去重）、表示层、打包分发材料全部交付；
七项既有缺陷全部修复并收口（①~④带回归测试，⑤⑥⑦为本轮验证新暴露），
PyInstaller 构建、Inno Setup 安装包编译与安装/卸载生命周期冒烟均在本机通过，
CI 覆盖率门禁已实测可绿。
剩余为 D4 明确剪裁的人工验证项（干净虚拟机实机安装、真实 Key 冒烟、签名证书），
可随首次 Release 发布流程一并执行。
