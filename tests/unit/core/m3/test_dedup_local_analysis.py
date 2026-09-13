# 本地相似素材池 + 离线对比闭环测试：
# 分析重复度/去重前后对比在在线平台全不可用时，仍以同关键词本地素材
# 为真实对比对象（而非空报告假 0 分）。
from __future__ import annotations

import os
import zlib
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from ych.common.cancellation import CancellationToken
from ych.common.schemas import (
    CompareReport,
    CompareTarget,
    DimScores,
    FeatureSet,
    MediaInfo,
)
from ych.core.m3_dedup.candidate_cache import CandidateCache
from ych.core.m3_dedup.candidate_searcher import CandidateSearcher
from ych.core.m3_dedup.dedup_pipeline import DedupPipeline
from ych.core.m3_dedup.feature_extractor import _CACHE_CAP, FeatureExtractService
from ych.core.m3_dedup.local_pool import keyword_of, local_similar_pool
from ych.core.m3_dedup.similarity import ReportBuilder, SimilarityCalculator
from ych.services.s5_base.config_service import ConfigService


def _make_layout(wd: Path) -> tuple[Path, list[Path]]:
    """wd/清洗类/地毯/2026-09-05/{src, sib1, sib2}.mp4 + 跨关键词素材。"""
    d = wd / "清洗类" / "地毯" / "2026-09-05"
    d.mkdir(parents=True)
    src = d / "pexels_地毯_001.mp4"
    sibs = [d / "pexels_地毯_002.mp4", d / "pexels_地毯_003.mp4"]
    for p in [src, *sibs]:
        p.write_bytes(b"x")
    cross = wd / "清洗类" / "沙发" / "2026-09-01" / "pexels_沙发_001.mp4"
    cross.parent.mkdir(parents=True)
    cross.write_bytes(b"x")
    return src, sibs


# ---------- local_pool ----------

def test_pool_same_keyword_excludes_self_and_derived(tmp_path: Path) -> None:
    src, sibs = _make_layout(tmp_path)
    (tmp_path / "清洗类" / "地毯" / "2026-09-05" / "a_deduped.mp4").write_bytes(b"x")
    (tmp_path / "清洗类" / "地毯" / "2026-09-05" / "b_cleaned.mp4").write_bytes(b"x")
    (tmp_path / "清洗类" / "地毯" / "2026-09-05" / "note.txt").write_bytes(b"x")

    pool = local_similar_pool(src, tmp_path, limit=8)
    assert set(pool) == set(sibs)          # 只含同关键词的其它原始素材
    assert keyword_of(src, tmp_path) == "地毯"


def test_pool_prefers_same_date_then_caps(tmp_path: Path) -> None:
    src, sibs = _make_layout(tmp_path)
    older = tmp_path / "清洗类" / "地毯" / "2026-09-01"
    older.mkdir()
    older_names = [older / f"pexels_地毯_old{i:02d}.mp4" for i in range(4)]
    for p in older_names:
        p.write_bytes(b"x")

    pool = local_similar_pool(src, tmp_path, limit=3)
    assert pool[:2] == sibs                # 同日期优先
    assert "old" in pool[2].name           # 跨日期的按文件名补在后面


def test_pool_falls_back_to_category(tmp_path: Path) -> None:
    src, _sibs = _make_layout(tmp_path)
    for p in [src.parent / "pexels_地毯_002.mp4",
              src.parent / "pexels_地毯_003.mp4"]:
        p.unlink()
    cross = tmp_path / "清洗类" / "沙发" / "2026-09-01" / "pexels_沙发_001.mp4"

    pool = local_similar_pool(src, tmp_path, limit=8)
    assert pool == [cross]                 # 同关键词为空 → 同大类其它关键词


