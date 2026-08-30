# schemas 单元测试（对照 tasks/01-common.md 完成标准）
from __future__ import annotations

import numpy as np
import pytest

from ych.common.schemas import (
    BBox,
    CompareReport,
    CompareTarget,
    DimScores,
    FeatureSet,
    ManualRegions,
    MediaInfo,
    ResumeState,
    SearchFilters,
    TaskPayload,
    VideoMeta,
)


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("a.mp4", True),
        ("a.AVI", True),
        ("a.mov", True),
        ("a.mkv", True),
        ("a.flv", True),
        ("a.gif", False),
        ("a.wmv", False),
        ("a", False),
    ],
)
def test_media_info_is_supported_whitelist(filename: str, expected: bool) -> None:
    assert MediaInfo(path=filename).is_supported is expected


def test_video_meta_defaults() -> None:
    meta = VideoMeta(plugin_id="pexels", video_key="1")
    assert meta.watermark_tag == "unknown"
    assert meta.file_size_bytes is None
    assert meta.extra == {}


def test_task_payload_roundtrip() -> None:
    payload = TaskPayload(
        type="download",
        data={"keyword": "地毯清洗", "limit": 20},
    )
    assert payload.type == "download"
    assert payload.data["limit"] == 20


def test_feature_set_default_version() -> None:
    fs = FeatureSet(
        composition=np.zeros((1, 512), dtype=np.float32),
        shot_mid_ts=[0.5],
        motion_curve=np.zeros((32, 3), dtype=np.float32),
        rhythm_hist=np.zeros(16, dtype=np.float32),
        cut_rate_curve=np.zeros(32, dtype=np.float32),
    )
    assert fs.version == "1"


def test_search_filters_defaults_match_design() -> None:
    f = SearchFilters()
    assert f.duration_max_s == 36000.0
    assert f.min_height == 0
    assert f.watermark == "unknown"


def test_bbox_manual_regions_defaults() -> None:
    regions = ManualRegions()
    assert regions.rects == []
    assert regions.time_end_s == -1.0
    regions.rects.append(BBox(x=0.1, y=0.1, w=0.2, h=0.2))
    assert len(regions.rects) == 1


def test_resume_state_defaults() -> None:
    state = ResumeState()
    assert state.downloaded_bytes == 0
    assert state.etag == ""


def test_compare_report_structure() -> None:
    dims = DimScores(composition=0.9, motion=0.8, rhythm=0.7, overall=0.85)
    target = CompareTarget(source="manual", local_path="x.mp4", scores=dims)
    report = CompareReport(
        src_path="a.mp4",
        overall_score=85.0,
        dims=dims,
        weights=(0.5, 0.25, 0.25),
        targets=[target],
    )
    assert report.targets[0].scores is not None
    assert report.overall_score == 85.0
    assert report.unavailable_platforms == []
