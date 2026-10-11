# 去重真实链路验证（一组视频完整跑通：生成素材 → 真实流水线 → 核对结果）
# 用法：python scripts/dedup_verify.py
# 产物目录：build/dedup_verify/（素材、已去重输出、特征缓存，可人工复核）
#
# 素材组（同关键词目录，模拟"同关键词下载的一批素材"）：
#   base.mp4   原片（动态测试画面+音轨）
#   dup_1.mp4  base 的搬运仿品①：轻裁切+角标色块+重编码（内容一致）
#   dup_2.mp4  base 的搬运仿品②：微调对比度+角标+重编码（内容一致）
#   unrel_1.mp4 无关视频①（分形画面）
#   unrel_2.mp4 无关视频②（彩条）
#
# 核对点：
#   1) 去重前后对同一批对比对象的重复度：base 应"高重复 → 明显下降"
#   2) 打分真实性：搬运仿品与 base 的相似度应显著高于无关视频
#   3) 产物有效性：已去重/ 镜像目录、ffprobe 可读、时长/音轨保持、镜像画面生效
#   4) 复跑跳过：输出已存在 → SkippedSignal（UI"重新生成"依赖的路径）
#   5) 性能：旧版特征提取（全帧对全分辨率光流）vs 当前实现，端到端计时
# 任一断言失败 → 退出码 1。
from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np

from ych.common.cancellation import CancellationToken, SkippedSignal
from ych.common.schemas import FeatureSet
from ych.core.m3_dedup.dedup_pipeline import DedupPipeline
from ych.core.m3_dedup.feature_extractor import (
    SAMPLE_FPS,
    CompositionEmbedder,
    FeatureExtractService,
)
from ych.core.m3_dedup.motion_analyzer import _pair_motion, _resample
from ych.core.m3_dedup.rhythm_analyzer import RhythmAnalyzer
from ych.core.m3_dedup.scene_detector import SceneDetector
from ych.core.m3_dedup.scheme_manager import SchemeManager
from ych.core.m3_dedup.similarity import SimilarityCalculator
from ych.core.m3_dedup.techniques.crop_scale import even_down
from ych.core.m3_dedup.techniques.registry import make_default_registry
from ych.core.m5_library.archive_service import ArchiveService
from ych.core.m5_library.workdir_manager import WorkDirManager
from ych.services.s1_media.ffmpeg_runner import FFmpegRunner
from ych.services.s1_media.frame_extractor import FrameExtractor
from ych.services.s1_media.probe_service import ProbeService
from ych.services.s2_ai.local_provider import LocalProvider
from ych.services.s2_ai.model_registry import ModelRegistry
from ych.services.s3_db.daos import make_daos
from ych.services.s3_db.database import Database
from ych.services.s5_base.config_service import ConfigService

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "build" / "dedup_verify"
WD = OUT / "workdir"
KEYWORD_DIR = WD / "生活好物" / "收纳好物" / "2026-10-07"
DURATION = 24.0

_checks: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    _checks.append((ok, name, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}"
          + (f"（{detail}）" if detail else ""))


# ---------------------------------------------------------------- 素材生成
PERF_DURATION = 150.0   # 性能对照专用长素材（帧数达到 300 上限，触发抽样路径）


def _encode(ffmpeg: Path, dest: Path, src_filter: str, video: str,
            audio: str, duration: float = DURATION) -> None:
    # 源已带自有参数（gradients=speed=…）用 : 续接；裸源名用 = 进入具名参数
    sep = ":" if "=" in video else "="
    subprocess.run([
        str(ffmpeg), "-y",
        "-f", "lavfi", "-i",
        f"{video}{sep}size=1280x720:rate=30:duration={duration}",
        "-f", "lavfi", "-i", f"aevalsrc='{audio}':s=44100:d={duration}",
        "-vf", src_filter,
        "-map", "0:v", "-map", "1:a", "-ac", "2",
        "-c:v", "libx264", "-g", "60", "-crf", "20", "-preset", "medium",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
        "-t", f"{duration}",
        str(dest),
    ], check=True, capture_output=True)


