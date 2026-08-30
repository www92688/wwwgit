# 外网能力检测（详设 12.4）：三站探测三态映射 + TTL 缓存 + 跨重启持久化
from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from enum import Enum
from typing import Literal, Protocol

from PySide6.QtCore import QObject, Signal

logger = logging.getLogger("ych.m1")

PersistStatus = Literal["ok", "blocked", "offline"]


class NetStatus(Enum):
    OK = "ok"
    BLOCKED = "blocked"      # 有网但目标不可达（典型为被墙）
    OFFLINE = "offline"      # 无网络（DNS 全挂）


class _ProbeClientLike(Protocol):
    """S4 HttpClient 的探测最小约定。"""

    def probe_url(
        self, url: str, timeout_s: float = 5.0
    ) -> Literal["ok", "dns_fail", "conn_fail"]: ...


class _SettingsDaoLike(Protocol):
    def get(self, key: str) -> str | None: ...

    def set(self, key: str, value: str) -> None: ...


_STATUS_KEY = "foreign_net_status"


class ForeignNetChecker(QObject):
    """国外平台开关打开前的网络能力检测；UI 据此弹窗或放行。"""

    checked = Signal(str)     # NetStatus.value

    PROBE_URLS = (
        "https://www.pexels.com",
        "https://www.pixabay.com",
        "https://www.tiktok.com",
    )

    def __init__(
        self,
        http: _ProbeClientLike,
        config: object,           # ConfigService（仅读 net_probe_ttl_seconds）
        settings: _SettingsDaoLike,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__()
        self._http = http
        self._config = config
        self._settings = settings
        self._clock = clock
        self._last_status: NetStatus | None = None
        self._last_ts: float | None = None
        self._load_persisted()

    # ---- 对外 ----
    @property
    def last_known(self) -> NetStatus | None:
        return self._last_status

    def check(self, force: bool = False) -> NetStatus:
        ttl = self._ttl_seconds()
        now = self._clock()
        if (
            not force
            and self._last_status is not None
            and self._last_ts is not None
            and (now - self._last_ts) < ttl
        ):
            return self._last_status
        results = [
            self._http.probe_url(u, 5.0) for u in self.PROBE_URLS
        ]
        status = self._map_results(results)
        self._last_status = status
        self._last_ts = now
        self._persist(status)
        self.checked.emit(status.value)
        logger.info("外网检测结果：%s（%s）", status.value, results)
        return status

    # ---- 内部 ----
    def _ttl_seconds(self) -> float:
        getter = getattr(self._config, "get_typed", None)
        if callable(getter):
            try:
                return float(getter("net_probe_ttl_seconds", int))
            except Exception:  # 配置未就绪时用默认值
                pass
        return 600.0

    @staticmethod
    def _map_results(
        results: list[Literal["ok", "dns_fail", "conn_fail"]],
    ) -> NetStatus:
        """任一 ok → OK；全部 dns_fail → OFFLINE；混有 conn_fail → BLOCKED。"""
        if any(r == "ok" for r in results):
            return NetStatus.OK
        if results and all(r == "dns_fail" for r in results):
            return NetStatus.OFFLINE
        return NetStatus.BLOCKED

    def _persist(self, status: NetStatus) -> None:
        payload = json.dumps(
            {"status": status.value, "checked_at": int(time.time())},
            ensure_ascii=False,
        )
        try:
            self._settings.set(_STATUS_KEY, payload)
        except Exception as exc:  # 持久化失败不影响本次结论
            logger.warning("外网检测状态写库失败：%s", exc)

    def _load_persisted(self) -> None:
        raw = self._settings.get(_STATUS_KEY)
        if not raw:
            return
        try:
            data = json.loads(raw)
            self._last_status = NetStatus(str(data.get("status")))
        except (json.JSONDecodeError, ValueError, TypeError, AttributeError):
            logger.warning("外网检测持久化状态解析失败，忽略")
