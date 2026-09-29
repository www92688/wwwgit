# ModelDownloader：状态/多源尝试/哈希校验/无源提示/契约校验
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from ych.common.errors import AppError
from ych.common.schemas import ResumeState
from ych.services.s2_ai import model_downloader as md
from ych.services.s2_ai.model_downloader import MODEL_MANIFEST, ModelDownloader


class FakeHttp:
    """download_stream 替身：写入 payload；前 fail_first 个 URL 抛网络错误。"""

    def __init__(self, payload: bytes, fail_first: int = 0) -> None:
        self.payload = payload
        self.fail_first = fail_first
        self.calls: list[str] = []

    def download_stream(self, url, dest, resume, on_progress, token):
        self.calls.append(url)
        if self.fail_first >= len(self.calls):
            raise AppError("NET001", "下载连接失败")
        part = Path(resume.temp_path) if resume and resume.temp_path else (
            Path(str(dest) + ".part")
        )
        part.parent.mkdir(parents=True, exist_ok=True)
        part.write_bytes(self.payload)
        on_progress(1.0)
        return ResumeState(downloaded_bytes=len(self.payload), etag="",
                           total_bytes=len(self.payload), temp_path=str(part))


@pytest.fixture(autouse=True)
def no_contract_check(monkeypatch):
    monkeypatch.setattr(
        ModelDownloader, "_check_contract",
        lambda self, key, path: None,
    )


def test_no_url_reports_manual_placement(tmp_path) -> None:
    dl = ModelDownloader(http=None, base_dir=tmp_path)
    with pytest.raises(AppError) as exc:
        dl.download("watermark")
    assert "手动放置" in exc.value.message


def test_download_success_with_hash(tmp_path, monkeypatch) -> None:
    payload = b"model-bytes"
    monkeypatch.setitem(MODEL_MANIFEST, "subtitle", md.ModelSpec(
        key="subtitle", file="sub.onnx", desc="d", urls=["u1"],
        sha256=hashlib.sha256(payload).hexdigest(),
    ))
    dl = ModelDownloader(http=FakeHttp(payload), base_dir=tmp_path)
    out = dl.download("subtitle")
    assert out.read_bytes() == payload
    assert not Path(str(out) + ".part").exists()
    assert dl.exists("subtitle") and dl.size_of("subtitle") == len(payload)


def test_hash_mismatch_discards_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setitem(MODEL_MANIFEST, "subtitle", md.ModelSpec(
        key="subtitle", file="sub.onnx", desc="d", urls=["u1"],
        sha256="0" * 64,
    ))
    dl = ModelDownloader(http=FakeHttp(b"bad"), base_dir=tmp_path)
    with pytest.raises(AppError):
        dl.download("subtitle")
    assert not (tmp_path / "sub.onnx").exists()
    assert not (tmp_path / "sub.onnx.part").exists()


def test_tries_next_url_on_failure(tmp_path, monkeypatch) -> None:
    monkeypatch.setitem(MODEL_MANIFEST, "subtitle", md.ModelSpec(
        key="subtitle", file="sub.onnx", desc="d",
        urls=["bad1", "bad2", "good"], sha256="",
    ))
    http = FakeHttp(b"ok", fail_first=2)
    dl = ModelDownloader(http=http, base_dir=tmp_path)
    dl.download("subtitle")
    assert http.calls == ["bad1", "bad2", "good"]


def test_all_sources_fail_raises(tmp_path, monkeypatch) -> None:
    monkeypatch.setitem(MODEL_MANIFEST, "subtitle", md.ModelSpec(
        key="subtitle", file="sub.onnx", desc="d", urls=["bad1"], sha256="",
    ))
    dl = ModelDownloader(http=FakeHttp(b"x", fail_first=99), base_dir=tmp_path)
    with pytest.raises(AppError):
        dl.download("subtitle")
    assert dl.exists("subtitle") is False


# ---------- 手动导入（无公开下载源模型） ----------
def test_import_file_copies_and_verifies(tmp_path) -> None:
    dl = ModelDownloader(http=None, base_dir=tmp_path)
    src = tmp_path / "elsewhere.onnx"
    src.write_bytes(b"model-bytes")
    out = dl.import_file("watermark", src)
    assert out == tmp_path / "watermark_yolov8n_640.onnx"
    assert out.read_bytes() == b"model-bytes"
    assert not Path(f"{out}.importing").exists()
    assert dl.exists("watermark")


def test_import_file_missing_source_raises(tmp_path) -> None:
    dl = ModelDownloader(http=None, base_dir=tmp_path)
    with pytest.raises(AppError):
        dl.import_file("watermark", tmp_path / "nope.onnx")
    assert not (tmp_path / "watermark_yolov8n_640.onnx").exists()


def test_import_contract_failure_discards_copy(tmp_path, monkeypatch) -> None:
    def fail(self, key, path):
        raise AppError("AI002", "契约不符")

    monkeypatch.setattr(ModelDownloader, "_check_contract", fail)
    src = tmp_path / "bad.onnx"
    src.write_bytes(b"junk")
    dl = ModelDownloader(http=None, base_dir=tmp_path)
    with pytest.raises(AppError):
        dl.import_file("watermark", src)
    assert not (tmp_path / "watermark_yolov8n_640.onnx").exists()
    assert not (tmp_path / "watermark_yolov8n_640.onnx.importing").exists()