def test_pool_deduped_mirror_src_maps_to_raw(tmp_path: Path) -> None:
    src, sibs = _make_layout(tmp_path)
    mirror_src = (tmp_path / "已去重" / "清洗类" / "地毯" / "2026-09-05"
                  / "pexels_地毯_001_deduped.mp4")
    mirror_src.parent.mkdir(parents=True)
    mirror_src.write_bytes(b"x")

    assert keyword_of(mirror_src, tmp_path) == "地毯"
    pool = local_similar_pool(mirror_src, tmp_path, limit=8)
    assert set(pool) == {src, *sibs}       # 已去重层对比对象是原始素材


def test_pool_outside_workdir_or_shallow(tmp_path: Path) -> None:
    src, _ = _make_layout(tmp_path)
    assert local_similar_pool(tmp_path / "loose.mp4", tmp_path) == []
    assert local_similar_pool(src, tmp_path / "other") == []
    shallow = tmp_path / "清洗类" / "top.mp4"
    shallow.write_bytes(b"x")
    assert local_similar_pool(shallow, tmp_path) == []


# ---------- CandidateSearcher：本地池兜底 ----------

class _FakeDao:
    def __init__(self) -> None:
        self.saved: list = []

    def add(self, src, report):   # type: ignore[no-untyped-def]
        self.saved.append((src, report))
        return 1


def _feat_for(name: str) -> FeatureSet:
    """每个文件名一个固定单位向量（同名 → 相似度 1，异名 → 正交）。"""
    idx = zlib.crc32(name.encode("utf-8")) % 512
    comp = np.zeros((1, 512), dtype=np.float32)
    comp[0, idx] = 1.0
    return FeatureSet(
        composition=comp, shot_mid_ts=[0.0],
        motion_curve=np.zeros((32, 3), dtype=np.float32),
        rhythm_hist=np.zeros(16, dtype=np.float32),
        cut_rate_curve=np.zeros(32, dtype=np.float32), version="1",
    )


def test_searcher_scores_local_pool_when_platforms_dead(tmp_path: Path) -> None:
    src, sibs = _make_layout(tmp_path)
    feats = {str(p): _feat_for(p.name) for p in [src, *sibs]}

    class DeadPlugin:
        id = "douyin"
        region = "cn"

        def check_available(self):   # type: ignore[no-untyped-def]
            return (False, "PLG010")

    class FakeManager:
        def enabled(self, region):   # type: ignore[no-untyped-def]
            return [DeadPlugin()]

        def availability(self, plugin, force=False):   # type: ignore[no-untyped-def]
            return plugin.check_available()

    searcher = CandidateSearcher(
        feature_extract=lambda p, token=None: feats[str(p)],
        calculator=SimilarityCalculator(),
        builder=ReportBuilder(_FakeDao()),
        plugin_manager=FakeManager(),      # type: ignore[arg-type]
        cache=CandidateCache(downloader=lambda m, d, t: d,
                             root=tmp_path / "cache"),
        workdirs=SimpleNamespace(workdir=lambda: tmp_path),   # type: ignore[arg-type]
        config=ConfigService(), prober=None,   # type: ignore[arg-type]
    )
    report = searcher.run(src, mode="auto", token=CancellationToken())

    local_ok = [t for t in report.targets
                if t.status == "ok" and t.source == "local"]
    assert {t.local_path for t in local_ok} == {str(p) for p in sibs}
    assert all(t.platform_id == "local" for t in local_ok)
    assert all(t.scores is not None for t in local_ok)
    assert report.overall_score > 0.0      # 有真实对比对象，不再是恒 0
    assert "douyin" in report.unavailable_platforms


