# 素材扫描健壮性：stat 竞态跳过单文件不中止扫描；不可访问子树不误删索引
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from ych.core.m5_library.scan_indexer import ScanIndexer
from ych.services.s1_media.ffmpeg_runner import FFmpegRunner
from ych.services.s1_media.probe_service import ProbeService
from ych.services.s3_db.daos import make_daos
from ych.services.s3_db.database import Database


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    # probe 走假 runner：命中不了 ffmpeg 也无妨（probe 失败本就被容忍）
    monkeypatch.setattr(
        ProbeService, "probe",
        lambda self, p: type("I", (), {
            "duration_s": 1.0, "width": 64, "height": 48})(),
    )
    wd = tmp_path / "work"
    wd.mkdir()
    daos = make_daos(Database(tmp_path / "app.db"))
    wd_mgr = AnyWorkDir(wd)
    indexer = ScanIndexer(wd_mgr, daos.assets, ProbeService(FFmpegRunner()))
    return {"indexer": indexer, "wd": wd, "daos": daos}


class AnyWorkDir:
    def __init__(self, path: Path) -> None:
        self._path = path

    def workdir(self) -> Path:
        return self._path


def test_stat_race_skips_file_but_scan_completes(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    wd: Path = env["wd"]
    (wd / "a.mp4").write_bytes(b"x")
    gone = wd / "gone.mp4"
    gone.write_bytes(b"x")
    finished: list[int] = []
    env["indexer"].scan_finished.connect(finished.append)

    real_stat = os.stat

    def flaky_stat(path: object, *args: object, **kwargs: object) -> os.stat_result:
        if "gone.mp4" in str(path):
            raise FileNotFoundError(str(path))
        return real_stat(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "stat", flaky_stat)
    added = env["indexer"].incremental_scan()
    # 竞态文件被跳过，另一文件正常入索引；scan_finished 照常发出
    assert added == 1
    assert env["daos"].assets.all_paths() == {str(wd / "a.mp4")}
    assert finished == [1]


def test_walk_permission_error_keeps_indexed_files(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """目录不可访问时不得把其中已索引文件判为失效删除。"""
    wd: Path = env["wd"]
    sub = wd / "锁定目录"
    sub.mkdir()
    (sub / "locked.mp4").write_bytes(b"x")
    assert env["indexer"].incremental_scan() == 1   # 首轮正常入索引

    def walk_without_locked(
        top: object, *args: object, **kwargs: object,
    ):  # type: ignore[no-untyped-def]
        # 模拟"锁定目录"无权限：回调 onerror 且不产出该子树
        onerror = kwargs.get("onerror")
        if onerror is not None:
            onerror(PermissionError(13, " Permission denied"))
        yield (str(wd), ["锁定目录"], [])

    monkeypatch.setattr(os, "walk", walk_without_locked)
    env["indexer"].incremental_scan()
    # locked.mp4 仍保留在索引中（本轮跳过失效清理）
    assert str(sub / "locked.mp4") in env["daos"].assets.all_paths()
