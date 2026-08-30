# M5 素材库管理单元测试（对照 10.4 / tasks/07-m5-library.md）
from __future__ import annotations

from pathlib import Path

import pytest

from ych.common.errors import (
    ERR_FILE_WORKDIR_INVALID,
    AppError,
)
from ych.common.schemas import VideoMeta
from ych.core.m5_library.archive_service import ArchiveService
from ych.core.m5_library.category_service import CategoryService
from ych.core.m5_library.scan_indexer import classify_kind
from ych.core.m5_library.workdir_manager import WorkDirManager
from ych.services.s1_media.ffmpeg_runner import FFmpegRunner
from ych.services.s1_media.probe_service import ProbeService
from ych.services.s3_db.daos import make_daos
from ych.services.s3_db.database import Database
from ych.services.s5_base.config_service import ConfigService


@pytest.fixture
def cfg() -> ConfigService:
    return ConfigService()


@pytest.fixture
def m5(tmp_path, cfg):
    work = tmp_path / "work"
    work.mkdir()
    wd_mgr = WorkDirManager(cfg)
    wd_mgr.set_workdir(work)
    db = Database(tmp_path / "app.db")
    daos = make_daos(db)
    runner = FFmpegRunner()
    prober = ProbeService(runner)
    archive = ArchiveService(wd_mgr, cfg, daos.assets, daos.categories)
    cats = CategoryService(daos.categories, wd_mgr)
    from ych.core.m5_library.scan_indexer import ScanIndexer

    indexer = ScanIndexer(wd_mgr, daos.assets, prober)
    return {"wd": wd_mgr, "daos": daos, "archive": archive,
            "cats": cats, "indexer": indexer, "root": tmp_path / "work"}


META = VideoMeta(plugin_id="pexels", video_key="1", duration_s=2.0,
                 width=64, height=48)


def _make_temp(root: Path, name: str) -> Path:
    p = root / name
    p.write_bytes(b"fake-video-bytes")
    return p


# ---------- WorkDirManager ----------
def test_validate_rejects_missing_dir(m5) -> None:
    err = m5["wd"].validate(Path("Z:/no/such/dir"))
    assert err is not None and err.code == "FILE001"


def test_workdir_changed_signal_and_layout(m5, tmp_path) -> None:
    seen: list[Path] = []
    m5["wd"].workdir_changed.connect(seen.append)
    work2 = tmp_path / "work2"
    work2.mkdir()
    m5["wd"].set_workdir(work2)
    assert len(seen) == 1
    assert (work2 / "已去重").is_dir()


def test_workdir_unset_raises(m5) -> None:
    fresh = WorkDirManager(ConfigService())
    with pytest.raises(AppError) as exc:
        fresh.workdir()
    assert exc.value.code == ERR_FILE_WORKDIR_INVALID


# ---------- 归档命名与三级目录 ----------
def test_archive_download_three_level_and_naming(m5) -> None:
    tmp_file = _make_temp(m5["root"], "dl.part.mp4")
    final = m5["archive"].archive_download(META, tmp_file, "地毯清洗")
    rel = final.relative_to(m5["root"])
    parts = rel.parts
    # 相对 workdir：地毯清洗/地毯清洗/YYYY-MM-DD/pexels_地毯清洗_001.mp4
    assert parts[0] == "地毯清洗" and parts[1] == "地毯清洗"
    assert parts[2].count("-") == 2          # YYYY-MM-DD
    assert final.name == "pexels_地毯清洗_001.mp4"
    # 只读保护默认开启
    import os
    import stat

    assert not (os.stat(final).st_mode & stat.S_IWRITE)
    # 解除只读便于后续清理（pytest tmp 清理会失败如果保持只读）
    from ych.common.fsutil import SafeFileOps

    SafeFileOps.protect_readonly(final, False)


