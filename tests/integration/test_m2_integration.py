# M2 集成测试（-m integration：真实 ffmpeg；对照 13.5）
from __future__ import annotations

from itertools import pairwise
from pathlib import Path
from types import SimpleNamespace

import pytest

from ych.common.cancellation import CancellationToken, SkippedSignal
from ych.common.schemas import BBox, ManualRegions
from ych.core.m2_preprocess.filter_only_processor import FilterOnlyProcessor
from ych.core.m2_preprocess.frame_level_processor import FrameLevelProcessor
from ych.core.m2_preprocess.ops import PreprocessOps, make_preprocess_payload
from ych.core.m2_preprocess.preprocess_pipeline import (
    PreprocessPipeline,
    handle_preprocess_task,
)
from ych.core.m2_preprocess.subtitle_handler import SubtitleHandler
from ych.core.m4_scheduler.fail_record_manager import FailRecordManager
from ych.core.m5_library.archive_service import ArchiveService
from ych.core.m5_library.workdir_manager import WorkDirManager
from ych.services.s1_media.ffmpeg_runner import FFmpegRunner
from ych.services.s1_media.frame_extractor import FrameExtractor
from ych.services.s1_media.probe_service import ProbeService
from ych.services.s3_db.daos import make_daos
from ych.services.s3_db.database import Database
from ych.services.s5_base.config_service import ConfigService

pytestmark = pytest.mark.integration


class DummyProvider:
    """固定中部 BBox 检出；inpaint 将掩码区填绿（BGR）。"""

    def detect_watermark(self, frames):   # type: ignore[no-untyped-def]
        return [
            [SimpleNamespace(bbox=BBox(0.25, 0.25, 0.5, 0.5),
                             confidence=0.9, label="watermark", ts=t)]
            for (t, _f) in frames
        ]

    def detect_subtitle(self, frames):    # type: ignore[no-untyped-def]
        return [[] for _ in frames]

    def inpaint(self, frame, mask):       # type: ignore[no-untyped-def]
        out = frame.copy()
        out[mask > 0] = (0, 255, 0)
        return out

    def embed_frames(self, frames):       # type: ignore[no-untyped-def]
        raise AssertionError


@pytest.fixture
def m2(tmp_path: Path, media_dir: Path, ffmpeg_bin, ffprobe_bin):
    runner = FFmpegRunner(ffmpeg_path=ffmpeg_bin, ffprobe_path=ffprobe_bin)
    prober = ProbeService(runner)
    extractor = FrameExtractor(runner, prober)
    cfg = ConfigService()
    wd_root = tmp_path / "wd"
    wd_root.mkdir()
    cfg.set("workdir", str(wd_root))
    wd = WorkDirManager(cfg)
    wd.set_workdir(wd_root)
    db = Database(tmp_path / "app.db")
    daos = make_daos(db)
    archive = ArchiveService(wd, cfg, daos.assets, daos.categories)
    provider = DummyProvider()
    subs = SubtitleHandler(runner, extractor, provider)
    filters = FilterOnlyProcessor(runner)
    frames = FrameLevelProcessor(runner, extractor, provider, cfg, prober=prober)
    pipeline = PreprocessPipeline(prober, filters, subs, frames, archive)

    src_dir = wd_root / "素材"
    src_dir.mkdir(parents=True, exist_ok=True)
    src = src_dir / "clip.mp4"
    src.write_bytes((media_dir / "solid.mp4").read_bytes())
    return SimpleNamespace(
        wd=wd, daos=daos, pipeline=pipeline, prober=prober,
        extractor=extractor, media_dir=media_dir,
        src=src, src_dir=src_dir,
    )


def test_manual_region_frame_path_replaces_center(m2, caplog) -> None:
    ops = PreprocessOps(
        remove_watermark_mode="manual",
        watermark_regions=ManualRegions(rects=[BBox(0.25, 0.25, 0.5, 0.5)]),
    )
    with caplog.at_level("INFO", logger="ych.m2"):
        out = m2.pipeline.execute_item(m2.src, ops, None, CancellationToken())
    assert "帧级路径耗时分解" in caplog.text      # 检测/流水线/修复分段计时
    assert out.exists() and out.name.endswith("_cleaned.mp4")
    info = m2.prober.probe(out)
    assert info.duration_s == pytest.approx(1.04, abs=0.1)
    # 输出帧中部为绿色修复块，角落保持原红色（BGR；编解码有轻微色移，用容差）
    frame = m2.extractor.single(out, 0.5)
    c = frame.img[24, 32].astype(int)
    assert c[1] > 180 and c[0] < 80 and c[2] < 80          # 绿色块
    corner = frame.img[2, 2].astype(int)
    assert corner[2] > 180 and corner[0] < 80 and corner[1] < 80   # 原红色


