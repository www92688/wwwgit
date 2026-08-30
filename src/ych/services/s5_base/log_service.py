# 日志服务（详设 5.2/5.3）：滚动文件 + 敏感信息脱敏
from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"

# URL query 中 key=/token= 参数值脱敏（HttpClient 记录请求日志时强制经过）
_SENSITIVE_QUERY = re.compile(r"((?:^|[?&])(?:key|token)=)([^&\s]+)", re.IGNORECASE)


class LogService:
    """滚动文件日志：logs/app.log，单文件 5MB × 5 个。"""

    @staticmethod
    def setup(level: str = "INFO") -> None:
        import pathlib

        # FileHandler 不创建父目录，需先建 logs/
        pathlib.Path("logs").mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            "logs/app.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter(_LOG_FORMAT))
        root = logging.getLogger()
        root.setLevel(level)
        # 幂等：重复 setup 不叠加 handler
        for old in list(root.handlers):
            root.removeHandler(old)
        root.addHandler(handler)

    @staticmethod
    def sanitize(msg: str) -> str:
        """过滤 URL query 中 key=/token= 的参数值（替换为 ***）。"""
        return _SENSITIVE_QUERY.sub(r"\1***", msg)
