# M3 智能去重测试（对照 14.7 / tasks/11-m3-dedup.md）：纯算法单测
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from ych.common.errors import ERR_AI_INVALID_INPUT, AppError
from ych.common.schemas import CompareTarget, DimScores, FeatureSet
from ych.core.m3_dedup.motion_analyzer import MotionAnalyzer, _resample
from ych.core.m3_dedup.rhythm_analyzer import RhythmAnalyzer
from ych.core.m3_dedup.scene_detector import SceneDetector, frame_hist_diff
from ych.core.m3_dedup.similarity import (
    SimilarityCalculator,
    composition_similarity,
    rhythm_similarity,
)
from ych.core.m3_dedup.techniques.base import ClipContext
from ych.core.m3_dedup.techniques.registry import make_default_registry


def _info(w: int = 64, h: int = 48) -> object:
    from ych.common.schemas import MediaInfo

    return MediaInfo(path="x.mp4", width=w, height=h, fps=10.0)


# ---------- SceneDetector：纯色跳变 = 切镜 ----------

def _frame(color: tuple[int, int, int]) -> np.ndarray:
    return np.full((48, 64, 3), color, dtype=np.uint8)


def test_scene_split_on_color_jump() -> None:
    ts = [i * 0.5 for i in range(24)]            # fps=2，12s
    imgs = [_frame((0, 0, 255)) for _ in range(12)] + \
           [_frame((255, 0, 0)) for _ in range(12)]
    bounds = SceneDetector().split(ts, imgs, 12.0)
    assert len(bounds) == 1
    assert bounds[0] == pytest.approx(6.0)


def test_scene_single_shot_when_stable() -> None:
    ts = [i * 0.5 for i in range(10)]
    imgs = [_frame((10, 100, 30)) for _ in range(10)]
    assert SceneDetector().split(ts, imgs, 5.0) == []


def test_hist_diff_zero_for_identical() -> None:
    f = _frame((1, 2, 3))
    assert frame_hist_diff(f, f) == pytest.approx(0.0)


def test_shots_of_boundaries() -> None:
    shots = SceneDetector.shots_of([2.0, 5.0], 8.0)
    assert shots == [(0.0, 2.0), (2.0, 5.0), (5.0, 8.0)]


# ---------- MotionAnalyzer：棋盘格平移 → pan 方向与数值 ----------