def test_filter_only_crop_and_skip_semantics(m2) -> None:
    ops = PreprocessOps(crop_rect=BBox(0.25, 0.25, 0.5, 0.5))
    out = m2.pipeline.execute_item(m2.src, ops, None, CancellationToken())
    info = m2.prober.probe(out)
    assert (info.width, info.height) == (32, 24)
    assert m2.src.exists()                       # 原文件不破坏

    with pytest.raises(SkippedSignal):           # 已存在 → skipped
        m2.pipeline.execute_item(m2.src, ops, None, CancellationToken())

    ops_sub = PreprocessOps(remove_subtitle_mode="auto")
    with pytest.raises(SkippedSignal):           # 无检出 → skipped 不报错
        m2.pipeline.execute_item(m2.src, ops_sub, None, CancellationToken())


def test_soft_strip_remux_drops_subtitle_stream(m2) -> None:
    src = m2.src_dir / "with_subs.mkv"
    src.write_bytes((m2.media_dir / "with_subs.mkv").read_bytes())
    base_info = m2.prober.probe(src)
    assert base_info.soft_subtitle_codec         # 前置：确有软轨
    ops = PreprocessOps(remove_subtitle_mode="auto")
    out = m2.pipeline.execute_item(src, ops, None, CancellationToken())
    out_info = m2.prober.probe(out)
    assert out_info.soft_subtitle_codec == ""    # 软轨已剥离
    assert out_info.duration_s == pytest.approx(base_info.duration_s, abs=0.2)


def test_batch_partial_failure_isolation(m2) -> None:
    bad = m2.src_dir / "broken.mp4"
    good2 = m2.src_dir / "clip2.mp4"
    good2.write_bytes(m2.src.read_bytes())
    crop = PreprocessOps(crop_rect=BBox(0, 0, 0.5, 1))
    payload = make_preprocess_payload([
        (str(m2.src), crop), (str(bad), crop), (str(good2), crop),
    ])
    task = SimpleNamespace(payload=payload, token=CancellationToken())
    fails = FailRecordManager(m2.daos.fails)
    result = handle_preprocess_task(task, m2.pipeline, fails)

    summary = result.summary
    assert len(summary["outputs"]) == 2          # 好的两条完成
    assert summary["failed"] == 1                # 坏文件隔离
    rows = m2.daos.fails.list_recent()
    assert len(rows) == 1 and rows[0].file_name == "broken.mp4"
    # 失败条目可一键重建（payload 完整保留）
    rebuilt = fails.rebuild_payload(rows[0].id)
    assert rebuilt.data["items"][0]["src"] == str(bad)


def test_frame_path_without_spans_degrades(m2, monkeypatch) -> None:
    """auto 检测无修复区域：无其余处理项→跳过；有裁剪→滤镜单趟（同规格编码）。"""
    frames = m2.pipeline._frames
    monkeypatch.setattr(
        frames._provider, "detect_watermark",
        lambda frames: [[] for _ in frames],
    )

    ops_auto = PreprocessOps(
        remove_watermark_mode="auto", remove_subtitle_mode="auto",
    )
    with pytest.raises(SkippedSignal):
        m2.pipeline.execute_item(m2.src, ops_auto, None, CancellationToken())
    assert not (m2.src_dir / "预处理" / "clip_cleaned.mp4").exists()

    # 剩余处理项有裁剪 → 降级滤镜单趟（同 EncoderSpec，一遍全质量转码）
    ops_crop = PreprocessOps(
        remove_watermark_mode="auto", crop_rect=BBox(0, 0, 0.5, 1),
    )
    out = m2.pipeline.execute_item(m2.src, ops_crop, None, CancellationToken())
    info = m2.prober.probe(out)
    assert (info.width, info.height) == (32, 48)   # 裁剪生效（滤镜路径产物）


