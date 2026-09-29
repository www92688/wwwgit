# S4 网络服务单元测试（对照 6.4 / tasks/03-s4-net.md）
from __future__ import annotations

import pytest
import requests
import responses

from ych.common.cancellation import CancellationToken
from ych.common.errors import AppError
from ych.common.schemas import ResumeState
from ych.services.s4_net.http_client import HttpClient


@pytest.fixture
def client(memory_config) -> HttpClient:
    return HttpClient(memory_config)


# ---------- 断点续传：四场景 ----------
BODY = b"A" * 100 + b"B" * 50


def _part_file(tmp_path, size: int):
    part = tmp_path / "video.mp4.part"
    part.write_bytes(b"C" * size)
    return part


def test_resume_206_etag_match_appends(client, tmp_path) -> None:
    part = _part_file(tmp_path, 100)
    captured: dict[str, str] = {}

    def cb(request):
        captured["range"] = request.headers.get("Range", "")
        return (
            206,
            {"ETag": "abc", "Content-Length": "50"},
            BODY[100:],
        )

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            responses.GET, "https://x.com/v.mp4", callback=cb,
            content_type="application/octet-stream",
        )
        state = client.download_stream(
            "https://x.com/v.mp4", tmp_path / "video.mp4",
            ResumeState(downloaded_bytes=100, etag="abc",
                        temp_path=str(part)),
            on_progress=None, token=None,
        )
    # 续传起点正确、追加写成功
    assert captured["range"] == "bytes=100-"
    assert part.read_bytes() == b"C" * 100 + BODY[100:]
    assert state.downloaded_bytes == 150
    assert state.etag == "abc"


def test_resume_status_200_restarts_fresh(client, tmp_path) -> None:
    part = _part_file(tmp_path, 100)
    with responses.RequestsMock() as rsps:
        rsps.add(
            responses.GET, "https://x.com/v.mp4", body=BODY, status=200,
        )
        state = client.download_stream(
            "https://x.com/v.mp4", tmp_path / "video.mp4",
            ResumeState(downloaded_bytes=100, etag="abc",
                        temp_path=str(part)),
            on_progress=None, token=None,
        )
    # 服务端不支持 Range（返回200）→ 整段重下且 etag 置空（详设 6.3）
    assert part.read_bytes() == BODY
    assert state.downloaded_bytes == len(BODY)
    assert state.etag == ""


def test_resume_etag_changed_restarts_fresh(client, tmp_path) -> None:
    part = _part_file(tmp_path, 100)
    with responses.RequestsMock() as rsps:
        rsps.add(
            responses.GET, "https://x.com/v.mp4", body=BODY, status=200,
            headers={"ETag": "CHANGED"},
        )
        state = client.download_stream(
            "https://x.com/v.mp4", tmp_path / "video.mp4",
            ResumeState(downloaded_bytes=100, etag="abc",
                        temp_path=str(part)),
            on_progress=None, token=None,
        )
    assert part.read_bytes() == BODY
    assert state.etag == ""


def test_no_resume_plain_get_creates_part(client, tmp_path) -> None:
    with responses.RequestsMock() as rsps:
        rsps.add(responses.GET, "https://x.com/f.mp4", body=BODY, status=200)
        state = client.download_stream(
            "https://x.com/f.mp4", tmp_path / "f.mp4",
            resume=None, on_progress=None, token=None,
        )
    part = tmp_path / "f.mp4.part"
    assert part.read_bytes() == BODY
    assert state.downloaded_bytes == len(BODY)
    assert state.temp_path.endswith(".part")


def test_cancel_mid_download_keeps_part(client, tmp_path) -> None:
    part = tmp_path / "v.mp4.part"
    token = CancellationToken()

    def cancel_on_first_progress(_ratio: float) -> None:
        token.cancel()

    big = b"Z" * (256 * 1024 + 100)   # 跨两个 chunk
    with responses.RequestsMock() as rsps:
        rsps.add(responses.GET, "https://x.com/big.bin", body=big, status=200)
        from ych.common.cancellation import TaskCanceled

        with pytest.raises(TaskCanceled):
            client.download_stream(
                "https://x.com/big.bin", tmp_path / "v.mp4",
                resume=None, on_progress=cancel_on_first_progress,
                token=token,
            )
    # .part 保留（首个 chunk 已写入），供续传
    assert part.exists()
    assert part.stat().st_size > 0


