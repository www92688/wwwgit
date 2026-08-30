# Spike 冒烟共享工具：ffmpeg/ffprobe 定位（找不到时跳过，对应 D3 自动跳过策略）
import shutil
from pathlib import Path

import pytest

# winget 安装位（当前 shell PATH 可能未刷新，作为兜底路径）
_WINGET_LINKS = Path(r"C:\Users\rememberme\AppData\Local\Microsoft\WinGet\Links")


def find_binary(name: str) -> Path | None:
    """按 PATH → winget 固定路径顺序查找可执行文件。"""
    found = shutil.which(name)
    if found:
        return Path(found)
    cand = _WINGET_LINKS / f"{name}.exe"
    if cand.exists():
        return cand
    return None


@pytest.fixture(scope="session")
def ffmpeg_bin() -> Path:
    p = find_binary("ffmpeg")
    if p is None:
        pytest.skip("ffmpeg not available")
    return p


@pytest.fixture(scope="session")
def ffprobe_bin() -> Path:
    p = find_binary("ffprobe")
    if p is None:
        pytest.skip("ffprobe not available")
    return p
