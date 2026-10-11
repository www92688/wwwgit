# 特征磁盘缓存：npz 往返 / 版本失效 / 损坏容错 / 容量清理（纯单测，无 ffmpeg）
from pathlib import Path

import numpy as np

from ych.common.schemas import FeatureSet
from ych.core.m3_dedup.feature_extractor import (
    _DISK_CACHE_FILES,
    FeatureExtractService,
)


def _feat(version: str = "1") -> FeatureSet:
    rng = np.random.RandomState(1)
    return FeatureSet(
        composition=rng.rand(3, 512).astype(np.float32),
        shot_mid_ts=[0.5, 1.5, 2.5],
        motion_curve=rng.rand(32, 3).astype(np.float32),
        rhythm_hist=rng.rand(16).astype(np.float32),
        cut_rate_curve=rng.rand(32).astype(np.float32),
        duration_s=3.0,
        version=version,
    )


def _svc(cache_dir: Path) -> FeatureExtractService:
    return FeatureExtractService(
        None, None, None, cache_dir=cache_dir,   # type: ignore[arg-type]
    )


def test_disk_roundtrip(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    src = tmp_path / "a.mp4"
    src.write_bytes(b"x")
    feat = _feat()
    svc._disk_put(src, feat)
    got = svc._disk_get(src)
    assert got is not None
    assert np.allclose(got.composition, feat.composition)
    assert got.shot_mid_ts == feat.shot_mid_ts
    assert np.allclose(got.motion_curve, feat.motion_curve)
    assert got.duration_s == feat.duration_s
    assert got.version == "1"
    assert not list(tmp_path.rglob("*.tmp"))     # 原子写中间文件不残留


def test_disk_version_mismatch_invalidates(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    src = tmp_path / "a.mp4"
    src.write_bytes(b"x")
    svc._disk_put(src, _feat(version="9"))       # 特征版本不同 → 不可复用
    assert svc._disk_get(src) is None


def test_disk_corrupt_file_recomputes(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    src = tmp_path / "a.mp4"
    src.write_bytes(b"x")
    svc._disk_put(src, _feat())
    svc._disk_file(src).write_bytes(b"corrupt")  # 模拟磁盘损坏
    assert svc._disk_get(src) is None
    svc._disk_put(src, _feat())                  # 损坏后可覆盖重建
    assert svc._disk_get(src) is not None


def test_disk_key_follows_file_identity(tmp_path: Path) -> None:
    """同路径不同内容（mtime/size 变化）→ 键变化 → 天然失效。"""
    svc = _svc(tmp_path)
    src = tmp_path / "a.mp4"
    src.write_bytes(b"x")
    svc._disk_put(src, _feat())
    f1 = svc._disk_file(src)
    src.write_bytes(b"xxxxxxxx")                 # 内容变化 → mtime_ns/size 变
    assert svc._disk_file(src) != f1
    assert svc._disk_get(src) is None


def test_prune_keeps_cap(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    n = _DISK_CACHE_FILES + 20
    for i in range(n):
        src = tmp_path / f"v{i}.mp4"
        src.write_bytes(b"x")
        svc._disk_put(src, _feat())
    files = list(tmp_path.glob("*.npz"))
    assert len(files) == _DISK_CACHE_FILES
    assert svc._disk_get(tmp_path / f"v{n - 1}.mp4") is not None   # 最新保留
