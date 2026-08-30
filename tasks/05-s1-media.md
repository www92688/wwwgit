# S1 - 媒体引擎服务

> 模块目标：FFmpeg/ffprobe 子进程唯一封装点：命令构建、执行、进度解析、媒体探测、抽帧、编码规范。上层不出现裸 subprocess 调 ffmpeg。
> 设计依据：`design/max_design.md` 第八章
> 前置依赖：01-common
> 输出位置：`src/ych/services/s1_media/`

## 任务清单

### EncoderSpec（`encoder_spec.py`）
- [x] frozen dataclass 默认值：libx264/crf=20/preset=medium/yuv420p + aac/128k/44100 + movflags=+faststart
- [x] `to_args(with_audio)`：无音轨素材不写音频流

### FFmpegRunner（`ffmpeg_runner.py`）
- [x] `locate_binaries()`：打包资源 runtime/ → 设置项 → PATH；找不到抛 MED001
- [x] `run(args, timeout_s, on_line, token) -> int`：阻塞执行，stderr 逐行回调（T-6 防管道死锁）；超时/取消 kill 进程树（MED011/TASK004）
- [x] `parse_progress_line(line)`：从 `time=00:01:23.45` 提取秒数（纯函数）
- [x] `run_pipe(decode_args, encode_args, frame_cb, total_frames, on_progress, token)`：
  - [x] 解码进程 `-vf fps=<f>,format=bgr24 -f rawvideo pipe:1`
  - [x] Python 逐帧读 w*h*3 字节 → frame_cb → 写编码进程 stdin
  - [x] 编码进程 rawvideo 输入 + 额外 -vf 滤镜链 + EncoderSpec → OUT.mp4
  - [x] 内存恒定 ≈2 帧；进度=已回调帧数/total_frames
  - [x] 任一进程非零退出 → MED010 附 stderr 尾部 20 行

### ProbeService（`probe_service.py`）
- [x] `probe(path) -> MediaInfo`：ffprobe JSON 解析；失败/非媒体 → MED002；扩展名白名单外 → MED003
- [x] 8.3 字段映射表逐项落地：duration/format.duration、fps 分数求值 r_frame_rate、has_audio、soft_subtitle_codec(subrip/ass/mov_text)、size_bytes、format_name

### FrameExtractor（`frame_extractor.py`)
- [x] `Frame(ts, img BGR ndarray)` 类型
- [x] `uniform(path, fps, max_frames)`：dur*fps 超上限时自适应 fps=max_frames/dur
- [x] `single(path, ts)`
- [x] `stream_pairs(path, fps, max_frames)`：生成器逐帧产出相邻帧对（光流用，避免整载内存）

### 测试（对照 8.5）
- [x] parse_progress_line 表驱动
- [x] fake_ffmpeg 替身下 run/run_pipe 全流程单测
- [x] 集成（-m integration）：fixtures 3 秒小视频 run_pipe 端到端输出可再次 probe
- [x] probe 字段映射对照 fixtures 假 JSON 断言

## 完成标准
8.5 测试通过；全局搜索确认 core 层无裸 ffmpeg 子进程调用。

