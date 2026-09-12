# 配置服务（详设 5.2/5.3）：内存模式退化 + SQLite 回填 + keyring 密钥
from __future__ import annotations

import json
import logging
from typing import ClassVar, Protocol, TypeVar, cast

from PySide6.QtCore import QObject, Signal

from ych.common.errors import ERR_CFG_KEY_MISSING, AppError

logger = logging.getLogger("ych.s5")

_SECRET_SERVICE = "YuChongGou"

T = TypeVar("T")


class _SettingsDaoLike(Protocol):
    """S3 SettingsDao 的最小鸭子类型约定（避免对 S3 的编译期依赖）。"""

    def get(self, key: str) -> str | None: ...

    def set(self, key: str, value: str) -> None: ...

    def all(self) -> dict[str, str]: ...


class ConfigService(QObject):
    """应用配置：详设 2.4 参数表是 _DEFAULTS 的唯一事实来源。

    SQLite 未就绪时退化为纯内存模式；database_ready(dao) 后先加载已持久化
    值、再回填内存期写入，支撑「UI 先行展示 ≤5s」目标。
    """

    # (key, new_value)
    changed = Signal(str, object)

    _DEFAULTS: ClassVar[dict[str, object]] = {
        # 并发与重试
        "download_concurrency": 3,
        "process_concurrency": 2,
        "max_retry": 2,
        "retry_backoff_seconds": [2, 8],
        # 采集
        "download_limit": 20,
        # 自定义 AI 服务（OpenAI 兼容接口）：{id: {name, base_url, model, has_key}}
        "ai_services": {},
        # 默认 AI 服务 id；空 = 未配置（关键词扩展等 AI 功能引导去设置页）
        "ai_default_service": "",
        # 工作台选项记忆（预处理选项 JSON / 去重档位 id）
        "preprocess_options": {},
        "dedup_preset": "",
        # 自动对比候选
        "compare_candidates_per_platform": 20,
        "candidate_cache_ttl_days": 7,
        # 特征与检测
        "feature_max_frames": 300,
        "detect_sample_frames": 24,
        "inpaint_tile_size": 512,
        "dedup_weights": [0.5, 0.25, 0.25],
        # 网络
        "net_probe_ttl_seconds": 600,
        "foreign_platforms_enabled": False,
        "proxy_enabled": False,
        "proxy_host": "",
        "proxy_port": 0,
        # 插件启用覆盖表（详设 12.1.2）：{plugin_id: bool}，缺省用 enabled_by_default
        "enabled_plugins": {},
        # 文件保护
        "readonly_protect_raw": True,
        # 界面
        "language": "zh_CN",
        "theme": "system",   # 浅色 / 深色 / 跟随系统
        "workdir": "",
        "win_geometry": "",   # 主窗口大小/位置（saveGeometry 十六进制串）
        # API Key 只在库中存布尔标记，真实值存 keyring
        "pexels_api_key": False,
        "pixabay_api_key": False,
    }

    def __init__(self) -> None:
        super().__init__()
        self._dao: _SettingsDaoLike | None = None
        self._values: dict[str, object] = {}

    # ---- 查询 ----
    def get(self, key: str) -> object:
        """读取配置；未设置返回默认值；键不存在且无默认值抛 CFG001。"""
        if key in self._values:
            return self._values[key]
        if key in self._DEFAULTS:
            return self._DEFAULTS[key]
        raise AppError(ERR_CFG_KEY_MISSING, f"配置项 {key} 不存在且没有默认值")

    def get_typed(self, key: str, tp: type[T]) -> T:
        """带类型断言的读取（兼容 Python 3.10 的泛型写法）。

        值已由 set/JSON 反序列化保证为原始类型，这里仅做 cast；
        类型不符时由调用方业务逻辑自行校验（避免 object() 误调用）。
        """
        return cast(T, self.get(key))

    # ---- 写入 ----
    def set(self, key: str, value: object) -> None:
        """写内存缓存；SQLite 就绪时同步落库并发射 changed。"""
        self._values[key] = value
        if self._dao is not None:
            self._dao.set(key, json.dumps(value, ensure_ascii=False))
        logger.debug("config set %s", key)
        self.changed.emit(key, value)

    # ---- 启动回填 ----
    def database_ready(self, dao: _SettingsDaoLike) -> None:
        """注入 S3 DAO：加载持久化值 → 回填内存期写入。"""
        self._dao = dao
        # dao.all() 是纯 DB 读取：失败时让 DB 错误码原样上抛，
        # 不包装成 CFG002（反序列化失败由下方逐键降级处理）
        persisted = dao.all()
        for key, raw in persisted.items():
            try:
                self._values[key] = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                logger.warning("配置 %s 值反序列化失败，使用默认值", key)
                self._values.pop(key, None)
        # 内存模式期间的写入回填落库
        for key, value in list(self._values.items()):
            dao.set(key, json.dumps(value, ensure_ascii=False))

    # ---- 密钥（keyring；库中仅布尔标记）----
    def secret_get(self, key: str) -> str:
        """从系统凭据管理器读取密钥；未设置返回空串。"""
        import keyring

        value = keyring.get_password(_SECRET_SERVICE, key)
        return value if value is not None else ""

    def secret_set(self, key: str, value: str) -> None:
        """写入系统凭据管理器；已知 API 键名同步置位布尔标记。"""
        import keyring

        keyring.set_password(_SECRET_SERVICE, key, value)
        marker = f"{key}_api_key"
        if marker in self._DEFAULTS:
            self.set(marker, True)

    def secret_delete(self, key: str) -> None:
        """从系统凭据管理器删除密钥；不存在时忽略。"""
        import contextlib

        import keyring

        with contextlib.suppress(keyring.errors.PasswordDeleteError):
            keyring.delete_password(_SECRET_SERVICE, key)


