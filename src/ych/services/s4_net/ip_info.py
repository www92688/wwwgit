# IP 归属信息查询（网络检测面板用）：免费源依次降级，统一字段输出。
# 仅限后台线程调用（阻塞网络 IO）。
from __future__ import annotations

from collections.abc import Callable
from typing import Final

from ych.common.errors import ERR_NET_TIMEOUT, AppError
from ych.services.s4_net.http_client import HttpClient

_TIMEOUT_S = 8.0


def _s(data: dict[str, object], *keys: str) -> str:
    for k in keys:
        v = data.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _parse_ipapi_co(data: dict[str, object]) -> dict[str, str]:
    return {
        "ip": _s(data, "ip"),
        "country": _s(data, "country_name"),
        "country_code": _s(data, "country_code"),
        "region": _s(data, "region"),
        "city": _s(data, "city"),
        "org": _s(data, "org"),
        "isp": _s(data, "org"),
        "asn": _s(data, "asn"),
        "timezone": _s(data, "timezone"),
    }


def _parse_ipwho(data: dict[str, object]) -> dict[str, str]:
    conn = data.get("connection")
    tz = data.get("timezone")
    conn_d = conn if isinstance(conn, dict) else {}
    tz_d = tz if isinstance(tz, dict) else {}
    asn = conn_d.get("asn")
    return {
        "ip": _s(data, "ip"),
        "country": _s(data, "country"),
        "country_code": _s(data, "country_code"),
        "region": _s(data, "region"),
        "city": _s(data, "city"),
        "org": _s(conn_d, "org", "isp"),
        "isp": _s(conn_d, "isp", "org"),
        "asn": f"AS{asn}" if isinstance(asn, int) else _s(conn_d, "asn"),
        "timezone": _s(tz_d, "id") or _s(tz_d, "time_zone"),
    }


def _parse_ipapi_com(data: dict[str, object]) -> dict[str, str]:
    as_field = _s(data, "as")
    asn = as_field.split(" ", 1)[0] if as_field.startswith("AS") else as_field
    return {
        "ip": _s(data, "query"),
        "country": _s(data, "country"),
        "country_code": _s(data, "countryCode"),
        "region": _s(data, "regionName"),
        "city": _s(data, "city"),
        "org": _s(data, "org") or _s(data, "isp"),
        "isp": _s(data, "isp"),
        "asn": asn,
        "timezone": _s(data, "timezone"),
    }


# (查询地址, 解析函数, 需要校验的 ip 字段名)
_SOURCES: Final[tuple[tuple[str, Callable[[dict[str, object]], dict[str, str]]], ...]] = (
    ("https://ipapi.co/json/", _parse_ipapi_co),
    ("https://ipwho.is/", _parse_ipwho),
    ("http://ip-api.com/json/", _parse_ipapi_com),
)


def fetch_ip_info(http: HttpClient) -> dict[str, str]:
    """依次查询免费 IP 归属源，返回统一字段；全部失败抛 NET001。

    字段：ip/country/country_code/region/city/org/isp/asn/timezone（部分可为空串）。
    """
    last_exc: Exception | None = None
    for url, parser in _SOURCES:
        try:
            data = http.get_json(url, timeout_s=_TIMEOUT_S)
        except AppError as exc:
            last_exc = exc
            continue
        if not isinstance(data, dict):
            last_exc = AppError(ERR_NET_TIMEOUT, "IP 信息响应格式异常")
            continue
        info = parser(data)
        if info.get("ip"):
            return info
    raise AppError(
        ERR_NET_TIMEOUT, "无法获取 IP 信息（稍后重试或检查代理）", cause=last_exc,
    )
