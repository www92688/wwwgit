# fsutil 单元测试（对照 10.4 / tasks/01-common.md）
from __future__ import annotations

from pathlib import Path

import pytest

from ych.common.fsutil import SafeFileOps, long_path

PREFIX = "\\\\?\\"


def test_long_path_short_unchanged(tmp_path: Path) -> None:
    p = tmp_path / "short.mp4"
    assert long_path(p) == p


def test_long_path_long_gets_prefix(tmp_path: Path) -> None:
    deep = tmp_path
    for _i in range(12):
        deep = deep / ("很长的目录名" * 6)
    p = deep / "video.mp4"
    result = long_path(p)
    assert str(result).startswith(PREFIX)


def test_atomic_write_success(tmp_path: Path) -> None:
    target = tmp_path / "out.txt"

    def writer(p: Path) -> None:
        p.write_text("hello 原子写", encoding="utf-8")

    SafeFileOps.atomic_write(target, writer)
    assert target.read_text(encoding="utf-8") == "hello 原子写"
    assert not list(tmp_path.glob("*.part.tmp"))


def test_atomic_write_writer_raises_leaves_target_untouched(
    tmp_path: Path,
) -> None:
    target = tmp_path / "out.txt"
    target.write_text("original", encoding="utf-8")

    def bad_writer(_p: Path) -> None:
        raise RuntimeError("模拟写入失败")

    with pytest.raises(RuntimeError):
        SafeFileOps.atomic_write(target, bad_writer)

    # 无半成品残留，原内容未变
    assert list(tmp_path.glob("*.part.tmp")) == []
    assert target.read_text(encoding="utf-8") == "original"


def test_protect_readonly_toggle(tmp_path: Path) -> None:
    import os
    import stat

    f = tmp_path / "raw.mp4"
    f.write_bytes(b"x")

    SafeFileOps.protect_readonly(f, True)
    assert not (os.stat(f).st_mode & stat.S_IWRITE)

    SafeFileOps.protect_readonly(f, False)
    assert os.stat(f).st_mode & stat.S_IWRITE


def test_safe_delete_idempotent_and_readonly(tmp_path: Path) -> None:
    f = tmp_path / "ro.mp4"
    f.write_bytes(b"data")
    SafeFileOps.protect_readonly(f, True)
    SafeFileOps.safe_delete(f)
    assert not f.exists()
    SafeFileOps.safe_delete(f)  # 幂等：不存在也不抛


def test_safe_delete_directory_tree(tmp_path: Path) -> None:
    d = tmp_path / "tree"
    (d / "sub").mkdir(parents=True)
    ro = d / "sub" / "ro.mp4"
    ro.write_bytes(b"z")
    SafeFileOps.protect_readonly(ro, True)
    SafeFileOps.safe_delete(d)
    assert not d.exists()