def test_next_filename_sequence_increments(m5) -> None:
    today = "2026-08-25"
    names = []
    for _ in range(3):
        path, name = m5["archive"].next_filename("douyin", "压面条", today)
        names.append(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
        from ych.common.fsutil import SafeFileOps

        SafeFileOps.protect_readonly(path, True)
        SafeFileOps.protect_readonly(path, False)
    assert names == [
        "douyin_压面条_001.mp4",
        "douyin_压面条_002.mp4",
        "douyin_压面条_003.mp4",
    ]


def test_mirror_path_cleaned_same_dir(m5) -> None:
    src = m5["root"] / "清洗类" / "地毯" / "2026-01-01" / "a.mp4"
    out = m5["archive"].mirror_path_for_output(src, "_cleaned")
    assert out.name == "a_cleaned.mp4"
    assert out.parent == src.parent


def test_mirror_path_deduped_mirrors_levels(m5) -> None:
    deep = (m5["root"] / "切割类" / "钢管" / "2026-02-03")
    deep.mkdir(parents=True)
    src = deep / "long_name_video.mp4"
    src.write_bytes(b"x")
    out = m5["archive"].mirror_path_for_output(
        src, "_deduped", out_root=m5["root"] / "已去重"
    )
    assert out == (m5["root"] / "已去重" / "切割类" / "钢管" / "2026-02-03"
                   / "long_name_video_deduped.mp4")


def test_mirror_path_outside_workdir_raises(m5, tmp_path) -> None:
    outside = tmp_path / "elsewhere" / "v.mp4"
    with pytest.raises(AppError):
        m5["archive"].mirror_path_for_output(
            outside, "_cleaned", out_root=m5["root"]
        )





# ---------- classify_kind 纯函数 ----------
@pytest.mark.parametrize(
    ("name", "rel_dir", "expected"),
    [
        ("a.mp4", "清洗类/地毯/2026-01-01", "raw"),
        ("a_cleaned.mp4", "清洗类/地毯/2026-01-01", "cleaned"),
        ("a_deduped.mp4", "已去重/清洗类/地毯/2026-01-01", "deduped"),
        ("a.mp4", "已去重/清洗类", "deduped"),          # 位于已去重区
        ("a_deduped.mp4", "清洗类", "deduped"),          # 名称兜底
    ],
)
def test_classify_kind(name, rel_dir, expected) -> None:
    assert classify_kind(name, rel_dir) == expected


# ---------- 增量扫描 ----------
def test_incremental_scan_adds_and_cleans(m5, tmp_path) -> None:
    import os

    root = m5["root"]
    # 预置混合目录树：raw / cleaned / deduped（用假 mp4 内容，probe 失败容忍）
    d1 = root / "清洗类" / "地毯" / "2026-01-01"
    d1.mkdir(parents=True)
    (d1 / "pexels_地毯_001.mp4").write_bytes(b"x")
    (d1 / "pexels_地毯_001_cleaned.mp4").write_bytes(b"x")
    d2 = root / "已去重" / "清洗类" / "地毯" / "2026-01-01"
    d2.mkdir(parents=True)
    (d2 / "pexels_地毯_001_deduped.mp4").write_bytes(b"x")
    # 非 mp4 文件应被忽略
    (root / "note.txt").write_text("hi")

    added = m5["indexer"].incremental_scan()
    assert added == 3   # probe 失败的文件也入索引（时长/分辨率留空）
    paths = m5["daos"].assets.all_paths()
    assert len(paths) == 3

    # 第二次扫描：无新增
    assert m5["indexer"].incremental_scan() == 0

    # 删除一个文件后增量清理失效行
    os.remove(d1 / "pexels_地毯_001_cleaned.mp4")
    m5["indexer"].incremental_scan()
    assert len(m5["daos"].assets.all_paths()) == 2


def test_readonly_protect_toggle_off(m5, cfg) -> None:
    cfg.set("readonly_protect_raw", False)
    tmp_file = _make_temp(m5["root"], "dl2.part.mp4")
    final = m5["archive"].archive_download(META, tmp_file, "压面条")
    import os
    import stat

    assert os.stat(final).st_mode & stat.S_IWRITE   # 未设只读
    from ych.common.fsutil import SafeFileOps

    SafeFileOps.protect_readonly(final, True)
    SafeFileOps.protect_readonly(final, False)

