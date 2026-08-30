# M2 - 视频预处理模块

> 模块目标：去水印/去字幕/裁剪/比例调整/去原声五项预处理，自动+手动双模式，路径分派保证 ≤2×时长。
> 设计依据：`design/max_design.md` 第十三章；需求模块二
> 前置依赖：05-s1-media、06-s2-ai、07-m5-library、08-m4-scheduler；SP-1/SP-5 结论回填参数
> 输出位置：`src/ych/core/m2_preprocess/`

## 任务清单

### Ops 与路径判定（13.1）
- [x] `PreprocessOps` dataclass：去水印/去字幕三态 off|auto|manual + ManualRegions、crop_rect、aspect_target、aspect_strategy crop|pad、strip_audio
- [x] 路径判定纯函数（可测）：need_frame_pass = 去水印≠off 或 去字幕∈{auto硬,manual}；need_transcode；only_soft_strip（软轨且其余全 off）；纯滤镜路径；帧级路径

### 纯滤镜管线（`filter_only_processor.py`）
- [x] `build_vf_chain(ops, probe)` 纯函数：crop=w:h:x:y → scale → pad（strategy 分支）；偶数对齐
- [x] 单条 ffmpeg 执行：-vf chain -an? 编码

### 字幕路由（`subtitle_handler.py`）
- [x] `route(probe, mode)`：auto 且有软轨→soft；auto 无软轨但 OCR 抽样≤6 帧检出底部文字带→hard；都没有→"none" 跳过不报错

### 软字幕剥离
- [x] ffmpeg `-map 0:v -map 0:a? -sn -c copy` 秒级完成

### 帧级修复管线（`frame_level_processor.py`）
- [x] 阶段A 检测：FrameExtractor.uniform(fps≈2, cap=detect_sample_frames=24) → detect_watermark/detect_subtitle → temporal_cluster + stabilize_regions → RegionSpans；manual 模式直接由 ManualRegions 构造全程 span
- [x] 阶段B 流式修复：run_pipe(frame_cb=_repair_frame)；_repair_frame 按 ts 匹配覆盖 span → bbox 像素掩码 → 静态区缓存命中贴缓存 / 否则 inpaint；静态区每 30 帧刷新缓存
- [x] crop/scale/pad 滤镜挂编码端 -vf，一次转码完成全部
- [x] 阶段C 守卫：elapsed > 2×dur 记 WARN 不中断
- [x] `region_temporal.py`：9.4 的 M2 侧薄封装 + 单测宿主

### 流水线入口（`preprocess_pipeline.py`）
- [x] `execute_item(src, ops, on_progress, token)`：probe 校验 MED003 → mirror_path_for_output(_cleaned) → 已存在抛 SkippedSignal → 路径分派 → SafeFileOps 原子收尾
- [x] `make_preprocess_payload(items)` + M2 handler：逐条执行，AppError 捕获 → FailRecordManager.record → continue
- [x] 结果语义落地：success（落盘且 ffprobe 可读）/ skipped（_cleaned 已存在或仅去字幕但未检出）/ failed（重试超限入失败列表）

### 测试（对照 13.5）
- [x] build_vf_chain 参数矩阵表驱动（比例组合 × crop/pad 策略）断言滤镜串
- [x] SubtitleHandler.route 变体用例（软轨有/无 × DummyProvider 文字检出开关）
- [x] FrameLevelProcessor：DummyProvider 固定中部 BBox + 假 ffmpeg run_pipe → 输出帧中部被替换色块，断言遮罩生效与缓存命中计数
- [x] only_soft_strip：fixtures 带字幕轨 mkv → 输出无 subtitle 流且时长不变
- [x] 手动框选模式：ManualRegions 构造 span 全程修复
- [x] 性能冒烟（-m integration）：30s 视频 ≤60s 完成帧级路径
- [x] 批量部分失败隔离：3 条中 1 条坏文件，其余成功且失败条目入 fail_record

## 完成标准
13.5 用例全过；输出命名 `<原名>_cleaned.mp4` 同目录存放且原文件保留只读。
