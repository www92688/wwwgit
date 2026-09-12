# 真实素材端到端验证（单测覆盖不了真实媒体的表现）
# 用法：python scripts/e2e_real_media.py
# 产物目录：build/e2e_real_media/（含生成素材与全部输出，可人工复核）
#
# 覆盖面：
#   A. 预处理·框选去水印+去字幕（帧级路径，无模型 → TELEA 经典降级）
#   B. 预处理·框选裁剪 + 9:16 比例（纯滤镜路径）
#   C. 去重全手法链（镜像/裁切缩放/LUT调色/变速/黑边）
#   音画同步：素材内置「白闪+同刻哔声」对表，输出端重测两路事件时刻求漂移。
# 任一断言失败 → 退出码 1。
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np

from ych.common.schemas import BBox, ManualRegions
from ych.core.m2_preprocess.filter_only_processor import FilterOnlyProcessor
from ych.core.m2_preprocess.frame_level_processor import FrameLevelProcessor
from ych.core.m2_preprocess.ops import PreprocessOps
from ych.core.m2_preprocess.preprocess_pipeline import PreprocessPipeline
from ych.core.m2_preprocess.subtitle_handler import SubtitleHandler
from ych.core.m3_dedup.dedup_pipeline import DedupPipeline
from ych.core.m3_dedup.techniques.color_filter import ColorFilterTechnique
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
OUT = ROOT / "build" / "e2e_real_media"

DURATION = 12.0
FPS = 30.0
FLASH_TS = (2.0, 4.0, 6.0, 8.0, 10.0)     # 白闪/哔声中心
FLASH_HALF_S = 0.03                        # 闪宽 ±0.03s（≈2 帧）
BEEP_HALF_S = 0.06                         # 哔宽 ±0.06s（silencedetect 更稳）
SYNC_TOL_S = 0.12                          # AAC priming + 帧量化余量

WM_BOX = (960, 40, 256, 72)                # 水印黄块（px）
SUB_BOX = (384, 628, 512, 52)              # 字幕白条（px）

_checks: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    _checks.append((ok, name, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"（{detail}）" if detail else ""))


# ---------------------------------------------------------------- 素材生成
def build_source(dest: Path, ffmpeg: Path) -> None:
    """真实素材：动态测试卡 + 烧录黄块水印/白条字幕 + 同刻白闪与哔声。"""
    fx, fy, fw, fh = WM_BOX
    sx, sy, sw, sh = SUB_BOX
    vf = [
        f"drawbox=x={fx}:y={fy}:w={fw}:h={fh}:color=yellow@1:t=fill",
        f"drawbox=x={sx}:y={sy}:w={sw}:h={sh}:color=white@1:t=fill",
    ]
    for t in FLASH_TS:
        vf.append(
            f"drawbox=x=0:y=0:w=1280:h=720:color=white@1:t=fill"
            f":enable='between(t,{t - FLASH_HALF_S},{t + FLASH_HALF_S})'"
        )
    beep = "+".join(
        f"0.55*sin(2*PI*880*t)*between(t,{t - BEEP_HALF_S:.2f},{t + BEEP_HALF_S:.2f})"
        for t in FLASH_TS
    )
    subprocess.run([
        str(ffmpeg), "-y",
        "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=12",
        # 表达式含逗号，需按滤镜图语法单引号包裹
        "-f", "lavfi", "-i", f"aevalsrc='{beep}':s=44100:d=12",
        "-vf", ",".join(vf),
        "-map", "0:v", "-map", "1:a", "-ac", "2",
        "-c:v", "libx264", "-crf", "18", "-preset", "medium",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
        str(dest),
    ], check=True, capture_output=True)


