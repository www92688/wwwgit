# M3 集成测试（-m integration：真实 ffmpeg；对照 14.7）
from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from ych.common.cancellation import CancellationToken, SkippedSignal
from ych.common.schemas import SearchFilters, VideoMeta
from ych.core.m3_dedup.candidate_cache import CandidateCache
from ych.core.m3_dedup.candidate_searcher import CandidateSearcher
from ych.core.m3_dedup.dedup_pipeline import DedupPipeline
from ych.core.m3_dedup.feature_extractor import FeatureExtractService
from ych.core.m3_dedup.similarity import ReportBuilder, SimilarityCalculator
from ych.core.m3_dedup.techniques.registry import make_default_registry
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
    """embed_frames 用帧均值哈希派生稳定向量（同素材 → 同向量）。"""

    def embed_frames(self, frames):   # type: ignore[no-untyped-def]
        out = []
        for _ts, img in frames:
            g = cv2_gray(img)
            vec = np.zeros(512, dtype=np.float32)
            for k in range(16):                 # 宽度方向 16 条带（64/4）
                block = g[:, k * 4:(k + 1) * 4]
                vec[k] = float(block.mean()) / 255.0 - 0.5
            out.append(vec / max(float(np.linalg.norm(vec)), 1e-6))
        return np.stack(out)

    def detect_watermark(self, frames):   # type: ignore[no-untyped-def]
        return [[] for _ in frames]

    def detect_subtitle(self, frames):    # type: ignore[no-untyped-def]
        return [[] for _ in frames]

    def inpaint(self, frame, mask):       # type: ignore[no-untyped-def]
        out = frame.copy()
        out[mask > 0] = (0, 255, 0)
        return out


def cv2_gray(img: np.ndarray) -> np.ndarray:
    import cv2

    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


@pytest.fixture
def m3(tmp_path: Path, media_dir: Path, ffmpeg_bin, ffprobe_bin):
    runner = FFmpegRunner(ffmpeg_path=ffmpeg_bin, ffprobe_path=ffprobe_bin)
    prober = ProbeService(runner)
    extractor = FrameExtractor(runner, prober)
    cfg = ConfigService()
    wd_root = tmp_path / "wd"
    (wd_root / "已去重").mkdir(parents=True)
    cfg.set("workdir", str(wd_root))
    wd = WorkDirManager(cfg)
    wd.set_workdir(wd_root)
    db = Database(tmp_path / "app.db")
    daos = make_daos(db)
    archive = ArchiveService(wd, cfg, daos.assets, daos.categories)

    provider = DummyProvider()
    feats = FeatureExtractService(prober, extractor, provider)
    registry = make_default_registry()

    src_dir = wd_root / "清洗类" / "地毯" / "2026-08-25"
    src_dir.mkdir(parents=True, exist_ok=True)
    src = src_dir / "pexels_地毯_001.mp4"
    shutil.copy(media_dir / "checker.mp4", src)

    pipeline = DedupPipeline(
        runner=runner, prober=prober, archive=archive, workdirs=wd,
        registry=registry,
    )
    return SimpleNamespace(
        wd=wd, daos=daos, cfg=cfg, prober=prober, runner=runner,
        extractor=extractor, provider=provider, feats=feats,
        registry=registry, pipeline=pipeline, media_dir=media_dir,
        src=src, wd_root=wd_root,
    )