def test_batch_progress_report_and_skip_names(m2) -> None:
    """批次进度 (0→1 单调) + summary 携带跳过/失败明细（终态提示用）。"""
    crop = PreprocessOps(crop_rect=BBox(0, 0, 0.5, 1))
    bad = m2.src_dir / "broken2.mp4"
    payload = make_preprocess_payload([(str(m2.src), crop), (str(bad), crop)])
    task = SimpleNamespace(payload=payload, token=CancellationToken())
    fails = FailRecordManager(m2.daos.fails)

    seen: list[float] = []
    result = handle_preprocess_task(task, m2.pipeline, fails, progress=seen.append)

    assert seen[0] == 0.0 and seen[-1] == 1.0
    assert all(b >= a for a, b in pairwise(seen))   # 单调不减
    assert len(result.summary["outputs"]) == 1
    assert result.summary["failed"] == 1
    assert result.summary["failed_msgs"]                 # 失败明细带文件名
    assert result.summary["output_dir"] == str(m2.src_dir / "预处理")

    # 重跑：输出已存在 → skipped，summary 带 skipped_names
    seen2: list[float] = []
    result2 = handle_preprocess_task(
        SimpleNamespace(payload=payload, token=CancellationToken()),
        m2.pipeline, fails, progress=seen2.append,
    )
    assert result2.summary["skipped"] == 1 and result2.summary["failed"] == 1
    assert result2.summary["skipped_names"] == ["clip.mp4"]
    assert seen2[-1] == 1.0
    assert result2.summary["output_dir"] == str(m2.src_dir / "预处理")



def test_segmented_passthrough_copies_unrepaired(
    m2, ffmpeg_bin, ffprobe_bin, caplog,
) -> None:
    """分段直通：拷贝段与源逐字节一致（零重压缩）、修复段生效、时长保持。"""
    import numpy as np

    runner = FFmpegRunner(ffmpeg_path=ffmpeg_bin, ffprobe_path=ffprobe_bin)
    src = m2.src_dir / "seg_test.mp4"
    # 4s、GOP=0.5s（-g 5 @10fps）+ AAC 音轨：关键帧 0,0.5,…,3.5
    code = runner.run([
        "-y",
        "-f", "lavfi", "-i", "testsrc2=size=64x48:rate=10:duration=4",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
        "-map", "0:v", "-map", "1:a", "-shortest",
        "-c:v", "libx264", "-g", "5", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
        str(src),
    ])
    assert code == 0
    probe = m2.prober.probe(src)
    assert probe.vcodec == "h264" and probe.has_audio

    # 手动水印框仅覆盖 1.0~2.0s（中部）→ copy[0,1)+repair[1,2)+copy[2,4]
    ops = PreprocessOps(
        remove_watermark_mode="manual",
        watermark_regions=ManualRegions(
            rects=[BBox(0.3, 0.3, 0.3, 0.3)],
            time_start_s=1.0, time_end_s=2.0,
        ),
    )
    import logging as _logging

    with caplog.at_level("INFO", logger="ych.m2"):
        out = m2.pipeline.execute_item(src, ops, None, CancellationToken())
    assert "分段直通：" in caplog.text          # 确认走了分段而非整片回退

    out_info = m2.prober.probe(out)
    assert out_info.duration_s == pytest.approx(probe.duration_s, abs=0.2)
    assert out_info.has_audio and out_info.acodec == "aac"

    # 拷贝段（2.5s 处）：与源同帧逐字节一致——零重压缩的核心断言
    a = m2.extractor.single(src, 2.5).img
    b = m2.extractor.single(out, 2.5).img
    assert np.array_equal(a, b)

    # 修复段（1.5s 处）：框内像素确实被修复改写
    a2 = m2.extractor.single(src, 1.5).img
    b2 = m2.extractor.single(out, 1.5).img
    y0, y1 = int(48 * 0.3), int(48 * 0.6)
    x0, x1 = int(64 * 0.3), int(64 * 0.6)
    assert not np.array_equal(a2[y0:y1, x0:x1], b2[y0:y1, x0:x1])
