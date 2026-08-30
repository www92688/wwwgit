# pytest 共享 fixture（详设 17.2 统一替身组件清单的 conftest 部分）
import os
import shutil
import subprocess
import sys
from pathlib import Path

# 必须在任何 Qt 模块导入前固定平台，避免测试间平台/单例不一致引发进程崩溃
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import keyring
import pytest

from ych.services.s5_base.config_service import ConfigService

# winget 安装位（当前 shell PATH 可能未刷新，作为兜底路径）
_WINGET_LINKS = Path(r"C:\Users\rememberme\AppData\Local\Microsoft\WinGet\Links")


def _find_binary(name: str) -> Path | None:
    found = shutil.which(name)
    if found:
        return Path(found)
    cand = _WINGET_LINKS / f"{name}.exe"
    return cand if cand.exists() else None


@pytest.fixture(scope="session")
def ffmpeg_bin() -> Path:
    p = _find_binary("ffmpeg")
    if p is None:
        pytest.skip("ffmpeg not available")
    return p


@pytest.fixture(scope="session")
def ffprobe_bin() -> Path:
    p = _find_binary("ffprobe")
    if p is None:
        pytest.skip("ffprobe not available")
    return p


@pytest.fixture
def temp_workdir(tmp_path: Path) -> Path:
    """隔离的工作目录（TempWorkdir，详设 17.2）。"""
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    (workdir / "已去重").mkdir()
    return workdir


class MemoryKeyringBackend:
    """keyring 内存字典后端（测试替身，详设 17.2）。"""

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, username: str, password: str) -> None:
        self._store[(service, username)] = password

    def get_password(self, service: str, username: str) -> str | None:
        return self._store.get((service, username))

    def delete_password(self, service: str, username: str) -> None:
        self._store.pop((service, username), None)


@pytest.fixture
def memory_keyring(monkeypatch: pytest.MonkeyPatch) -> MemoryKeyringBackend:
    """把 keyring 模块级 API 替换为内存实现（读写往返可断言）。"""
    backend = MemoryKeyringBackend()
    monkeypatch.setattr(keyring, "set_password", backend.set_password)
    monkeypatch.setattr(keyring, "get_password", backend.get_password)
    monkeypatch.setattr(keyring, "delete_password", backend.delete_password)
    return backend


@pytest.fixture(scope="session")
def media_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """会话级小样本媒体目录（solid.mp4/checker.mp4/with_subs.mkv）。

    无 ffmpeg 时 skip（D3 自动跳过策略）。
    """
    out = tmp_path_factory.mktemp("media")
    if _find_binary("ffmpeg") is None:
        pytest.skip("ffmpeg not available")
    script = Path(__file__).parent / "fixtures" / "make_media.py"
    python = sys.executable
    subprocess.run(
        [python, str(script), str(out)],
        check=True,
        capture_output=True,
    )
    return out


@pytest.fixture
def memory_config() -> ConfigService:
    """内存模式 ConfigService（无 DAO 注入，供上层单测使用）。"""
    return ConfigService()