# ---------- probe_url 三态矩阵 ----------
def test_probe_ok_on_head_response(client) -> None:
    with responses.RequestsMock() as rsps:
        rsps.add(responses.HEAD, "https://a.com", status=200)
        assert client.probe_url("https://a.com") == "ok"


def test_probe_dns_fail(client) -> None:
    err = requests.ConnectionError(
        "Failed to resolve 'nope.invalid': getaddrinfo failed"
    )
    with responses.RequestsMock() as rsps:
        rsps.add(responses.HEAD, "https://nope.invalid", body=err)
        assert client.probe_url("https://nope.invalid") == "dns_fail"


def test_probe_conn_fail_via_get_fallback(client) -> None:
    refused = requests.ConnectionError("Connection refused")
    with responses.RequestsMock() as rsps:
        rsps.add(responses.HEAD, "https://blocked.com", body=refused)
        rsps.add(responses.GET, "https://blocked.com", body=refused)
        assert client.probe_url("https://blocked.com") == "conn_fail"


def test_probe_head_rejected_but_get_ok(client) -> None:
    rejected = requests.ConnectionError("head method rejected")
    with responses.RequestsMock() as rsps:
        rsps.add(responses.HEAD, "https://semi.com", body=rejected)
        rsps.add(responses.GET, "https://semi.com", status=206)
        assert client.probe_url("https://semi.com") == "ok"


# ---------- 缺陷回归：429 不进传输层重试（退避职责归业务层 PLG003） ----------
def test_retry_policy_excludes_429(client) -> None:
    adapter = client.session.get_adapter("https://api.x.com")
    forcelist = adapter.max_retries.status_forcelist or ()
    assert 429 not in forcelist


def test_429_single_request_no_transport_retry(client) -> None:
    calls: list[int] = []

    def _always_429(request):
        calls.append(1)
        return (
            429,
            {"Content-Type": "application/json"},
            b'{"error": "rate limited"}',
        )

    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.add_callback(
            responses.GET, "https://api.x.com/feed", callback=_always_429,
            content_type="application/json",
        )
        # HTTPError(429) → RequestException 分支 → 映射 NET 域 AppError 上抛
        with pytest.raises(AppError):
            client.get_json("https://api.x.com/feed")
    # max_retry=2：若 429 误入 status_forcelist 会打出 3 次请求；修复后仅 1 次
    assert len(calls) == 1


# ---------- probe_latency ----------
def test_probe_latency_returns_ms(client) -> None:
    with responses.RequestsMock() as rsps:
        rsps.add(responses.HEAD, "https://x.com", status=200)
        ms = client.probe_latency("https://x.com")
    assert isinstance(ms, int) and ms >= 0


def test_probe_latency_head_rejected_falls_back_to_get(client) -> None:
    with responses.RequestsMock() as rsps:
        rsps.add(responses.HEAD, "https://x.com", body=requests.ConnectionError())
        rsps.add(responses.GET, "https://x.com", status=206)
        ms = client.probe_latency("https://x.com")
    assert isinstance(ms, int)


def test_probe_latency_unreachable_returns_none(client) -> None:
    with responses.RequestsMock() as rsps:
        rsps.add(responses.HEAD, "https://x.com", body=requests.ConnectTimeout())
        rsps.add(responses.GET, "https://x.com", body=requests.ConnectTimeout())
        assert client.probe_latency("https://x.com") is None


# ---------- 代理热更新 ----------
def test_proxy_config_hot_reload(memory_config) -> None:
    """设置页改代理 → 运行中的 HttpClient 即时生效，无需重启。"""
    client = HttpClient(memory_config)
    assert client.session.proxies == {}

    memory_config.set("proxy_enabled", True)
    memory_config.set("proxy_host", "127.0.0.1")
    memory_config.set("proxy_port", 7897)
    assert client.session.proxies == {
        "http": "http://127.0.0.1:7897",
        "https": "http://127.0.0.1:7897",
    }

    memory_config.set("proxy_enabled", False)
    assert client.session.proxies == {}