def _checker(shift_x: float = 0.0, shift_y: float = 0.0,
             size: tuple[int, int] = (64, 48), cell: int = 16) -> np.ndarray:
    w, h = size
    xs = np.arange(w)[None, :] + shift_x
    ys = np.arange(h)[:, None] + shift_y
    phase = ((xs // cell).astype(int) + (ys // cell).astype(int)) % 2
    frame = np.where(phase == 0, 235, 20).astype(np.uint8)
    return cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)


def test_motion_pan_direction_and_magnitude() -> None:
    frames = [_checker(-4.0 * i, 0.0) for i in range(6)]   # 内容右移 → 相机左摇
    curve = MotionAnalyzer().analyze(frames)
    assert curve.shape == (32, 3)
    mean_pan_x = float(curve[:, 0].mean())
    assert mean_pan_x > 0.01                    # 平移量约 4/64=0.0625/帧
    assert abs(float(curve[:, 2].mean())) < 0.05   # zoom≈0


def test_motion_static_frames_near_zero() -> None:
    frames = [_checker(0.0, 0.0)] * 5
    curve = MotionAnalyzer().analyze(frames)
    assert float(np.abs(curve).max()) < 1e-3


def test_resample_constant_series() -> None:
    out = _resample([(0.5, -0.25, 0.1)], 32)
    assert out.shape == (32, 3)
    assert float(out[0, 0]) == 0.5 and float(out[-1, 1]) == -0.25


# ---------- RhythmAnalyzer：直方图与切换率 ----------

def test_rhythm_hist_two_equal_shots() -> None:
    hist = RhythmAnalyzer.rhythm_hist([6.0], 12.0)
    assert len(hist) == 16
    # 两个 6s 镜头：log2(6)=2.58 落在 (4,8] 档（10 档中的第 5 档 → 下标 4）
    assert hist.sum() == pytest.approx(1.0)
    assert hist[4] == pytest.approx(1.0)
    assert hist[8:].sum() == pytest.approx(0.0)   # 补零区


def test_rhythm_cut_rate_peak_at_boundary() -> None:
    curve = RhythmAnalyzer.cut_rate_curve([5.0], 10.0)
    assert len(curve) == 32
    assert curve.max() == pytest.approx(1.0)    # 全局最大归一
    assert curve.min() >= 0.0


def test_rhythm_empty_video() -> None:
    hist, rate = RhythmAnalyzer().compute([], 0.0)
    assert hist.shape == (16,) and rate.shape == (32,)


# ---------- SimilarityCalculator：手算期望值表驱动 ----------

def _feat(comp: np.ndarray | None, curve: np.ndarray,
          hist: np.ndarray, rate: np.ndarray,
          version: str = "1") -> FeatureSet:
    if comp is None:
        arr = np.zeros((0, 512), dtype=np.float32)      # 空镜头
    else:
        arr = np.asarray(comp, dtype=np.float32)
    return FeatureSet(
        composition=arr,
        shot_mid_ts=[0.0] * len(arr),
        motion_curve=curve.astype(np.float32),
        rhythm_hist=hist.astype(np.float32),
        cut_rate_curve=rate.astype(np.float32),
        version=version,
    )


_IDENT = np.eye(512, dtype=np.float32)[:4]


def test_similarity_identical_features() -> None:
    a = _feat(_IDENT, np.zeros((32, 3)), np.eye(16, dtype=np.float32)[0],
              np.zeros(32))
    calc = SimilarityCalculator()
    dims = calc.compare(a, a)
    assert dims.composition == pytest.approx(1.0)
    assert dims.motion == pytest.approx(1.0)
    assert dims.rhythm == pytest.approx(1.0)
    assert dims.overall == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("comp", "curve_a", "curve_b", "expect_motion"),
    [
        # 同向曲线 → S=1；完全反向（|Δ|=2 上限）→ S=0；差 0.5 → 1−0.125
        (np.zeros((2, 2), dtype=np.float32),
         np.full((32, 3), 1.0), np.full((32, 3), 1.0), 1.0),
        (np.zeros((2, 2), dtype=np.float32),
         np.full((32, 3), 1.0), np.full((32, 3), -1.0), 0.0),
        (np.zeros((2, 2), dtype=np.float32),
         np.full((32, 3), 0.5), np.zeros((32, 3)), 0.75),   # mean|Δ|=0.5→1−¼
    ],
)
def test_similarity_motion_formula(comp, curve_a, curve_b, expect_motion) -> None:   # type: ignore[no-untyped-def]
    a = _feat(comp, curve_a, np.eye(16, dtype=np.float32)[0], np.zeros(32))
    b = _feat(comp, curve_b, np.eye(16, dtype=np.float32)[0], np.zeros(32))
    dims = SimilarityCalculator().compare(a, b)
    assert dims.motion == pytest.approx(expect_motion)


def test_similarity_rhythm_formula() -> None:
    h_a = np.zeros(16)
    h_a[0] = 1.0
    h_b = np.zeros(16)
    h_b[1] = 1.0
    r_zeros = np.zeros(32)
    r_ones = np.ones(32) * 0.5
    s = rhythm_similarity(h_a, h_b, r_zeros, r_ones)
    # 直方图交=0；S_rate=1−0.5 → S_rhythm=0.25
    assert s == pytest.approx(0.25)
    # 相同输入 → 1
    assert rhythm_similarity(h_a, h_a, r_zeros, r_zeros) == pytest.approx(1.0)


def test_similarity_composition_chamfer() -> None:
    v = _IDENT
    assert composition_similarity(v, v) == pytest.approx(1.0)
    # 不相交正交基：双向最大匹配均为 0
    other = np.eye(512, dtype=np.float32)[4:8]
    assert composition_similarity(v, other) == pytest.approx(0.0, abs=1e-6)
    # 空镜头 → 0
    assert composition_similarity(np.zeros((0, 512), dtype=np.float32), v) == 0.0


def test_similarity_weighted_overall_and_clip() -> None:
    a = _feat(_IDENT, np.zeros((32, 3)), np.eye(16, dtype=np.float32)[0],
              np.zeros(32))
    b = _feat(None, np.full((32, 3), 1.0), np.zeros(16), np.ones(32))
    # S_comp=0（b 空镜头）；S_motion=1−½·1=0.5；S_rhythm=0
    # overall = 0.25·0.5 + 0.25·0 = 0.125（默认权重 0.5/0.25/0.25）
    dims = SimilarityCalculator().compare(a, b)
    assert dims.overall == pytest.approx(0.125)


