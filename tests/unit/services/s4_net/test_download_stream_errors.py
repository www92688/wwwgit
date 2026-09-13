# download_stream 传输中途网络异常映射：瞬态断流必须落到可重试的 NET 域
# 错误码（此前裸抛 requests 异常 → 任务以 UNKNOWN 终态失败且重试失效）
from __future__ import annotations

from pathlib import Path

import pytest
import requests

from ych.common.errors import ERR_DL_VERIFY_FAILED, ERR_NET_TIMEOUT, AppError
from ych.core.m4_scheduler.retry_controller import is_retryable
from ych.services.s4_net.http_client import HttpClient


@pytest.fixture
def client(memory_config) -> HttpClient:
    return HttpClient(memory_config)


class _FakeResp:
    """模拟 stream 响应：iter_content 首块后抛传输异常。"""

    def __init__(self, chunks_error: bool = True, status: int = 200) -> None:
        self.status_code = status
        self.headers: dict[str, str] = {}
        self._chunks_error = chunks_error
        self.closed = False

    def iter_content(self, chunk_size: int):  # type: ignore[no-untyped-def]
        yield b"x" * chunk_size
        if self._chunks_error:
            raise requests.exceptions.ChunkedEncodingError(
                "Connection broken: IncompleteRead")

    def close(self) -> None:
        self.closed = True


def test_mid_transfer_exception_maps_to_retryable_net_error(
    client: HttpClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    resp = _FakeResp()
    monkeypatch.setattr(client._session, "get", lambda *a, **k: resp)
    with pytest.raises(AppError) as ei:
        client.download_stream(
            "https://x.com/v.mp4", tmp_path / "v.mp4",
            resume=None, on_progress=None, token=None,
        )
    assert ei.value.code == ERR_NET_TIMEOUT
    assert is_retryable(ei.value.code)      # NET* → 走重试/失败重提体系
    assert resp.closed                       # 响应句柄仍被释放


def test_fallback_get_failure_maps_to_retryable_net_error(
    client: HttpClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ych.common.schemas import ResumeState

    calls: list[int] = []

    def fake_get(*_a: object, **_k: object) -> _FakeResp:
        calls.append(1)
        if len(calls) == 1:
            return _FakeResp(chunks_error=False)   # 200 → 触发整段重下分支
        raise requests.exceptions.ConnectionError("reset by peer")

    monkeypatch.setattr(client._session, "get", fake_get)
    part = tmp_path / "v2.mp4.part"
    part.write_bytes(b"C" * 100)
    with pytest.raises(AppError) as ei:
        client.download_stream(
            "https://x.com/v2.mp4", tmp_path / "v2.mp4",
            resume=ResumeState(downloaded_bytes=100, temp_path=str(part)),
            on_progress=None, token=None,
        )
    assert ei.value.code == ERR_NET_TIMEOUT
    assert is_retryable(ei.value.code)


@pytest.mark.parametrize("status", [403, 404, 503])
def test_http_error_status_rejected_before_saving(
    client: HttpClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    status: int,
) -> None:
    """4xx/5xx 必须在写盘前拒绝：此前 404 错误页会被存成 .part，
    模型下载最终报"模型文件损坏"、媒体下载得到坏文件，误导排查。"""
    resp = _FakeResp(chunks_error=False, status=status)
    monkeypatch.setattr(client._session, "get", lambda *a, **k: resp)
    with pytest.raises(AppError) as ei:
        client.download_stream(
            "https://x.com/v3.mp4", tmp_path / "v3.mp4",
            resume=None, on_progress=None, token=None,
        )
    assert ei.value.code == ERR_DL_VERIFY_FAILED
    assert str(status) in ei.value.message
    assert resp.closed                        # 响应句柄仍被释放
    assert not (tmp_path / "v3.mp4.part").exists()   # 错误页绝不落盘