# ---------------------------------------------------------------- 测量工具
def frame_luma_series(path: Path, ffmpeg: Path) -> np.ndarray:
    """逐帧平均亮度（64x36 灰度粗采样，测白闪足够）。"""
    proc = subprocess.Popen(
        [str(ffmpeg), "-i", str(path), "-vf", "scale=64:36,format=gray",
         "-f", "rawvideo", "pipe:1"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    frame_bytes = 64 * 36
    means = []
    assert proc.stdout is not None
    while chunk := proc.stdout.read(frame_bytes):
        means.append(float(np.frombuffer(chunk, np.uint8).mean()))
    proc.wait()
    return np.asarray(means)


def flash_times(path: Path, ffmpeg: Path) -> list[float]:
    """白闪事件中心时刻（相邻高亮帧聚类取中点）。"""
    series = frame_luma_series(path, ffmpeg)
    baseline = float(np.median(series))
    hot = np.where(series > baseline + 40)[0]
    groups: list[list[int]] = []
    for i in hot:
        if groups and i - groups[-1][-1] <= 2:
            groups[-1].append(int(i))
        else:
            groups.append([int(i)])
    return [(g[0] + g[-1]) / 2 / FPS for g in groups]


def beep_times(path: Path, ffmpeg: Path, duration: float) -> list[float]:
    """哔声事件中心时刻（silencedetect 的静默补集）。"""
    proc = subprocess.run(
        [str(ffmpeg), "-i", str(path), "-af", "silencedetect=noise=-45dB:d=0.15",
         "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    silences: list[tuple[float, float | None]] = []
    start: float | None = None
    for line in proc.stderr.splitlines():
        if "silence_start:" in line:
            start = float(line.split("silence_start:")[1].split()[0])
        elif "silence_end:" in line and start is not None:
            silences.append((start,
                             float(line.split("silence_end:")[1].split()[0])))
            start = None
    if start is not None:
        silences.append((start, None))
    sound: list[tuple[float, float]] = []
    prev_end = 0.0
    for s0, s1 in silences:
        if s0 - prev_end > 0.05:
            sound.append((prev_end, s0))
        prev_end = s1 if s1 is not None else duration
    if duration - prev_end > 0.05:
        sound.append((prev_end, duration))
    return [(a + b) / 2 for a, b in sound]


def sync_drift(path: Path, ffmpeg: Path, duration: float) -> tuple[float, int]:
    """输出端白闪/哔声逐对求漂移：返回最大漂移与配对数。"""
    flashes = flash_times(path, ffmpeg)
    beeps = beep_times(path, ffmpeg, duration)
    n = min(len(flashes), len(beeps))
    assert n == len(FLASH_TS), f"事件数不符：flash={len(flashes)} beep={len(beeps)}"
    drifts = [abs(f - b) for f, b in zip(flashes[:n], beeps[:n], strict=False)]
    return max(drifts), n


def grab(path: Path, ts: float, extractor: FrameExtractor) -> np.ndarray:
    return extractor.single(path, ts).img


# ---------------------------------------------------------------- 装配
def build_stack(tmp: Path):
    runner = FFmpegRunner()
    ffmpeg, _ffprobe = runner.locate_binaries()
    prober = ProbeService(runner)
    extractor = FrameExtractor(runner, prober)
    cfg = ConfigService()
    wd = tmp / "workdir"
    wd.mkdir(parents=True, exist_ok=True)
    cfg.set("workdir", str(wd))
    wd_manager = WorkDirManager(cfg)
    wd_manager.set_workdir(wd)
    daos = make_daos(Database(tmp / "app.db"))
    archive = ArchiveService(wd_manager, cfg, daos.assets, daos.categories)
    provider = LocalProvider(ModelRegistry())   # 无模型 → 真实降级路径
    pipeline = PreprocessPipeline(
        prober, FilterOnlyProcessor(runner),
        SubtitleHandler(runner, extractor, provider),
        FrameLevelProcessor(runner, extractor, provider, cfg), archive,
    )
    dedup = DedupPipeline(
        runner=runner, prober=prober, archive=archive, workdirs=wd_manager,
        registry=make_default_registry(),
    )
    return ffmpeg, prober, extractor, pipeline, dedup


# ---------------------------------------------------------------- 用例
def case_a_frame_path(src: Path, prober, extractor, pipeline, ffmpeg: Path) -> Path:
    print("\n[A] 预处理·框选去水印 + 框选去字幕（帧级路径，TELEA 降级）")
    ops = PreprocessOps(
        remove_watermark_mode="manual",
        watermark_regions=ManualRegions(rects=[
            BBox(0.74, 0.045, 0.22, 0.11),          # 覆盖黄块
        ]),
        remove_subtitle_mode="manual",
        subtitle_regions=ManualRegions(rects=[
            BBox(0.28, 0.855, 0.44, 0.11),          # 覆盖白条
        ]),
    )
    out = pipeline.execute_item(src, ops, None, None)
    info = prober.probe(out)
    src_info = prober.probe(src)

    check(abs(info.duration_s - src_info.duration_s) < 0.2,
          "时长保持", f"{info.duration_s:.2f}s vs {src_info.duration_s:.2f}s")
    check(info.has_audio, "音轨保留")
    n_out = len(frame_luma_series(out, ffmpeg))
    check(abs(n_out - int(DURATION * FPS)) <= 2,
          "帧数不丢帧", f"输出 {n_out} 帧（期望≈{int(DURATION * FPS)}）")

    f_src = grab(src, 1.0, extractor)
    f_out = grab(out, 1.0, extractor)
    h, w = f_src.shape[:2]

    def frac_yellow(img, x0, y0, x1, y1):
        r = img[int(y0*h):int(y1*h), int(x0*w):int(x1*w)].astype(int)
        return float(((r[..., 2] > 180) & (r[..., 1] > 180)
                      & (r[..., 0] < 100)).mean())

    def frac_white(img, x0, y0, x1, y1):
        r = img[int(y0*h):int(y1*h), int(x0*w):int(x1*w)].astype(int)
        return float(((r > 200).all(axis=2)).mean())

    def mean_abs_diff(x0, y0, x1, y1):
        a = f_src[int(y0*h):int(y1*h), int(x0*w):int(x1*w)].astype(int)
        b = f_out[int(y0*h):int(y1*h), int(x0*w):int(x1*w)].astype(int)
        return float(np.abs(a - b).mean())

    wm_src, wm_out = frac_yellow(f_src, 0.75, 0.055, 0.95, 0.155), \
        frac_yellow(f_out, 0.75, 0.055, 0.95, 0.155)
    check(wm_src > 0.8 and wm_out < 0.2, "水印黄块被清除",
          f"黄块占比 {wm_src:.2f} → {wm_out:.2f}")
    sub_src, sub_out = frac_white(f_src, 0.35, 0.88, 0.65, 0.93), \
        frac_white(f_out, 0.35, 0.88, 0.65, 0.93)
    check(sub_src > 0.8 and sub_out < 0.2, "字幕白条被清除",
          f"白条占比 {sub_src:.2f} → {sub_out:.2f}")
    ctrl = mean_abs_diff(0.40, 0.40, 0.60, 0.60)
    check(ctrl < 12, "无关区域基本不动", f"中心区平均差 {ctrl:.1f}")
    check(mean_abs_diff(0.75, 0.055, 0.95, 0.155) > 25,
          "水印区像素确实被改写")

    drift, n = sync_drift(out, ffmpeg, info.duration_s)
    check(drift <= SYNC_TOL_S, "音画同步（帧级转码后）",
          f"最大漂移 {drift*1000:.0f}ms / {n} 对")
    return out


def case_b_filter_path(src: Path, prober, pipeline, ffmpeg: Path) -> Path:
    print("\n[B] 预处理·框选裁剪 + 9:16（纯滤镜路径）")
    ops = PreprocessOps(
        crop_rect=BBox(0.30, 0.10, 0.40, 0.80),
        aspect_target=(9, 16),
        aspect_strategy="crop",
    )
    out = pipeline.execute_item(src, ops, None, None)
    info = prober.probe(out)
    ratio = info.width / info.height
    check(abs(ratio - 9 / 16) < 0.01, "输出 9:16", f"{info.width}x{info.height}")
    check(info.has_audio, "音轨保留")
    src_info = prober.probe(src)
    check(abs(info.duration_s - src_info.duration_s) < 0.2, "时长保持",
          f"{info.duration_s:.2f}s")
    drift, n = sync_drift(out, ffmpeg, info.duration_s)
    check(drift <= SYNC_TOL_S, "音画同步（裁剪转码后）",
          f"最大漂移 {drift*1000:.0f}ms / {n} 对")
    return out


def case_c_dedup(src: Path, prober, extractor, dedup: DedupPipeline, ffmpeg: Path) -> Path:
    print("\n[C] 去重全手法链（镜像+裁缩放+LUT调色+1.25x变速+黑边）")
    # LUT 预设走真实 .cube 而非退化路径
    from ych.core.m3_dedup.techniques.base import ClipContext

    ctx = ColorFilterTechnique().apply(
        ClipContext(), {"preset": "warm"})
    check(any("lut3d=file=" in f for f in ctx.vf_filters),
          "调色预设命中 runtime/luts（lut3d）",
          str(ctx.vf_filters))

    params = [
        {"id": "mirror", "params": {}},
        {"id": "crop_scale", "params": {}},
        {"id": "color_filter", "params": {"preset": "warm"}},
        {"id": "speed", "params": {"factor": 1.25}},
        {"id": "border", "params": {}},
    ]
    result = dedup.execute_item(src, params, None, None)
    out = result.out_path
    check(out.exists() and "已去重" in str(out), "输出落在 已去重/", out.name)
    info = prober.probe(out)
    src_info = prober.probe(src)
    expect_dur = src_info.duration_s / 1.25
    check(abs(info.duration_s - expect_dur) < 0.25, "时长≈1/1.25 变速",
          f"{info.duration_s:.2f}s（期望≈{expect_dur:.2f}s）")
    check(info.has_audio, "音轨保留（atempo 变速）")

    drift, n = sync_drift(out, ffmpeg, info.duration_s)
    check(drift <= SYNC_TOL_S, "音画同步（变速后两路同比缩放）",
          f"最大漂移 {drift*1000:.0f}ms / {n} 对")
    # 变速比核对：输出白闪间距 / 原素材白闪间距 ≈ 1/1.25
    flashes_out = flash_times(out, ffmpeg)
    flashes_src = flash_times(src, ffmpeg)
    ratio = ((flashes_out[-1] - flashes_out[0])
             / (flashes_src[-1] - flashes_src[0]))
    check(abs(ratio - 1 / 1.25) < 0.05, "视频事件间距 = 1/1.25", f"{ratio:.3f}")

    # 暖调生效：全帧 R-B 均差应变大（黑边稀释后仍应可观）
    f_src = grab(src, 3.0, extractor).astype(int)
    f_out = grab(out, 3.0 / 1.25, extractor).astype(int)
    shift = (f_out[..., 2] - f_out[..., 0]).mean() \
        - (f_src[..., 2] - f_src[..., 0]).mean()
    check(shift > 1.5, "LUT 暖调整体生效", f"R-B 均差 {shift:+.1f}")
    return out


def main() -> int:
    import shutil

    if OUT.exists():
        shutil.rmtree(OUT)      # 幂等重跑：清掉上次的 _cleaned/已去重 产物
    OUT.mkdir(parents=True, exist_ok=True)
    ffmpeg, prober, extractor, pipeline, dedup = build_stack(OUT)
    # 源放工作目录内（去重镜像输出要求素材在工作目录下，同真实用法）
    src_dir = OUT / "workdir" / "素材"
    src_dir.mkdir(parents=True, exist_ok=True)
    src = src_dir / "src.mp4"
    print(f"生成真实素材 → {src}")
    build_source(src, ffmpeg)

    case_a_frame_path(src, prober, extractor, pipeline, ffmpeg)
    # 各用例独立源副本：_cleaned 输出与源同目录同名，避免互相判为已存在
    src_b = src_dir / "src_b.mp4"
    src_b.write_bytes(src.read_bytes())
    case_b_filter_path(src_b, prober, pipeline, ffmpeg)
    case_c_dedup(src, prober, extractor, dedup, ffmpeg)

    failed = [c for c in _checks if not c[0]]
    print(f"\n===== 端到端验证：{len(_checks) - len(failed)}/{len(_checks)} 通过 =====")
    for _ok, name, detail in failed:
        print(f"  FAIL {name}（{detail}）")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
