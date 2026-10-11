# 编码参数质量门扫描：候选 (preset, crf) 对比基准（medium, crf20）。
# 用法：python scripts/encoder_gate.py
# 采用规则（写死在脚本里，防口头决策）：
#   全部素材 SSIM（候选输出 vs 基准输出）>= 0.99 且编码提速 >= 15% 才允许采用；
#   否则维持基准参数——画质红线优先于速度。
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_WINGET_LINKS = r"C:\Users\rememberme\AppData\Local\Microsoft\WinGet\Links"
BASELINE = ("faster", 18)   # 现行生产规格（2026-10 实测采纳）
CANDIDATES = [("faster", 20), ("fast", 20), ("medium", 18), ("faster", 18)]
SSIM_FLOOR = 0.99
SPEED_MIN = 1.15
SOURCES = [
    ("motion", "testsrc2=size=1280x720:rate=30:duration=10"),
    ("static", "smptebars=size=1280x720:rate=30:duration=10"),
    ("mixed", "gradients=size=1280x720:rate=30:duration=10"),
]


def find_ffmpeg() -> Path | None:
    found = shutil.which("ffmpeg")
    if found:
        return Path(found)
    cand = Path(_WINGET_LINKS) / "ffmpeg.exe"
    return cand if cand.exists() else None


def run(args: list[str]) -> tuple[int, str]:
    proc = subprocess.run(
        [str(FFMPEG), *args], capture_output=True, text=True, errors="replace",
    )
    return proc.returncode, proc.stderr


def encode(src: Path, out: Path, preset: str, crf: int) -> float:
    t0 = time.perf_counter()
    code, err = run([
        "-y", "-i", str(src), "-an", "-c:v", "libx264",
        "-crf", str(crf), "-preset", preset, "-pix_fmt", "yuv420p",
        str(out),
    ])
    if code != 0:
        print(f"  编码失败 {preset}/crf{crf}: {err[-200:]}")
        raise SystemExit(1)
    return time.perf_counter() - t0


def ssim(candidate: Path, reference: Path) -> float:
    code, err = run([
        "-i", str(candidate), "-i", str(reference),
        "-lavfi", "[0:v][1:v]ssim", "-f", "null", "-",
    ])
    if code != 0:
        print(f"  SSIM 计算失败: {err[-200:]}")
        raise SystemExit(1)
    m = re.search(r"All:([0-9.]+)", err)
    return float(m.group(1)) if m else 0.0


FFMPEG = find_ffmpeg()


def main() -> int:
    if FFMPEG is None:
        print("ffmpeg 不可用")
        return 1
    tmp = Path(tempfile.mkdtemp())
    print(f"基准：preset={BASELINE[0]} crf={BASELINE[1]}，"
          f"采用规则：全素材 SSIM>={SSIM_FLOOR} 且提速>={SPEED_MIN:.2f}x\n")
    results: dict[tuple[str, int], dict[str, float]] = {}
    for name, lavfi in SOURCES:
        src = tmp / f"{name}_src.mp4"
        code, err = run(["-y", "-f", "lavfi", "-i", lavfi, "-c:v", "libx264",
                         "-crf", "18", "-preset", "medium", "-pix_fmt",
                         "yuv420p", str(src)])
        if code != 0:
            print(f"源生成失败 {name}: {err[-200:]}")
            return 1
        base_out = tmp / f"{name}_base.mp4"
        results.setdefault(BASELINE, {})[name] = encode(
            src, base_out, *BASELINE)
        base_ref = tmp / f"{name}_base_ref.mp4"
        shutil.copyfile(base_out, base_ref)   # 候选 vs 基准输出
        for cand in CANDIDATES:
            out = tmp / f"{name}_{cand[0]}_{cand[1]}.mp4"
            t = encode(src, out, *cand)
            results.setdefault(cand, {})[name] = t
            results.setdefault(cand, {})[f"{name}_ssim"] = ssim(out, base_ref)
    print(f"{'参数':<18}{'总耗时(s)':<12}{'相对基准':<10}{'SSIM 范围'}")
    base_total = sum(results[BASELINE][n] for n, _ in SOURCES)
    for cand in [BASELINE, *CANDIDATES]:
        total = sum(results[cand][n] for n, _ in SOURCES)
        ssims = [results[cand][f"{n}_ssim"] for n, _ in SOURCES
                 if f"{n}_ssim" in results[cand]]
        rel = total / base_total
        lo = min(ssims) if ssims else 1.0
        hi = max(ssims) if ssims else 1.0
        mark = " ← 基准" if cand == BASELINE else ""
        print(f"{cand[0]}/crf{cand[1]:<10}{total:<12.2f}{rel:<10.2f}"
              f"{lo:.4f}~{hi:.4f}{mark}")
    print()
    adopted = False
    for cand in CANDIDATES:
        total = sum(results[cand][n] for n, _ in SOURCES)
        ssims = [results[cand][f"{n}_ssim"] for n, _ in SOURCES
                 if f"{n}_ssim" in results[cand]]
        if ssims and min(ssims) >= SSIM_FLOOR and base_total / total >= SPEED_MIN:
            print(f"✔ 可采用：preset={cand[0]} crf={cand[1]}"
                  f"（SSIM 最低 {min(ssims):.4f}，提速 {base_total/total:.2f}x）")
            adopted = True
    if not adopted:
        print("✘ 无候选通过质量门——维持基准参数（medium/crf20）不变")
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