# ---------- 死代理自动直连兜底 ----------
def _fake_response(content: bytes) -> requests.Response:
    resp = requests.Response()
    resp.status_code = 200
    resp._content = content
    return resp


def test_proxy_dead_falls_back_to_direct(client, monkeypatch) -> None:
    """代理请求 ProxyError（Clash 关闭残留）→ 标记死亡窗口并直连重试。"""
    calls = {"proxy": 0, "direct": 0}

    def _proxy_boom(*_a: object, **_k: object) -> requests.Response:
        calls["proxy"] += 1
        raise requests.exceptions.ProxyError("connection refused")

    def _direct_ok(*_a: object, **_k: object) -> requests.Response:
        calls["direct"] += 1
        return _fake_response(b'{"ok": true}')

    monkeypatch.setattr(client._session, "get", _proxy_boom)
    monkeypatch.setattr(client._direct, "get", _direct_ok)

    data = client.get_json("https://api.example.com/x")
    assert data == {"ok": True}
    assert calls == {"proxy": 1, "direct": 1}
    # 死亡窗口置位：窗口内的后续请求直接走直连，不再碰代理
    client.get_json("https://api.example.com/x")
    assert calls == {"proxy": 1, "direct": 2}


def test_proxy_dead_direct_also_fails_maps_net_error(
    client, monkeypatch,
) -> None:
    """代理与直连都失败：按直连侧异常映射 NET 域错误码（不误报代理）。"""

    def _proxy_boom(*_a: object, **_k: object) -> requests.Response:
        raise requests.exceptions.ProxyError("connection refused")

    def _direct_boom(*_a: object, **_k: object) -> requests.Response:
        raise requests.exceptions.ConnectionError("no route to host")

    monkeypatch.setattr(client._session, "get", _proxy_boom)
    monkeypatch.setattr(client._direct, "get", _direct_boom)

    with pytest.raises(AppError) as ei:
        client.get_json("https://api.example.com/x")
    assert ei.value.code == "NET001"   # ConnectionError（非 DNS）→ 网络失败


def test_proxy_hot_reload_resets_dead_window(memory_config) -> None:
    """代理配置变更后清除断代标记：新配置立即恢复尝试。"""
    client = HttpClient(memory_config)
    client._proxy_dead_until = 1e12   # 模拟处于死亡窗口
    memory_config.set("proxy_enabled", True)
    memory_config.set("proxy_host", "127.0.0.1")
    memory_config.set("proxy_port", 7897)
    assert client._proxy_dead_until == 0.0


# ---------- 代理地址宽容解析 ----------
def test_split_proxy_address_variants() -> None:
    from ych.services.s4_net.http_client import split_proxy_address

    assert split_proxy_address("127.0.0.1:7890") == ("127.0.0.1", 7890)
    assert split_proxy_address("http://127.0.0.1:7890") == ("127.0.0.1", 7890)
    assert split_proxy_address("http://120.25.100.196:3000") == (
        "120.25.100.196", 3000,
    )
    assert split_proxy_address(" 127.0.0.1 ") == ("127.0.0.1", 0)
    assert split_proxy_address("") == ("", 0)
    assert split_proxy_address("   ") == ("", 0)
    assert split_proxy_address("http://") is None          # 无主机
    assert split_proxy_address("http://h:notaport") is None  # 端口非法


def test_proxy_host_with_embedded_address_applies(memory_config) -> None:
    """历史脏数据：host 里存了完整地址（端口留 0）也要能生效，不得静默停用。"""
    memory_config.set("proxy_enabled", True)
    memory_config.set("proxy_host", "http://120.25.100.196:3000")
    memory_config.set("proxy_port", 0)
    client = HttpClient(memory_config)
    assert client.session.proxies == {
        "http": "http://120.25.100.196:3000",
        "https": "http://120.25.100.196:3000",
    }