def _encode_from(ffmpeg: Path, dest: Path, src: Path, vf: str) -> None:
    """从已有素材派生"搬运仿品"：画面微扰 + 重编码（内容保持一致）。"""
    subprocess.run([
        str(ffmpeg), "-y", "-i", str(src),
        "-vf", vf,
        "-map", "0:v", "-map", "0:a", "-ac", "2",
        "-c:v", "libx264", "-g", "60", "-crf", "23", "-preset", "medium",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
        str(dest),
    ], check=True, capture_output=True)


def _encode_multi(ffmpeg: Path, dest: Path, sources: list[str],
                  durations: list[float], audio: str,
                  tail_filter: str) -> None:
    """多场景拼接 + 全片缓推镜：贴近真实短视频（有切镜、有运镜）。"""
    inputs: list[str] = []
    for src, dur in zip(sources, durations, strict=True):
        sep = ":" if "=" in src else "="
        inputs += ["-f", "lavfi", "-i",
                   f"{src}{sep}size=1280x720:rate=30:duration={dur}"]
    n = len(sources)
    total = sum(durations)
    fc = "".join(f"[{i}:v]" for i in range(n)) + f"concat=n={n}:v=1:a=0[cat];"
    zoom = (f"[cat]zoompan=z='min(1+0.4*on/(30*{int(total * 30)}),1.4)'"
            ":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d=1:s=1280x720:fps=30,{tail_filter}[v]")
    subprocess.run([
        str(ffmpeg), "-y", *inputs,
        "-f", "lavfi", "-i", f"aevalsrc='{audio}':s=44100:d={total}",
        "-filter_complex", fc + zoom,
        "-map", "[v]", "-map", f"{n}:a", "-ac", "2",
        "-c:v", "libx264", "-g", "60", "-crf", "20", "-preset", "medium",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
        "-t", f"{total}",
        str(dest),
    ], check=True, capture_output=True)


def build_media_set(ffmpeg: Path) -> dict[str, Path]:
    """一组视频：1 原片 + 2 搬运仿品（内容一致、画面微扰）+ 2 无关视频。"""
    KEYWORD_DIR.mkdir(parents=True, exist_ok=True)
    base = KEYWORD_DIR / "base.mp4"
    if not base.exists():
        # 原片：三场景拼接（testsrc2 / 动态渐变 / 色相偏移画面）+ 缓慢推镜
        # + 角部装饰块；切镜与运镜让节奏/运镜特征有真实内容
        _encode_multi(
            ffmpeg, base,
            ["testsrc2", "gradients=speed=0.08", "testsrc"],
            [12.0, 12.0, 12.0],
            "0.4*sin(2*PI*220*t)*(1+sin(2*PI*0.5*t))",
            "drawbox=x=1100:y=40:w=140:h=90:color=orange@0.9:t=fill,"
            "drawbox=x=60:y=600:w=220:h=70:color=green@0.8:t=fill",
        )

    dup1 = KEYWORD_DIR / "dup_1_搬运版.mp4"
    if not dup1.exists():
        # 搬运仿品①：裁掉 3% 边缘（模拟防裁切）+ 右上角水印块 + 重编码
        _encode_from(ffmpeg, dup1, base,
                     "crop=1216:684:32:18,scale=1280:720,"
                     "drawbox=x=1050:y=30:w=200:h=64:color=red@0.85:t=fill")

    dup2 = KEYWORD_DIR / "dup_2_二传版.mp4"
    if not dup2.exists():
        # 搬运仿品②：对比度/亮度微调 + 左下角标 + 重编码
        _encode_from(ffmpeg, dup2, base,
                     "eq=contrast=1.06:brightness=0.02,"
                     "drawbox=x=40:y=620:w=180:h=56:color=blue@0.85:t=fill")

    unrel1 = KEYWORD_DIR / "unrel_1_渐变.mp4"
    if not unrel1.exists():
        # 无关素材①：同为"多场景+推镜"的真实感结构，但场景内容与
        # 切镜节奏（8/16/12）都与原片不同
        _encode_multi(
            ffmpeg, unrel1,
            ["gradients=speed=0.03", "smptebars", "testsrc"],
            [8.0, 16.0, 12.0],
            "0.3*sin(2*PI*330*t)",
            "drawbox=x=80:y=80:w=160:h=80:color=purple@0.8:t=fill",
        )
    unrel2 = KEYWORD_DIR / "unrel_2_彩条.mp4"
    if not unrel2.exists():
        vf = "drawbox=x=0:y=0:w=1280:h=720:color=black@0.3:t=fill"
        _encode(ffmpeg, unrel2, vf, "smptebars", "0.2*sin(2*PI*110*t)")
    # 性能对照专用长素材（150s → 300 帧封顶，触发帧对抽样），不入关键词池
    perf = OUT / "perf_long.mp4"
    if not perf.exists():
        _encode(ffmpeg, perf, "format=yuv420p", "testsrc2",
                "0.2*sin(2*PI*150*t)", duration=PERF_DURATION)
    return {
        "base": base, "dup1": dup1, "dup2": dup2,
        "unrel1": unrel1, "unrel2": unrel2, "perf": perf,
    }


