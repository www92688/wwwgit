# IP 信息查询单元测试（responses mock：三源解析 + 降级 + 全败报错）
from __future__ import annotations

import pytest
import responses

from ych.common.errors import AppError
from ych.services.s4_net.http_client import HttpClient
from ych.services.s4_net.ip_info import fetch_ip_info


@pytest.fixture
def client(memory_config) -> HttpClient:
    return HttpClient(memory_config)


def test_first_source_parses_normalized_fields(client) -> None:
    with responses.RequestsMock() as rsps:
        rsps.add(
            responses.GET, "https://ipapi.co/json/",
            json={
                "ip": "1.2.3.4", "country_name": "China",
                "country_code": "CN", "region": "Fujian", "city": "Putian",
                "org": "CHINA UNICOM", "asn": "AS4837",
                "timezone": "Asia/Shanghai",
            },
            status=200,
        )
        info = fetch_ip_info(client)
    assert info["ip"] == "1.2.3.4"
    assert info["country"] == "China"
    assert info["asn"] == "AS4837"
    assert info["isp"] == "CHINA UNICOM"


def test_falls_back_to_second_source(client) -> None:
    with responses.RequestsMock() as rsps:
        rsps.add(responses.GET, "https://ipapi.co/json/", status=503)
        rsps.add(
            responses.GET, "https://ipwho.is/",
            json={
                "ip": "5.6.7.8", "country": "Japan", "country_code": "JP",
                "region": "Tokyo", "city": "Tokyo",
                "connection": {"isp": "NTT", "org": "NTT Com", "asn": 2497},
                "timezone": {"id": "Asia/Tokyo"},
            },
            status=200,
        )
        info = fetch_ip_info(client)
    assert info["ip"] == "5.6.7.8"
    assert info["asn"] == "AS2497"
    assert info["isp"] == "NTT"
    assert info["timezone"] == "Asia/Tokyo"


def test_falls_back_to_ipapi_com_and_extracts_asn(client) -> None:
    with responses.RequestsMock() as rsps:
        rsps.add(responses.GET, "https://ipapi.co/json/", status=503)
        rsps.add(responses.GET, "https://ipwho.is/", status=503)
        rsps.add(
            responses.GET, "http://ip-api.com/json/",
            json={
                "query": "9.9.9.9", "country": "China",
                "countryCode": "CN", "regionName": "Fujian", "city": "Putian",
                "isp": "ChinaUnicom", "org": "CU169",
                "as": "AS4837 CHINA UNICOM", "timezone": "Asia/Shanghai",
            },
            status=200,
        )
        info = fetch_ip_info(client)
    assert info["ip"] == "9.9.9.9"
    assert info["asn"] == "AS4837"
    assert info["org"] == "CU169"


def test_all_sources_fail_raises(client) -> None:
    with responses.RequestsMock() as rsps:
        for url in (
            "https://ipapi.co/json/", "https://ipwho.is/",
            "http://ip-api.com/json/",
        ):
            rsps.add(responses.GET, url, status=503)
        with pytest.raises(AppError):
            fetch_ip_info(client)