def test_similarity_version_mismatch_raises_ai003() -> None:
    a = _feat(_IDENT[:2], np.zeros((32, 3)), np.zeros(16), np.zeros(32), "1")
    b = _feat(_IDENT[:2], np.zeros((32, 3)), np.zeros(16), np.zeros(32), "2")
    with pytest.raises(AppError) as ei:
        SimilarityCalculator().compare(a, b)
    assert ei.value.code == ERR_AI_INVALID_INPUT


def test_similarity_value_domain_selfcheck() -> None:
    """随机特征 200 组：三分量恒在 [0,1]。"""
    rng = np.random.default_rng(42)
    calc = SimilarityCalculator()
    for _ in range(200):
        a = _feat(rng.normal(size=(3, 512)).astype(np.float32),
                  rng.uniform(-2, 2, (32, 3)),
                  rng.dirichlet(np.ones(16)).astype(np.float32),
                  rng.random(32).astype(np.float32))
        b = _feat(rng.normal(size=(2, 512)).astype(np.float32),
                  rng.uniform(-2, 2, (32, 3)),
                  rng.dirichlet(np.ones(16)).astype(np.float32),
                  rng.random(32).astype(np.float32))
        d = calc.compare(a, b)
        for v in (d.composition, d.motion, d.rhythm, d.overall):
            assert 0.0 <= v <= 1.0


# ---------- 五手法 validate/apply ----------

def _ctx() -> ClipContext:
    return ClipContext(src=None, probe=_info(64, 48))   # type: ignore[arg-type]


def test_technique_registry_order_and_dup() -> None:
    reg = make_default_registry()
    ids = [t.id for t in reg.ordered(
        ["speed", "mirror", "border", "color_filter", "crop_scale"])]
    assert ids == ["mirror", "crop_scale", "color_filter", "speed", "border"]
    dup = reg.all()[0]
    with pytest.raises(ValueError):
        reg.register(dup)


def test_mirror_apply_variants() -> None:
    mirror = make_default_registry().get("mirror")
    assert mirror is not None
    cases = {
        "horizontal": ["hflip"],
        "vertical": ["vflip"],
        "both": ["hflip", "vflip"],
    }
    for axis, expect in cases.items():
        ctx = mirror.apply(_ctx(), {"axis": axis})
        assert ctx.vf_filters == expect


def test_crop_scale_filters_and_clamp() -> None:
    cs = make_default_registry().get("crop_scale")
    assert cs is not None
    ctx = _ctx()
    cs.apply(ctx, {"mode": "crop", "margin_pct": 0.10})
    # W=64,H=48：ow=even(64*0.8)=52? 64*0.8=51.2→int 51→偶 50；oh=48*0.8=38.4→38
    assert ctx.vf_filters[0].startswith("crop=")
    assert "scale=64:48" in ctx.vf_filters[0]     # 显式回原分辨率

    clamped = cs.validate_params({"margin_pct": 9.9})
    assert clamped["margin_pct"] == 0.25          # 上限 clamp
    filled = cs.validate_params({})
    assert filled["mode"] == "crop" and filled["margin_pct"] == 0.08


def test_color_filter_numeric_vs_preset() -> None:
    cf = make_default_registry().get("color_filter")
    assert cf is not None
    ctx = cf.apply(_ctx(), {"brightness": 0.1, "temperature": -0.5,
                            "preset": "none"})
    assert any(f.startswith("eq=brightness=0.100") for f in ctx.vf_filters)
    assert any(f.startswith("colorbalance=rm=-0.500") for f in ctx.vf_filters)
    ctx2 = cf.apply(_ctx(), {"preset": "warm"})
    joined = ",".join(ctx2.vf_filters)
    assert ("lut3d" in joined) or ("colorbalance" in joined)   # 文件缺失退化


def test_speed_factor_accumulates() -> None:
    sp = make_default_registry().get("speed")
    assert sp is not None
    ctx = sp.apply(_ctx(), {"factor": 1.1})
    ctx = sp.apply(ctx, {"factor": 0.5})          # clamp 到下限 0.75
    assert ctx.speed_factor == pytest.approx(1.1 * 0.75)