# ---------------------------------------------------------------- 装配
def build_stack():
    """与生产 context.py 相同的组件装配；全部落在沙箱目录。"""
    runner = FFmpegRunner()
    prober = ProbeService(runner)
    extractor = FrameExtractor(runner, prober)
    cfg = ConfigService()                      # 纯内存，不触用户配置
    WD.mkdir(parents=True, exist_ok=True)
    cfg.set("workdir", str(WD))
    wd_manager = WorkDirManager(cfg)
    wd_manager.set_workdir(WD)
    daos = make_daos(Database(OUT / "app.db"))
    archive = ArchiveService(wd_manager, cfg, daos.assets, daos.categories)
    provider = LocalProvider(ModelRegistry())  # runtime/models 真模型（含 CLIP）
    feats = FeatureExtractService(
        prober, extractor, provider,
        cache_dir=OUT / "feature_cache",       # 特征缓存也隔离
    )
    dedup = DedupPipeline(
        runner=runner, prober=prober, archive=archive, workdirs=wd_manager,
        registry=make_default_registry(),
        feature_extract=feats.extract,
        calculator=SimilarityCalculator(),
        reports=daos.reports,
        weights=(0.5, 0.25, 0.25),
        pool_limit=8,
    )
    scheme = SchemeManager(make_default_registry(), None)
    return runner, prober, extractor, feats, dedup, scheme


# ------------------------- 旧版特征提取（改动前行为逐帧复现） -------------------------
def old_style_extract(path: Path, prober: ProbeService,
                      extractor: FrameExtractor,
                      provider: LocalProvider) -> FeatureSet:
    """改动前实现：光流全帧对 + 640px 原分辨率（其余步骤与当前一致）。"""
    info = prober.probe(path)
    duration = max(info.duration_s, 1e-6)
    fps = SAMPLE_FPS
    if duration * SAMPLE_FPS > 300:
        fps = 300 / duration
    frames = list(extractor.stream_pairs_all(path, eff_fps=fps,
                                             max_frames=300))
    frames_ts = [f.ts for f in frames]
    frames_img = [f.img for f in frames]
    boundaries = SceneDetector().split(frames_ts, frames_img, duration)
    shots = SceneDetector.shots_of(boundaries, duration)
    composition, mids = CompositionEmbedder(provider).embed(
        frames_ts, frames_img, shots)
    triplets = [_pair_motion(frames_img[i - 1], frames_img[i])
                for i in range(1, len(frames_img))]   # 旧行为：全部对、原分辨率
    motion = np.clip(_resample(triplets, 32), -1.0, 1.0).astype(np.float32)
    rhythm_hist, cut_rate = RhythmAnalyzer().compute(boundaries, duration)
    return FeatureSet(
        composition=composition, shot_mid_ts=mids, motion_curve=motion,
        rhythm_hist=rhythm_hist, cut_rate_curve=cut_rate,
        duration_s=duration, version="1",
    )


def grab_frame(path: Path, ts: float, extractor: FrameExtractor) -> np.ndarray:
    return extractor.single(path, ts).img.astype(int)


