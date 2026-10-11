# 交接文档：去重升级 + 预处理无痕改造（2026-10-11 会话）

> 写给下一个会话：本文档记录本会话全部工作、当前代码状态、**未解决的问题**与调试线索。
> 所有改动均在工作区**未提交 git**（60 个文件，含此前会话遗留的未提交改动）。
>
> **【2026-10-12 续会话已收尾】**第三节列的 P0-1/P0-2/P1-1 全部解决：
> - P0-1 根因**不是**修复分派逻辑（打印确认手动 span 一直正确走 kind="box"），
>   而是 `local_provider.py::inpaint` 泊松融合的 `seamlessClone` center 传错：
>   OpenCV 把**掩码外接框中心**对齐到 center，旧代码传的是上下文贴片中心——
>   掩码贴画面边时贴片被钳位、掩码不居中，填充内容被放去别处，目标区域原封不动
>   （e2e 黄块贴顶边 → 必现；真实视频水印不贴边 → 碰巧没错位，掩盖了 bug）。
>   已改为传"膨胀掩码外接框中心在全帧坐标的位置"。
> - P0-2 重跑 real_check 亲眼看图后又发现并修复一层：时域掩码边界切在半透明
>   水印光晕上，LaMa 把亮环向外延续填成**白色块状伪影**。把
>   `temporal_stroke_mask` 末端膨胀 21→65（半径~32px，越过大水印光晕；实测
>   椭圆45仍留淡白条、逐字符外接框出白色横条、整框/椭圆65无痕）。
>   最终 `build/real_check/2_after.png` 水印+字幕均无痕，5/15/25/30/38s 多点
>   复核通过（`build/real_check/multi_*_after.png`）。
> - P1-1 RUF046 实际位置在 `frame_level_processor.py:770`（int(round()) 冗余），
>   已清；`local_provider.py` 那处已不存在。
> - 回归状态：全量 pytest 绿（1 个单测随掩码语义更新：静态淡笔画用例贴片
>   48x64→120x160，原"远处背景"像素在新膨胀半径内必被盖住）、e2e 27/27、
>   改动文件 ruff 干净。待办仅剩：与用户确认 git 提交范围后提交。

---

## 一、用户需求演进（本会话处理了 6 个需求）

1. 去重太慢（逐帧对比）+ 点"开始去重"没反应 → 性能升级 + UI 反馈
2. 用一组视频验证去重有效性 → `scripts/dedup_verify.py`（21/21 通过）
3. 去重再提速，不许降画质/不许有补丁/不许不流畅 → 批次流水线化
4. 点"分析重复度"卡死在 45% → `candidate_searcher` 重写
5. 预处理去水印/字幕是"覆盖"不是"消失" → 掩码与修复路径重构
6. **要求无痕** → 时域掩码方案（本轮最后的工作，字幕已无痕，水印接近但未完全收尾）

---

## 二、已完成并验证的改动

### 去重（M3）——已验证 ✓
- `motion_analyzer.py`：光流分析降采样 320px + 帧对抽样 ≤96 对（15s→1s，15×）
- `probe_service.py`：按 (路径,mtime,size) 的探测缓存
- `dedup_pipeline.py`：拆分 `transcode_item`/`compare_item` 两阶段；对比目标 4 线程并行；进度覆盖转码[0,0.7]+对比(0.7,1.0]
- `context.py::_handle_dedup`：批次流水线（生产者转码 ∥ 消费者对比），进度=各条贡献求和
- `candidate_searcher.py`（分析重复度）：搜索/评分分离、本地池与候选并行（4/3 线程）、单候选"下载+特征"合并 90s 预算、进度全程推进（修掉"卡 45%"）
- `u3_dedup/dedup_page.py` + `app.py`：busy 态 + 页面进度条（`attach_scheduler`/`track_task`）
- 验证：全量测试绿、`scripts/dedup_verify.py` 21/21（含 pHash 拉开 2.2~2.8×）

### 预处理（M2）无痕改造——字幕已无痕 ✓，水印接近 ✓（有遗留，见下）
- `frame_level_processor.py::repair_frame` 完全重写：
  - 紧凑笔画掩码（`_tight_stroke_mask`：逐通道色差 vs 局部中值，替代旧灰度版——黄字压沙地不再漏）
  - 自适应阈值：`thr = max(12, min(otsu, 35, p85*2.2 if p90≤60 else 35))`
  - 只贴掩码像素（`_feather3`/距离 alpha），不再整框覆盖
  - 自适应刷新（背景运动 `moved>10` 提前刷新）+ 空掩码帧跳过
  - `_solid_overlay`：实心色块（直方图主色 ≥50% + 框环亮度差 ≥25）回退整框
  - 手动框选（`RegionSpan.manual=True`）：紧凑失效时回退整框
- `local_provider.py::inpaint` LaMa 路径重写：
  - **正方形上下文裁剪**（side = 掩码长边×1.4，等比缩放 512²）替代旧"拉伸+反射填充"（拉伸变形 + 镜像副本让 LaMa 把洞"续"成文字——残影主因）
  - **泊松融合**（`seamlessClone NORMAL_CLONE`）消除填充区与渐变背景的色块边界；掩码贴边时回退距离 alpha 混合
  - 距离 alpha（≥2.5px 内 100% 填充）替代固定 σ 高斯（旧版细笔画中心权重 0.7 → 幽灵字）