def test_dedup_pipeline_output_and_skip(m3) -> None:
    params = [
        {"id": "mirror", "params": {"axis": "horizontal"}},
        {"id": "color_filter",
         "params": {"brightness": 0.06, "contrast": 1.1, "saturation": 1.2,
                    "temperature": 0.0, "preset": "none"}},
    ]
    result = m3.pipeline.execute_item(m3.src, params, None, CancellationToken())
    out = result.out_path
    assert out.exists()
    # 已去重/ 镜像层级：已去重/清洗类/地毯/2026-08-25/<name>_deduped.mp4
    rel = out.relative_to(m3.wd_root)
    assert rel.parts[0] == "已去重"
    assert "_deduped" in out.name
    assert out.name.endswith(".mp4")
    info = m3.prober.probe(out)
    assert abs(info.duration_s - 1.5) < 0.3
    assert result.before_pct is None and result.after_pct is None   # 无历史报告

    with pytest.raises(SkippedSignal):      # 幂等：已存在即跳过
        m3.pipeline.execute_item(m3.src, params, None, CancellationToken())


def test_dedup_speed_changes_duration(m3) -> None:
    params = [{"id": "speed", "params": {"factor": 1.25}}]
    result = m3.pipeline.execute_item(m3.src, params, None, CancellationToken())
    info = m3.prober.probe(result.out_path)
    assert info.duration_s < 1.5            # 加速后变短


def test_candidate_searcher_with_fake_plugins(m3) -> None:
    class GoodPlugin:
        id = "douyin"
        region = "cn"

        def check_available(self):   # type: ignore[no-untyped-def]
            return (True, "ok")

        def search(self, keyword, filters, max_count, token):   # type: ignore[no-untyped-def]
            assert isinstance(filters, SearchFilters)
            meta = VideoMeta(
                plugin_id="douyin", video_key="dy1",
                title="候选", page_url="https://x/1",
                download_url="https://cdn/dy1.mp4",
            )
            return [meta]

        def availability(self, plugin, force=False):   # type: ignore[no-untyped-def]
            return (True, "ok")

    class DeadPlugin:
        id = "bilibili"
        region = "cn"

        def check_available(self):   # type: ignore[no-untyped-def]
            return (False, "PLG010")

    class FakeManager:
        def __init__(self) -> None:
            self._plugins = [GoodPlugin(), DeadPlugin()]
            self.downloads = 0

        def enabled(self, region):   # type: ignore[no-untyped-def]
            return [p for p in self._plugins if p.region == region]

        def availability(self, plugin, force=False):   # type: ignore[no-untyped-def]
            return plugin.check_available()

    fake_mgr = FakeManager()

    def fake_download(meta, dest, token):   # type: ignore[no-untyped-def]
        fake_mgr.downloads += 1
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(m3.media_dir / "solid.mp4", dest)
        return dest

    cache = CandidateCache(downloader=fake_download,
                           root=m3.wd_root / "cache")
    calc = SimilarityCalculator()
    builder = ReportBuilder(m3.daos.reports)
    searcher = CandidateSearcher(
        feature_extract=m3.feats.extract, calculator=calc, builder=builder,
        plugin_manager=fake_mgr, cache=cache, workdirs=m3.wd,
        config=m3.cfg, prober=m3.prober,
    )

    report = searcher.run(
        m3.src, mode="auto", keyword="地毯",
        token=CancellationToken(),
    )
    assert fake_mgr.downloads == 1                       # 好平台完成缓存下载
    assert "bilibili" in report.unavailable_platforms    # 坏平台被标注
    ok_targets = [t for t in report.targets if t.status == "ok"]
    assert len(ok_targets) == 1 and ok_targets[0].platform_id == "douyin"
    assert ok_targets[0].scores is not None
    assert report.overall_score >= 0.0
    # 报告落库可回读
    latest = m3.daos.reports.latest_for(m3.src)
    assert latest is not None and latest.overall_score == report.overall_score


def test_feature_extract_service_shapes(m3) -> None:
    feat = m3.feats.extract(m3.src, CancellationToken())
    assert feat.version == "1"
    assert feat.composition.ndim == 2 and feat.composition.shape[1] == 512
    assert feat.motion_curve.shape == (32, 3)
    assert feat.rhythm_hist.shape == (16,)
    assert feat.cut_rate_curve.shape == (32,)
    assert feat.duration_s == pytest.approx(1.5, abs=0.3)
