# 归档命名竞态回归：并发同关键词下载不得互相覆盖成品（目录级命名锁）
from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from ych.common.schemas import VideoMeta
from ych.core.m5_library.archive_service import ArchiveService
from ych.core.m5_library.workdir_manager import WorkDirManager
from ych.services.s3_db.daos import make_daos
from ych.services.s3_db.database import Database
from ych.services.s5_base.config_service import ConfigService


def test_concurrent_archives_get_distinct_names(tmp_path: Path) -> None:
    wd_root = tmp_path / "work"
    wd_root.mkdir()
    cfg = ConfigService()
    wd_mgr = WorkDirManager(cfg)
    wd_mgr.set_workdir(wd_root)
    daos = make_daos(Database(tmp_path / "app.db"))
    archive = ArchiveService(wd_mgr, cfg, daos.assets, daos.categories)
    meta = VideoMeta(plugin_id="pexels", video_key="1",
                     duration_s=1.0, width=64, height=48)
    today = datetime.now().strftime("%Y-%m-%d")

    errors: list[Exception] = []

    def worker(i: int) -> None:
        try:
            temp = tmp_path / f"part_{i}.tmp"
            temp.write_bytes(b"video-bytes")
            archive.archive_download(meta, temp, "地毯")
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert not errors
    out_dir = wd_root / "地毯" / "地毯" / today
    files = sorted(out_dir.glob("pexels_地毯_*.mp4"))
    names = [f.name for f in files]
    assert len(files) == 4
    assert len(set(names)) == 4        # 序号互不相同（无覆盖）
    sizes = {f.stat().st_size for f in files}
    assert sizes == {len(b"video-bytes")}   # 每个成品内容完整