- **时域掩码方案**（本轮核心新增）：
  - `temporal_stroke_mask(patches)`：跨采样帧逐像素中值响应 + 方向一致性（≥70% 同号），静态淡笔画（差值 10~30）整段收进掩码
  - `detect_spans` → `_attach_temporal_masks`：水印出现率门槛降到 0.45（YOLO 对半透明水印间歇漏检，实测出现率 ~0.53 被 0.6 过滤掉）；采样失败重试一次
  - `repair_frame` 使用前**逐帧验证**：时间掩码与当前帧紧凑掩码重叠率 ≥50% 才采用（轮播字幕的时间掩码盖的是别的行，自动回退逐帧路径）
  - `postprocess.py`：`RegionSpan` 加 `manual`/`temporal_mask` 字段；`temporal_cluster` 加中心规则链接（细长条检测框逐帧抖动导致 IoU<0.5 簇碎裂→全被出现率过滤——**这是水印区域此前从未进入修复的根因**）+ 簇框随成员并集增长

### 其他
- `region_temporal.py`：`build_spans` 加 `presence_ratio` 参数（水印用 0.45）；`manual_spans` 置 manual=True
- 自动框外扩 12%（聚类框盖不住行列首尾的字）
- `i18n/` 四件套已按"显式列文件 lupdate → fill → lrelease"流程重新生成（zh 448/en 447 finished）
- 新脚本：`scripts/dedup_verify.py`（去重验收）、`scripts/hw_encoder_gate.py`（硬件编码器质量门，本机实测 NVENC 会话被禁、QSV 提速 0.96~1.04× 不达标 → 维持 libx264 crf18 faster）

---

## 三、未解决的问题（按优先级）

### P0-1：e2e 回归未修复（2 项 FAIL）
`scripts/e2e_real_media.py` 当前 25/27，两项 FAIL：
- 用例 A："水印区像素确实被改写"
- 用例 D："修复段水印被清除（黄块占比 0.82）"

场景：testsrc2 源视频上**手动框选**纯色黄块（WM_BOX=(960,40,256,72)，manual BBox(0.74,0.045,0.22,0.11)）。黄块是**纯色实心**，对色差掩码不可见（局部中值=黄色本身），应走 `_solid_overlay` 实心判定 → 整框修复。上一轮该 e2e 是 27/27，本轮改动后失效。**怀疑点**：`_tight_stroke_mask` 改逐通道色差后，纯色块 cover 仍≈0 → 应进 `_solid_overlay` 分支；调试时先打印 repair_frame 里手动 span 实际走的 kind（box/tight/none）。注意 e2e 源视频在 `build/e2e_real_media/`。

### P0-2：真实帧水印最终状态未确认
最后一轮撤销"梯度校正"后**没有重新生成并查看** `build/real_check/2_after.png`（撤销前那版 grad 校正把带子涂花了，撤销后理论上回到泊松版=文字不可辨+极淡云带）。下一步：重跑 `build/real_check.py` 并亲眼看图。验证脚本用真实视频 `D:/tmp_ych_workdir/.../douyin_..._001.mp4` 30s 帧（"机械帕克"半透明水印 + 黄色字幕）。

### P1-1：ruff 还剩 1 个错误
`local_provider.py:338` RUF046（`sx0 = int(min(max(round(...)...)))` round 已返回 int，去掉 `int(...)`）。我的 sed 修复失败（文本不匹配），需手工改。

### P1-2：全帧修复速度仍慢
LaMa CPU 单次 ~1.6s（实测），2 分钟视频全帧修复 30+ 分钟。GPU 不可用已实测（NVENC 会话被禁、LaMa DML 算子不兼容）。时域掩码+空帧跳过有节省但量级未变。潜在方向：逐区域裁剪修复代替整帧、帧间复用（当前 30 帧缓存贴片已有）。

### P2：杂项
- 此前会话遗留的 ruff（douyin_plugin 等 9 处）与 mypy（6 处）未清，与本次无关
- `build/` 下有调试脚本（real_check.py、seamless_check.py、lama_probe.py 等，build/ 已 gitignore，可留可删）
- 时域掩码采样 = 每视频 6~12 次 ffmpeg seek（一次性几秒），可接受

---

## 四、调试线索（新会话直接用）

- 真实帧验证脚本：`build/real_check.py`（完整 auto 链路：detect_spans → repair_frame）
- 无痕前后对比图：`build/real_check/1_before.png` / `2_after.png`；`build/seamless_check/`（合成素材版）
- 关键参数都在 `frame_level_processor.py` 顶部常量区：`_STROKE_MIN_COVER=0.004`、`_STROKE_DENSE_COVER=0.65`、`_BOX_SHIFT_DENSE=25`、`_REFRESH_DIFF=10`、`CACHE_REFRESH_FRAMES=30`
- 时间掩码的修复时验证（重叠率 ≥50%）在 `repair_frame` 的 `use_temporal` 计算处
- 用户真实视频目录：`D:/tmp_ych_workdir/`（从 `%LOCALAPPDATA%/YuChongGou/app.db` 的 app_settings.workdir 读到）
- 运行测试：`.venv/Scripts/python.exe -m pytest tests -q`（当前全绿）
- i18n 流程（改 tr() 必走）：pyside6-lupdate **显式列文件**（勿 -recursive，会打碎 en_US.ts）→ `scripts/fill_i18n_translations.py` → pyside6-lrelease

---

## 五、建议的下一步顺序

1. 修 e2e 两项 FAIL（P0-1）——先打印 repair_frame 手动 span 实际走的 kind
2. 重跑 `build/real_check.py` 亲眼看水印最终状态（P0-2），若泊松版可接受则交用户验收
3. 清 ruff RUF046
4. 全量测试 + e2e 27/27 后，建议 git 提交（改动量大，且含此前会话遗留改动，提交前先和用户确认提交范围）
5. 速度优化（P1-2）另开议题