def test_border_solid_and_blur() -> None:
    bd = make_default_registry().get("border")
    assert bd is not None
    solid_ctx = bd.apply(_ctx(), {"style": "solid", "width_pct": 0.05})
    f0 = solid_ctx.vf_filters[0]
    # 偶数化 pad：2*ceil((iw+N)/2) 保证 yuv420p 输出宽高为偶数
    assert f0.startswith("pad=2*ceil((iw+") and ":black" in f0
    blur_ctx = bd.apply(_ctx(), {"style": "blur"})
    joined = ",".join(blur_ctx.vf_filters)
    assert "split" in joined and "gblur=sigma=20" in joined and "overlay" in joined


def test_border_color_sanitized() -> None:
    bd = make_default_registry().get("border")
    assert bd is not None
    # 注入性/异常颜色串 → 回退 black；#RRGGBB → 0xRRGGBB
    ctx = bd.apply(_ctx(), {"style": "solid", "color": "a:b;c,d"})
    assert ":black" in ctx.vf_filters[0]
    ctx2 = bd.apply(_ctx(), {"style": "solid", "color": "#ff8800"})
    assert ":0xff8800" in ctx2.vf_filters[0]


# ---------- SchemeManager：recommend 边界 + seed 一致性 ----------

@pytest.mark.parametrize(
    ("score", "expect"),
    [(0.49, "light"), (0.50, "mid"), (0.80, "mid"), (0.81, "heavy")],
)
def test_recommend_boundaries(score: float, expect: str) -> None:
    from ych.core.m3_dedup.scheme_manager import SchemeManager

    assert SchemeManager.recommend(score) == expect


def test_instantiate_seed_reproducible() -> None:
    from ych.core.m3_dedup.scheme_manager import SchemeManager

    mgr = SchemeManager(make_default_registry(), None)   # type: ignore[arg-type]
    a = mgr.instantiate("heavy", seed=7)
    b = mgr.instantiate("heavy", seed=7)
    c = mgr.instantiate("heavy", seed=8)
    assert a == b
    assert a != c
    assert [item["id"] for item in a] == [
        "mirror", "crop_scale", "color_filter", "speed", "border",
    ]
    # 参数落在预设区间内
    crop = next(i for i in a if i["id"] == "crop_scale")
    m = crop["params"]["margin_pct"]             # type: ignore[index]
    assert 0.12 <= float(str(m)) <= 0.18         # type: ignore[arg-type]


def test_preset_unknown_raises() -> None:
    from ych.core.m3_dedup.scheme_manager import SchemeManager

    with pytest.raises(KeyError):
        SchemeManager(make_default_registry(), None).instantiate("nope")   # type: ignore[arg-type]


# ---------- CompareTarget 排序 / 阈值计数（ReportBuilder 协作件） ----------

def _report_of(targets: list[CompareTarget]):   # type: ignore[no-untyped-def]
    from ych.common.schemas import CompareReport, DimScores

    return CompareReport(
        src_path="a.mp4", overall_score=90.0,
        dims=DimScores(composition=1, motion=1, rhythm=1, overall=1),
        weights=(0.5, 0.25, 0.25), targets=list(targets),
    )


def test_count_over_threshold() -> None:
    from ych.core.m3_dedup.similarity import count_over_threshold

    def target(score: float) -> CompareTarget:
        return CompareTarget(source="auto", scores=DimScores(
            composition=score, motion=score, rhythm=score, overall=score))

    targets = [target(0.9), target(0.85), target(0.3)]
    assert count_over_threshold(_report_of(targets), 0.80) == 2


def test_report_builder_sorts_desc_and_persists() -> None:
    from ych.core.m3_dedup.similarity import ReportBuilder

    saved: list[tuple] = []

    class FakeDao:
        def add(self, src, report):   # type: ignore[no-untyped-def]
            saved.append((src, report))
            return 1

    def target(key: str, score: float) -> CompareTarget:
        return CompareTarget(source="auto", video_key=key, scores=DimScores(
            composition=score, motion=score, rhythm=score, overall=score))

    builder = ReportBuilder(FakeDao())
    report = builder.build(
        Path("a.mp4"),
        [target("low", 0.3), target("high", 0.9), target("mid", 0.6)],
        (0.5, 0.25, 0.25),
        unavailable_platforms=["tiktok"],
    )
    order = [t.video_key for t in sorted(
        report.targets,
        key=lambda t: -(t.scores.overall if t.scores else 0))]
    # targets 按分数降序
    assert [t.video_key for t in report.targets] == order
    assert report.overall_score == pytest.approx(90.0)
    assert report.unavailable_platforms == ["tiktok"]
    assert len(saved) == 1


