# COMMON - 工程骨架与公共件

> 模块目标：搭建可运行的包骨架与跨层公共数据结构/错误码/取消机制，供所有模块复用。
> 设计依据：`design/max_design.md` 第二、三、四章
> 前置依赖：SP P0 通过
> 输出位置：`src/ych/common/`

## 任务清单

### 工程骨架
- [x] `pyproject.toml`：Python >=3.10,<3.13；依赖 PySide6>=6.6,<7 / opencv-python-headless>=4.9 / onnxruntime>=1.17 / numpy>=1.26 / requests>=2.31 / keyring>=24；dev 依赖 pytest/pytest-qt/responses
- [x] 目录树创建：`src/ych/{common,services/{s5_base,s4_net,s3_db,s1_media,s2_ai},core/{m5_library,m4_scheduler,m1_capture/plugins,m2_preprocess,m3_dedup/techniques},ui/{u0_main,u1_capture,u2_preprocess,u3_dedup,u4_failures,u5_settings,u6_common}}` + `tests/{unit,integration,fixtures}` + `runtime/models` + `templates` + `i18n`
- [x] `ych/__init__.py` 定义 `__version__`
- [x] `core/interfaces.py`：业务模块间服务接口协议（Protocol），防止 core 内模块反向依赖（详设三章目录树明确项）
- [x] pytest 配置：unit/component 默认收集，integration 用 `-m integration` 标记隔离

### 公共数据结构（`common/schemas.py`）
- [x] 类型别名：WatermarkTag / TaskState（七态 pending/running/success/failed/skipped/interrupted/**canceled**——⚠ 相对详设 4.1 的偏差修正：11.2 状态机与 worker 均使用 canceled，原六态定义与之矛盾，此处以七态为准）/ Region
- [x] `BBox`（归一化坐标）、`VideoMeta`、`SearchFilters`、`ResumeState`
- [x] `MediaInfo`（含 is_supported 白名单属性：mp4/avi/mov/mkv/flv）
- [x] `ManualRegions`、`FeatureSet`（含 version 字段）、`DimScores`、`CompareTarget`、`CompareReport`
- [x] `TaskPayload`（type 四态 + data dict）

### 错误码体系（`common/errors.py`）
- [x] `AppError(code, message, cause)` 异常类：message 为用户中文文案，日志另记 cause 堆栈
- [x] 全域错误码常量：NET/PLG/DL/MED/AI/DB/TASK/FILE/CFG（对照 4.3 表逐条定义）

### 取消与回调约定（`common/cancellation.py`）
- [x] `CancellationToken`：cancel/cancelled/check()（check 抛 TaskCanceled）
- [x] `TaskCanceled`、`SkippedSignal` 异常类
- [x] `ProgressFn = Callable[[float], None]`、`LineFn = Callable[[str], None]` 类型别名

### 文件工具（`common/fsutil.py`）
- [x] Windows 长路径兼容：超 240 字符自动加 `\\?\` 前缀
- [x] `SafeFileOps.atomic_write(target, writer)`：`.part.tmp` 写完 fsync → os.replace 同卷原子替换 → 失败清理残留
- [x] `SafeFileOps.protect_readonly(path, readonly)`：os.chmod S_IREAD/S_IWRITE
- [x] 安全删除工具

### 测试基建（`tests/fixtures/` + conftest）
- [x] fake_ffmpeg/fake_ffprobe 可执行 Python 脚本（输出固定 probe JSON、生成纯色 rawvideo、可脚本化失败）
- [x] TempWorkdir fixture（隔离工作目录 + :memory: 数据库）
- [x] MemoryConfigService / keyring 内存后端 fixture
- [x] 小样本视频生成脚本（≤3s，纯色/棋盘格/带字幕轨 mkv）

## 完成标准
`import ych.common.*` 全部可用；schemas/errors 与设计文档第四章一一对应；pytest 空跑通过。


