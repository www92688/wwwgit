# M3 - 智能去重模块

> 模块目标：重复度检测（自动搜索对比+手动参考）、五手法去重引擎、三档预设+自定义策略、前后对比输出。
> 设计依据：`design/max_design.md` 第十四章；需求模块三
> 前置依赖：05-s1-media、06-s2-ai、07-m5-library、08-m4-scheduler、09-m1-capture(候选搜索复用插件)；SP-4 结论回填
> 输出位置：`src/ych/core/m3_dedup/`

## 任务清单

### 特征提取（14.1）
- [x] `feature_extractor.py` extract 总流程：probe → stream_pairs(fps=2, cap=300) → 切分/运镜/节奏/构图 → FeatureSet(version="1")
- [x] `scene_detector.py`：HSV 三通道各 32bin 直方图差 d_t；自适应阈值 T=μ+2.5σ；邻域±3帧极大值；距上一边界≥0.8s 最短镜头
- [x] `motion_analyzer.py`：Farneback 光流(0.5,3,15,3,5,1.2,0) → 网格 16px 采样 → estimateAffinePartial2D RANSAC → pan_x/pan_y/zoom=s−1 → 插值 L=32 → clip[-1,1]
- [x] `rhythm_analyzer.py`：镜头时长 log2 十档直方图归一化补零至 16 维；滑窗 w=5s 步 w/6 cut_rate 曲线插值 32 点除以全局最大归一
- [x] 构图嵌入（CompositionEmbedder）：每镜头中点最近帧 embed_frames → (m,512) L2 归一化；m>32 均匀下采样至 32

### 相似度计算（14.2 — 公式逐条落地，重点单测）
- [x] S_comp 双向 Chamfer 最大匹配余弦（空镜头记 0 标 skipped_reason）
- [x] S_motion = 1 − ½·mean|Δmotion_curve|（÷2 归一）
- [x] S_rhythm = 0.5·直方图交 + 0.5·(1−mean|Δcut_rate|)
- [x] Score 加权 clip(w_c·S_c + w_m·S_m + w_r·S_r)；version 不一致抛 AI003
- [x] 档位推荐边界：<0.50 轻度 / 0.50~0.80 中度 / >0.80 重度
- [x] ReportBuilder.build：overall=max(targets)、超80%计数、targets 降序、ReportDao.add

### 自动搜索对比（14.3）
- [x] CandidateSearcher.run：feat_A 提取 → manual refs 逐一提特征 → auto：从归档路径解析二级关键词（非归档素材由 UI 要求用户填）→ 对比平台集=抖音/快手/B站（必选默认开）+ TikTok/YouTube（可选），**不含小红书**（需求 3.1 + 详设 18.2-6）；check_available(False 记 unavailable 不阻塞) → 宽筛(≤90s) K=20 → 低清变体(高度≤480 最近者) → 缓存下载 → 单条特征预算 60s 超时 skip → 全平台不可用提示手动兜底
- [x] CandidateCache.fetch_or_download：<用户数据目录>/compare_cache/<plugin>/<key>.mp4，TTL 7 天惰性清理

### 手法引擎（14.4）
- [x] `techniques/base.py`：ParamField/ParamSchema、ClipContext(src/probe/vf_filters/speed_factor/keep_audio)、DedupTechnique(id/display_name_zh/zorder 10~50/validate_params clamp+补全/apply)
- [x] TechniqueRegistry.register(id 去重)/ordered(按 zorder)
- [x] mirror(z10)：hflip/vflip/hflip,vflip
- [x] crop_scale(z20)：crop 后显式 scale 回原 W:H（非 iw:ih）；scale 放大后居中裁回；偶数对齐
- [x] color_filter(z30)：eq(brightness/contrast/saturation) + colorbalance temperature + preset lut3d 内置 .cube（preset 与数值互斥）
- [x] speed(z40)：setpts=PTS/f + atempo=f（0.75~1.25 单段）；影响 dur 与进度换算
- [x] border(z50)：solid pad 放大画幅 / blur split+gblur sigma20+overlay

### 策略管理（14.5）
- [x] SchemeManager.recommend(score) 区间映射
- [x] instantiate(preset_id, seed)：light/mid/heavy PRESETS 区间均匀随机取参，seed 可复现
- [x] save_custom/load_custom（SchemeDao）/ preset_to_custom（支撑预设→自定义微调切换）

### 去重流水线（14.6）
- [x] execute_item：_deduped 镜像输出已存在 SkippedSignal → ClipContext 依序 apply → 单条 ffmpeg(vf 链 join + setpts/atempo 注入 + EncoderSpec) → SafeFileOps 原子写出 → 前后重复度对比（复用 ReportDao.latest_for 缓存特征，对同一批 targets 重算）→ DedupItemResult(before_pct, after_pct) → >3×dur WARN

### 测试（对照 14.7）
- [x] 合成帧序列切分断言（纯色跳变=切镜）；棋盘格平移断言 pan 方向与数值
- [x] SimilarityCalculator 手算期望值表驱动 + 边界值域自检 + version 抛错
- [x] 五手法 validate/apply 断言滤镜串与参数 clamp
- [x] recommend 边界值（0.49/0.50/0.80/0.81）；seed 一致性
- [x] DedupPipeline：DummyProvider+假 ffmpeg 输出生成、_deduped 命名、镜像层级、before/after 字段存在
- [x] CandidateSearcher：1 平台正常 + 1 平台不可用 → 报告 unavailable 标注且整体成功

## 完成标准
14.7 用例全过；关键算法模块（similarity/scene/motion/rhythm/postprocess）覆盖率 ≥90%；输出位于 已去重/ 镜像层级。