def phash(path: Path, ts: float, extractor: FrameExtractor) -> np.ndarray:
    """64 位感知哈希（32x32 DCT 左上 8x8 → 中值阈值）——平台查重的常见口径。"""
    img = extractor.single(path, ts).img
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, (32, 32)).astype(np.float32)
    d = cv2.dct(small)[:8, :8]
    med = float(np.median(d[1:, 1:]))
    return (d > med).flatten()


def phash_distance(a: np.ndarray, b: np.ndarray) -> int:
    return int((a != b).sum())


def avg_phash_distance(p1: Path, ts1: list[float], p2: Path, ts2: list[float],
                       extractor: FrameExtractor) -> float:
    """多采样点平均汉明距离（0~64，越小越"被判重复"）。"""
    dists = [phash_distance(phash(p1, t1, extractor), phash(p2, t2, extractor))
             for t1, t2 in zip(ts1, ts2, strict=True)]
    return sum(dists) / len(dists)


# ---------------------------------------------------------------- 主流程
def main() -> int:
    if OUT.exists():
        shutil.rmtree(OUT)
    runner, prober, extractor, feats, dedup, scheme = build_stack()
    ffmpeg, _ffprobe = runner.locate_binaries()
    token = CancellationToken()
    media = build_media_set(ffmpeg)

    print("\n[0] 素材组（同关键词目录）")
    for name in ("base", "dup1", "dup2", "unrel1", "unrel2"):
        info = prober.probe(media[name])
        check(info.duration_s > DURATION - 1 and info.has_audio,
              f"{name}.mp4 生成有效", f"{info.width}x{info.height} "
              f"{info.duration_s:.1f}s 音轨={info.has_audio}")

    print(f"\n[1] 特征提取性能对照（{PERF_DURATION:.0f}s 长素材，"
          "300 帧封顶触发帧对抽样）")
    provider_of(feats)._registry.session("clip")   # 预热会话：两侧计时都不含加载
    t0 = time.perf_counter()
    feats.extract(media["perf"], token)
    t_new = time.perf_counter() - t0
    t0 = time.perf_counter()
    old_style_extract(media["perf"], prober, extractor, provider_of(feats))
    t_old = time.perf_counter() - t0
    check(t_new < t_old / 3, "当前实现显著快于旧版",
          f"旧版 {t_old:.1f}s → 当前 {t_new:.1f}s（{t_old / max(t_new, 1e-6):.1f}×）")

    print("\n[2] 打分真实性（未去重时两两相似度，特征含真 CLIP）")
    calc = SimilarityCalculator()
    w = (0.5, 0.25, 0.25)
    feat_base = feats.extract(media["base"], token)
    f_dup1 = feats.extract(media["dup1"], token)
    f_dup2 = feats.extract(media["dup2"], token)
    f_unrel1 = feats.extract(media["unrel1"], token)
    f_unrel2 = feats.extract(media["unrel2"], token)
    s_dup1 = calc.compare(feat_base, f_dup1, w).overall * 100
    s_dup2 = calc.compare(feat_base, f_dup2, w).overall * 100
    s_unrel1 = calc.compare(feat_base, f_unrel1, w).overall * 100
    s_unrel2 = calc.compare(feat_base, f_unrel2, w).overall * 100
    print(f"    base ↔ dup1={s_dup1:.1f}%  dup2={s_dup2:.1f}%  "
          f"unrel1={s_unrel1:.1f}%  unrel2={s_unrel2:.1f}%")
    # 注：unrel1 与 base 同构（同为三段拼接+同一推镜曲线，合成素材的极限），
    # 真实无关素材在运镜/构图上差异更大，分隔会更宽；此处验证排序正确性
    check(min(s_dup1, s_dup2) > max(s_unrel1, s_unrel2) + 5,
          "搬运仿品相似度高于无关视频（排序正确）",
          f"仿品 ≥{min(s_dup1, s_dup2):.1f}% vs 无关 ≤{max(s_unrel1, s_unrel2):.1f}%")

    print("\n[3] 去重 base.mp4（中度预设 seed=42：镜像+裁缩放+对比度）")
    params = scheme.instantiate("mid", seed=42)
    print("    手法链：" + "、".join(str(p["id"]) for p in params))
    progress: list[float] = []
    t0 = time.perf_counter()
    result = dedup.execute_item(media["base"], params,
                                progress.append, token)
    wall = time.perf_counter() - t0
    out = result.out_path
    check(out.exists() and "已去重" in str(out), "输出落在 已去重/ 镜像目录",
          str(out.relative_to(WD)))
    info = prober.probe(out)
    check(info.has_audio, "音轨保留")
    src_info = prober.probe(media["base"])
    check(abs(info.duration_s - src_info.duration_s) < 0.3, "时长保持"
          "（中度档无变速）", f"{info.duration_s:.2f}s")
    check(progress and abs(progress[-1] - 1.0) < 1e-6
          and any(0.75 < p < 0.95 for p in progress),
          "进度全程推进（转码段→对比段→1.0）",
          f"n={len(progress)} 末值={progress[-1]:.2f}")
    check(result.before_pct is not None and result.after_pct is not None
          and result.after_pct < result.before_pct,
          "中度档重复度下降（before→after）",
          f"{result.before_pct:.1f}% → {result.after_pct:.1f}%"
          f"（对 {len(list(KEYWORD_DIR.glob('*.mp4'))) - 1} 个同关键词对象）")
    print(f"    单条耗时 {wall:.1f}s（含转码+前后对比）")

    # 分项分数：定位降幅来自哪个特征分量（构图/运镜/节奏）
    dims_before = calc.compare(feat_base, f_dup1, w)
    feat_out_mid = feats.extract(out, token)
    dims_after = calc.compare(feat_out_mid, f_dup1, w)
    print(f"    与 dup1 分项：构图 {dims_before.composition:.2f}→"
          f"{dims_after.composition:.2f}  运镜 {dims_before.motion:.2f}→"
          f"{dims_after.motion:.2f}  节奏 {dims_before.rhythm:.2f}→"
          f"{dims_after.rhythm:.2f}")

    print("\n[4] 产物画面核对（按已知参数重建变换：居中裁切→缩放→镜像）")
    f_src = grab_frame(media["base"], 5.0, extractor).astype(np.float32)
    f_out = grab_frame(out, 5.0, extractor).astype(np.float32)
    margin = float(next(p["params"]["margin_pct"]        # type: ignore[index]
                        for p in params if p["id"] == "crop_scale"))
    h, w = f_src.shape[:2]
    ow = even_down(w * (1 - 2 * margin))     # 与 CropScaleTechnique 同口径
    oh = even_down(h * (1 - 2 * margin))
    x0, y0 = (w - ow) // 2, (h - oh) // 2
    cropped = f_src[y0:y0 + oh, x0:x0 + ow]
    aligned = cv2.resize(cropped, (w, h)).astype(np.float32)
    d_mirror = float(np.abs(f_out - aligned[:, ::-1]).mean())
    d_nomirror = float(np.abs(f_out - aligned).mean())
    check(d_mirror < d_nomirror * 0.6, "输出画面 = 裁缩放后水平镜像",
          f"镜像差 {d_mirror:.1f} vs 不镜像差 {d_nomirror:.1f}")

    print("\n[5] 复跑跳过（输出已存在 → SkippedSignal）")
    try:
        dedup.execute_item(media["base"], params, None, token)
        check(False, "应跳过而不重处理")
    except SkippedSignal as exc:
        check(True, "输出已存在 → 跳过", str(exc))

    print("\n[6] 重度预设对照（删中度产物后按重度重跑：镜像+裁缩放+饱和度"
          "+变速+黑边）")
    out.unlink()
    heavy = scheme.instantiate("heavy", seed=43)
    print("    手法链：" + "、".join(str(p["id"]) for p in heavy))
    result_h = dedup.execute_item(media["base"], heavy, None, token)
    assert result_h.before_pct is not None and result_h.after_pct is not None
    assert result.after_pct is not None
    print(f"    重度：{result_h.before_pct:.1f}% → {result_h.after_pct:.1f}%"
          f"（中度为 {result.after_pct:.1f}%）")
    # 分数方向契约：重度 ≤ 中度 < 处理前（降幅大小受特征构成影响，见汇总报告）
    check(result_h.after_pct <= result.after_pct < result.before_pct,
          "重复度单调下降（重度 ≤ 中度 < 处理前）",
          f"{result.before_pct:.1f}% → 中度 {result.after_pct:.1f}% → "
          f"重度 {result_h.after_pct:.1f}%")
    factor = float(next(p["params"]["factor"]           # type: ignore[index]
                        for p in heavy if p["id"] == "speed"))
    info_h = prober.probe(result_h.out_path)
    expect = src_info.duration_s / factor
    check(abs(info_h.duration_s - expect) < 0.3 and info_h.has_audio,
          "重度档变速与音轨正常", f"{info_h.duration_s:.2f}s ≈ {expect:.2f}s")

    print("\n[7] 平台级查重口径（64 位感知哈希 pHash，5 个采样点平均汉明距）")
    sample_ts = [5.0, 10.0, 15.0, 20.0, 25.0]
    out_ts = [t / factor for t in sample_ts]     # 变速后内容时刻对齐
    d_raw = avg_phash_distance(media["base"], sample_ts,
                               media["dup1"], sample_ts, extractor)
    d_dedup_base = avg_phash_distance(result_h.out_path, out_ts,
                                      media["base"], sample_ts, extractor)
    d_dedup_dup1 = avg_phash_distance(result_h.out_path, out_ts,
                                      media["dup1"], sample_ts, extractor)
    d_dedup_dup2 = avg_phash_distance(result_h.out_path, out_ts,
                                      media["dup2"], sample_ts, extractor)
    print(f"    原片↔搬运仿品 = {d_raw:.1f}（越小越会被判重）")
    print(f"    去重产物↔原片 = {d_dedup_base:.1f}  ↔仿品① = {d_dedup_dup1:.1f}"
          f"  ↔仿品② = {d_dedup_dup2:.1f}")
    check(d_raw <= 16, "原片与搬运仿品哈希接近（平台判重风险高）",
          f"距离 {d_raw:.1f}/64")
    check(min(d_dedup_base, d_dedup_dup1, d_dedup_dup2) > d_raw * 2,
          "去重产物与全部原内容哈希显著拉开（不易被判重）",
          f"最小 {min(d_dedup_base, d_dedup_dup1, d_dedup_dup2):.1f}"
          f" vs 去重前 {d_raw:.1f}（{min(d_dedup_base, d_dedup_dup1, d_dedup_dup2) / d_raw:.1f}×）")

    print("\n[8] 第二条：去重 unrel_1.mp4（低重复素材也应正常出片）")
    progress2: list[float] = []
    r2 = dedup.execute_item(media["unrel1"], params, progress2.append, token)
    check(r2.out_path.exists(), "无关素材去重产物生成", r2.out_path.name)
    if r2.before_pct is not None and r2.after_pct is not None:
        print(f"    unrel_1 重复度 {r2.before_pct:.1f}% → {r2.after_pct:.1f}%")
        check(r2.after_pct < r2.before_pct, "无关素材重复度同样下降",
              f"{r2.before_pct:.1f}% → {r2.after_pct:.1f}%")

    print("\n[9] 批次产物清点")
    outs = sorted((WD / "已去重").rglob("*_deduped.mp4"))
    check(len(outs) == 2, "已去重/ 下产出 2 条", "、".join(p.name for p in outs))

    print("\n========== 汇总 ==========")
    failed = [c for c in _checks if not c[0]]
    print(f"通过 {len(_checks) - len(failed)}/{len(_checks)}"
          + (f"，失败 {len(failed)} 项" if failed else "，全部通过"))
    for _ok, name, detail in failed:
        print(f"  FAIL: {name} {detail}")
    return 1 if failed else 0


def provider_of(feats: FeatureExtractService) -> LocalProvider:
    # 验证脚本直取装配件（私有访问仅为复用同一 provider 实例）
    return feats._embedder._provider


if __name__ == "__main__":
    sys.exit(main())