def test_searcher_no_pool_no_keyword_notes_unavailable(tmp_path: Path) -> None:
    loose = tmp_path / "loose.mp4"
    loose.write_bytes(b"x")

    class FakeManager:
        def enabled(self, region):   # type: ignore[no-untyped-def]
            return []

        def availability(self, plugin, force=False):   # type: ignore[no-untyped-def]
            return (False, "PLG010")

    searcher = CandidateSearcher(
        feature_extract=lambda p, token=None: _feat_for(Path(p).name),
        calculator=SimilarityCalculator(), builder=ReportBuilder(_FakeDao()),
        plugin_manager=FakeManager(),      # type: ignore[arg-type]
        cache=CandidateCache(downloader=lambda m, d, t: d,
                             root=tmp_path / "cache"),
        workdirs=SimpleNamespace(workdir=lambda: tmp_path),   # type: ignore[arg-type]
        config=ConfigService(), prober=None,   # type: ignore[arg-type]
    )
    report = searcher.run(loose, mode="auto", token=CancellationToken())
    assert "no_keyword" in report.unavailable_platforms
    assert report.targets == []


# ---------- DedupPipeline._compare_scores：本地池兜底 ----------

def _ident_feat() -> FeatureSet:
    comp = np.eye(512, dtype=np.float32)[:1]
    return FeatureSet(
        composition=comp, shot_mid_ts=[0.0],
        motion_curve=np.zeros((32, 3), dtype=np.float32),
        rhythm_hist=np.zeros(16, dtype=np.float32),
        cut_rate_curve=np.zeros(32, dtype=np.float32), version="1",
    )


def _ortho_feat() -> FeatureSet:
    comp = np.zeros((1, 512), dtype=np.float32)
    comp[0, 511] = 1.0
    return FeatureSet(
        composition=comp, shot_mid_ts=[0.0],
        motion_curve=np.full((32, 3), 0.5, dtype=np.float32),
        rhythm_hist=np.zeros(16, dtype=np.float32),
        cut_rate_curve=np.zeros(32, dtype=np.float32), version="1",
    )


def _pipeline(tmp_path: Path, wd: Path, feats: dict, reports=None):
    return DedupPipeline(
        runner=None, prober=None, archive=None,      # type: ignore[arg-type]
        workdirs=SimpleNamespace(workdir=lambda: wd),   # type: ignore[arg-type]
        registry=None,                               # type: ignore[arg-type]
        feature_extract=lambda p, token=None: feats[str(Path(p))],
        calculator=SimilarityCalculator(), reports=reports,
    )


def test_compare_scores_pool_fallback_real_numbers(tmp_path: Path) -> None:
    wd = tmp_path / "wd"
    src, sibs = _make_layout(wd)
    out = wd / "已去重" / "清洗类" / "地毯" / "2026-09-05" / "pexels_地毯_001_deduped.mp4"
    # 源与同关键词素材内容一致 → before=100；输出内容不同 → after 显著下降
    feats = {str(p): _ident_feat() for p in [src, *sibs]}
    feats[str(out)] = _ortho_feat()
    pipeline = _pipeline(tmp_path, wd, feats)

    before, after = pipeline._compare_scores(src, out)
    # 相同特征 → 构图/运镜满分、节奏直方图 0 → 综合 87.5%
    assert before == pytest.approx(87.5)
    assert after is not None and after < 50.0


def test_compare_scores_prefers_report_targets(tmp_path: Path) -> None:
    wd = tmp_path / "wd"
    src, _sibs = _make_layout(wd)
    ref = wd / "清洗类" / "地毯" / "2026-09-05" / "pexels_地毯_002.mp4"
    out = wd / "已去重" / "x_deduped.mp4"
    feats = {str(src): _ident_feat(), str(out): _ortho_feat(),
             str(ref): _ident_feat()}

    class FakeReports:
        def latest_for(self, s):   # type: ignore[no-untyped-def]
            return CompareReport(
                src_path=str(src), overall_score=87.5,
                dims=DimScores(0.8, 0.9, 0.9, 0.875),
                weights=(0.5, 0.25, 0.25),
                targets=[CompareTarget(source="manual", local_path=str(ref),
                                       title=ref.name, status="ok")],
            )

    pipeline = _pipeline(tmp_path, wd, feats, reports=FakeReports())
    before, after = pipeline._compare_scores(src, out)
    assert before == 87.5                  # 沿用报告总分，不重算
    assert after is not None and after < 50.0


