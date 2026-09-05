# 本地代理自动检测单元测试（纯逻辑注入，不触网）
from __future__ import annotations

from ych.services.s4_net.proxy_detect import (
    _system_proxy_candidates,
    detect_local_proxy,
)


def test_system_proxy_checked_first() -> None:
    calls: list[tuple[str, int]] = []

    def checker(host: str, port: int) -> bool:
        calls.append((host, port))
        return True

    result = detect_local_proxy(
        system_proxies=lambda: {"https": "http://127.0.0.1:7890"},
        checker=checker,
    )
    assert result == ("127.0.0.1", 7890)
    assert calls == [("127.0.0.1", 7890)]


def test_schemeless_system_proxy_parsed() -> None:
    candidates = _system_proxy_candidates(
        lambda: {"http": "127.0.0.1:10809", "https": "127.0.0.1:10809"},
    )
    assert candidates == [("127.0.0.1", 10809)]


def test_socks_system_proxy_filtered_then_port_scan_hits() -> None:
    # 系统 socks 代理验证不过（HTTP 客户端用不了）→ 扫描端口 7897 命中
    def checker(host: str, port: int) -> bool:
        return port == 7897

    result = detect_local_proxy(
        system_proxies=lambda: {"https": "socks5://127.0.0.1:10808"},
        checker=checker,
    )
    assert result == ("127.0.0.1", 7897)


def test_dedupes_system_proxy_and_scan_list() -> None:
    calls: list[tuple[str, int]] = []

    def checker(host: str, port: int) -> bool:
        calls.append((host, port))
        return False

    detect_local_proxy(
        system_proxies=lambda: {"https": "http://127.0.0.1:7890"},
        checker=checker,
    )
    assert calls.count(("127.0.0.1", 7890)) == 1


def test_getproxies_crash_treated_as_empty() -> None:
    def boom() -> dict[str, str]:
        raise RuntimeError("registry boom")

    result = detect_local_proxy(system_proxies=boom, checker=lambda h, p: True)
    assert result == ("127.0.0.1", 7890)   # 直接落到端口扫描首项


def test_nothing_found_returns_none() -> None:
    assert detect_local_proxy(
        system_proxies=lambda: {}, checker=lambda h, p: False,
    ) is None
