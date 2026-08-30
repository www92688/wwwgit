# 全局错误码体系与统一业务异常（详设 4.3）
from __future__ import annotations

# ---- NET 域（S4 / M1.5）----
ERR_NET_TIMEOUT = "NET001"        # 连接超时
ERR_NET_DNS_FAIL = "NET002"       # DNS 解析失败
ERR_NET_PROXY = "NET003"          # 代理不可用
ERR_NET_FOREIGN_UNREACHABLE = "NET010"  # 外网不可达

# ---- PLG 域（M1 插件）----
ERR_PLG_KEY_MISSING = "PLG001"    # Key 缺失
ERR_PLG_KEY_INVALID = "PLG002"    # Key 无效(401/403)
ERR_PLG_RATE_LIMITED = "PLG003"   # 限频(429)
ERR_PLG_UNAVAILABLE = "PLG010"    # 平台不可达/暂未开放
ERR_PLG_SCHEMA_CHANGED = "PLG020" # 接口结构变更解析失败

# ---- DL 域（M1.3）----
ERR_DL_WRITE_FAILED = "DL001"     # 分块写入失败
ERR_DL_VERIFY_FAILED = "DL002"    # 校验失败
ERR_DL_NO_SPACE = "DL003"         # 目标盘空间不足
ERR_DL_LIMIT_REACHED = "DL010"    # 达到单次上限（非错误，提示）

# ---- MED 域（S1）----
ERR_MED_FFMPEG_NOT_FOUND = "MED001"   # ffmpeg 未找到
ERR_MED_PROBE_FAILED = "MED002"       # ffprobe 解析失败
ERR_MED_FORMAT_UNSUPPORTED = "MED003" # 格式不支持
ERR_MED_TRANSCODE_FAILED = "MED010"   # 转码退出码非零
ERR_MED_TIMEOUT_KILLED = "MED011"     # 超时被杀

# ---- AI 域（S2）----
ERR_AI_MODEL_MISSING = "AI001"    # 模型文件缺失
ERR_AI_MODEL_LOAD_FAILED = "AI002"  # 模型加载失败
ERR_AI_INVALID_INPUT = "AI003"    # 推理输入非法
ERR_AI_INFER_TIMEOUT = "AI004"    # 推理超时

# ---- DB 域（S3）----
ERR_DB_OPEN_FAILED = "DB001"      # 打开失败
ERR_DB_MIGRATION_FAILED = "DB002" # 迁移失败
ERR_DB_CONSTRAINT = "DB003"       # 唯一约束冲突
ERR_DB_IO = "DB004"               # 磁盘 IO 错误

# ---- TASK 域（M4）----
ERR_TASK_NOT_FOUND = "TASK001"      # 任务不存在
ERR_TASK_BAD_TRANSITION = "TASK002" # 状态迁移非法
ERR_TASK_RETRY_EXHAUSTED = "TASK003"  # 重试超限
ERR_TASK_CANCELED = "TASK004"       # 用户取消

# ---- FILE 域（M5 / common）----
ERR_FILE_WORKDIR_INVALID = "FILE001"   # 工作目录无效
ERR_FILE_NO_WRITE_PERMISSION = "FILE002"  # 无写权限
ERR_FILE_ATOMIC_RENAME = "FILE003"     # 原子重命名失败
ERR_FILE_READONLY_CONFLICT = "FILE004" # 只读保护冲突

# ---- CFG 域（S5）----
ERR_CFG_KEY_MISSING = "CFG001"          # 键不存在且默认值缺失
ERR_CFG_VALUE_DESERIALIZE = "CFG002"    # 值反序列化失败


class AppError(Exception):
    """统一业务异常：code 为错误码，message 为面向用户的中文文案。

    cause 为底层异常，仅供日志记录堆栈，不进入用户可见信息。
    """

    def __init__(
        self,
        code: str,
        message: str,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.cause = cause

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"