def test_compare_scores_no_pool_no_report_is_none(tmp_path: Path) -> None:
    wd = tmp_path / "wd"
    wd.mkdir()
    src = wd / "清洗类" / "地毯" / "2026-09-05" / "only.mp4"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"x")
    out = wd / "已去重" / "only_deduped.mp4"
    feats = {str(src): _ident_feat(), str(out): _ortho_feat()}
    pipeline = _pipeline(tmp_path, wd, feats)
    assert pipeline._compare_scores(src, out) == (None, None)


# ---------- FeatureExtractService 特征缓存 ----------

def test_feature_extract_caches_by_file_identity(tmp_path: Path) -> None:
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\x00" * 16)

    class FakeProber:
        def probe(self, p):   # type: ignore[no-untyped-def]
            return MediaInfo(path=str(p), duration_s=1.5, width=32, height=32)

    class FakeExtractor:
        def stream_pairs_all(self, path, eff_fps, max_frames, token=None):   # type: ignore[no-untyped-def]
            for i in range(3):
                yield SimpleNamespace(
                    ts=i / 2.0, img=np.zeros((32, 32, 3), dtype=np.uint8))

    class CountingProvider:
        def __init__(self) -> None:
            self.calls = 0

        def embed_frames(self, pairs):   # type: ignore[no-untyped-def]
            self.calls += 1
            return np.zeros((len(pairs), 512), dtype=np.float32)

    provider = CountingProvider()
    svc = FeatureExtractService(FakeProber(), FakeExtractor(), provider,   # type: ignore[arg-type]
                                max_frames=8)

    f1 = svc.extract(video, CancellationToken())
    f2 = svc.extract(video, CancellationToken())
    assert provider.calls == 1             # 同文件第二次直接命中缓存
    assert f1 is f2

    os.utime(video, None)                  # 文件被替换 → 缓存失效
    svc.extract(video, CancellationToken())
    assert provider.calls == 2


def test_feature_extract_cache_capacity_bounded(tmp_path: Path) -> None:
    class FakeProber:
        def probe(self, p):   # type: ignore[no-untyped-def]
            return MediaInfo(path=str(p), duration_s=1.5, width=32, height=32)

    class FakeExtractor:
        def stream_pairs_all(self, path, eff_fps, max_frames, token=None):   # type: ignore[no-untyped-def]
            yield SimpleNamespace(ts=0.0, img=np.zeros((32, 32, 3),
                                                      dtype=np.uint8))

    class SilentProvider:
        def embed_frames(self, pairs):   # type: ignore[no-untyped-def]
            return np.zeros((len(pairs), 512), dtype=np.float32)

    svc = FeatureExtractService(FakeProber(), FakeExtractor(),   # type: ignore[arg-type]
                                SilentProvider(), max_frames=8)
    for i in range(_CACHE_CAP + 8):
        p = tmp_path / f"v{i}.mp4"
        p.write_bytes(b"\x00" * 8)
        svc.extract(p, CancellationToken())
    assert len(svc._cache) <= _CACHE_CAP


@pytest.mark.parametrize("exc", [KeyError("boom")])
def test_compare_scores_src_extract_failure_returns_none(
    tmp_path: Path, exc: Exception,
) -> None:
    wd = tmp_path / "wd"
    src, _sibs = _make_layout(wd)
    out = wd / "已去重" / "o_deduped.mp4"

    def extract(p, token=None):   # type: ignore[no-untyped-def]
        raise exc

    pipeline = DedupPipeline(
        runner=None, prober=None, archive=None,     # type: ignore[arg-type]
        workdirs=SimpleNamespace(workdir=lambda: wd),  # type: ignore[arg-type]
        registry=None, feature_extract=extract,     # type: ignore[arg-type]
        calculator=SimilarityCalculator(),
    )
    assert pipeline._compare_scores(src, out) == (None, None)
