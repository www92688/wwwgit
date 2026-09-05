# 本地代理自动检测：系统代理优先，其次扫描常见本地端口，逐个验证可通外网。
# 验证目标用 generate_204（直连环境大概率不通，经代理成功即证明代理可用且能出外网）。
# 检测含阻塞网络 IO，仅限后台线程调用。
from __future__ import annotations

import urllib.request
from collections.abc import Callable
from urllib.parse import urlparse

_VERIFY_URLS = (
    "https://www.google.com/generate_204",
    "https://www.gstatic.com/generate_204",
)

# 常见本地 HTTP 代理端口：Clash / Clash Verge / v2rayN / privoxy 等
COMMON_HTTP_PORTS: tuple[int, ...] = (7890, 7897, 10809, 8888, 8118, 2080)


def _proxy_works(host: str, port: int, timeout_s: float = 2.0) -> bool:
    """用候选代理请求验证地址；任一目标有响应即认为代理可用。"""
    import requests

    proxies = {"http": f"http://{host}:{port}", "https": f"http://{host}:{port}"}
    for url in _VERIFY_URLS:
        try:
            resp = requests.head(
                url, proxies=proxies, timeout=timeout_s, allow_redirects=True,
            )
            resp.close()
            return True
        except requests.exceptions.RequestException:
            continue
    return False


def _system_proxy_candidates(
    getproxies_fn: Callable[[], dict[str, str]],
) -> list[tuple[str, int]]:
    """读系统代理/环境变量，解析出 (host, port) 候选（保序去重）。"""
    try:
        proxies = getproxies_fn()
    except Exception:
        return []
    out: list[tuple[str, int]] = []
    for key in ("https", "http"):
        raw = proxies.get(key)
        if not raw:
            continue
        try:
            parsed = urlparse(raw if "//" in raw else f"http://{raw}")
            host, port = parsed.hostname, parsed.port
        except ValueError:
            continue
        if host and port and (host, port) not in out:
            out.append((host, port))
    return out


def detect_local_proxy(
    system_proxies: Callable[[], dict[str, str]] = urllib.request.getproxies,
    checker: Callable[[str, int], bool] = _proxy_works,
) -> tuple[str, int] | None:
    """返回第一个验证可用的 (host, port)；找不到返回 None。

    顺序：系统代理/环境变量 → 常见本地端口扫描。
    """
    candidates = _system_proxy_candidates(system_proxies)
    for port in COMMON_HTTP_PORTS:
        if ("127.0.0.1", port) not in candidates:
            candidates.append(("127.0.0.1", port))
    for host, port in candidates:
        if checker(host, port):
            return (host, port)
    return None
