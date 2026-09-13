# 代理热改健壮性：配置变更重建全新会话（旧实现原位改共享 session.proxies，
# requests.Session 非严格线程安全，下载在途时有竞态）
from __future__ import annotations

from ych.services.s4_net.http_client import HttpClient


def test_proxy_change_rebuilds_session(memory_config) -> None:
    client = HttpClient(memory_config)
    old_session = client.session
    assert client.session.proxies == {}

    memory_config.set("proxy_enabled", True)
    memory_config.set("proxy_host", "127.0.0.1")
    memory_config.set("proxy_port", 7897)
    assert client.session is not old_session        # 换新会话而非原位改
    assert client.session.proxies == {
        "http": "http://127.0.0.1:7897",
        "https": "http://127.0.0.1:7897",
    }

    memory_config.set("proxy_enabled", False)
    assert client.session is not old_session
    assert client.session.proxies == {}
