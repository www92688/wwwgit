# 插件管理器（详设 12.1.2）：发现/分区/启用开关/可用性 TTL 缓存
from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
import time
from collections.abc import Callable
from typing import Protocol

from ych.common.errors import ERR_PLG_UNAVAILABLE
from ych.common.schemas import Region
from ych.core.m1_capture.plugin_base import PlatformPlugin, RateLimiterLike, SkeletonPlugin
from ych.services.s4_net.http_client import HttpClient
from ych.services.s5_base.config_service import ConfigService

logger = logging.getLogger("ych.m1")

_PLUGIN_PKG = "ych.core.m1_capture.plugins"


class _SettingsDaoLike(Protocol):
    """外网检测结果跨重启持久化的最小约定（S3 SettingsDao）。"""

    def get(self, key: str) -> str | None: ...

    def set(self, key: str, value: str) -> None: ...


class PluginManager:
    """插件发现与生命周期管理；deps 注入给每个插件实例。"""

    def __init__(
        self,
        http: HttpClient,
        config: ConfigService,
        limiter: RateLimiterLike | None = None,
        package: str = _PLUGIN_PKG,
        ttl_seconds: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = http
        self._config = config
        self._limiter = limiter
        self._package_name = package
        self._plugins: list[PlatformPlugin] = []
        # plugin.id -> (ok, reason, checked_at_monotonic)
        self._avail_cache: dict[str, tuple[bool, str, float]] = {}
        self._ttl_override = ttl_seconds
        self._clock = clock

    # ---- 发现 ----
    def discover(self) -> None:
        """扫描包内 *_plugin.py，实例化其中定义的 PlatformPlugin 子类。"""
        pkg = importlib.import_module(self._package_name)
        found: dict[str, PlatformPlugin] = {}
        for mod_info in pkgutil.iter_modules(getattr(pkg, "__path__", [])):
            if not mod_info.name.endswith("_plugin"):
                continue
            mod = importlib.import_module(f"{self._package_name}.{mod_info.name}")
            for obj in vars(mod).values():
                if not isinstance(obj, type) or not issubclass(obj, PlatformPlugin):
                    continue
                if obj in (PlatformPlugin, SkeletonPlugin) or inspect.isabstract(obj):
                    continue
                if obj.__module__ != mod.__name__:  # 只收本模块定义，忽略转发导入
                    continue
                instance = obj(self._http, self._config, self._limiter)
                if instance.id and instance.id not in found:
                    logger.info("plugin discovered: %s (%s)", instance.id, mod_info.name)
                    found[instance.id] = instance
        self._plugins = list(found.values())

    # ---- 查询 ----
    def all(self) -> list[PlatformPlugin]:
        return list(self._plugins)

    def get(self, plugin_id: str) -> PlatformPlugin | None:
        for p in self._plugins:
            if p.id == plugin_id:
                return p
        return None

    def by_region(self) -> dict[str, list[PlatformPlugin]]:
        out: dict[str, list[PlatformPlugin]] = {"cn": [], "global": []}
        for p in self._plugins:
            out.setdefault(p.region, []).append(p)
        return out

    def enabled(self, region: Region) -> list[PlatformPlugin]:
        """enabled_plugins 覆盖 + foreign_platforms_enabled 总开关（仅约束国外区）。"""
        master_foreign = bool(self._config.get("foreign_platforms_enabled"))
        overrides_raw = self._config.get("enabled_plugins")
        overrides: dict[str, bool] = {}
        if isinstance(overrides_raw, dict):
            overrides = {str(k): bool(v) for k, v in overrides_raw.items()}
        out: list[PlatformPlugin] = []
        for p in self._plugins:
            if p.region != region:
                continue
            if p.region == "global" and not master_foreign:
                continue
            if not overrides.get(p.id, p.enabled_by_default):
                continue
            out.append(p)
        return out

    # ---- 可用性（TTL 缓存） ----
    def availability(self, plugin: PlatformPlugin, force: bool = False) -> tuple[bool, str]:
        ttl = self._ttl_override
        if ttl is None:
            ttl = float(self._config.get_typed("net_probe_ttl_seconds", int))
        now = self._clock()
        hit = self._avail_cache.get(plugin.id)
        if not force and hit is not None and (now - hit[2]) < ttl:
            return (hit[0], hit[1])
        try:
            ok, reason = plugin.check_available()
        except Exception as exc:  # 契约兜底：check_available 不允许抛异常
            logger.warning("check_available %s 异常：%s", plugin.id, exc)
            ok, reason = False, ERR_PLG_UNAVAILABLE
        self._avail_cache[plugin.id] = (bool(ok), str(reason), now)
        return (bool(ok), str(reason))


__all__ = ["PluginManager"]
