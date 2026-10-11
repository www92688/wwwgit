# 硬件编码器质量门扫描：候选（nvenc/qsv）对比基准（libx264 crf18 faster）。
# 采用规则（与 scripts/encoder_gate.py 同口径，防口头决策）：
#   SSIM（候选输出 vs 基准输出）>= 0.99 且 提速 >= 1.5x 才允许采用；
#   否则维持 libx264——画质红线优先于速度。
#
# 2026-10-11 本机（RTX 4060 Laptop + Intel UHD）实测结论：
#   - h264_nvenc：会话初始化即失败（远程桌面/虚拟显示会话禁用 NVENC）；
#   - h264_qsv：可用的调用形态是「-init_hw_device qsv=hw + 系统内存帧直喂」
#     （oneVPL 自动走 D3D11VA 子设备）；hwupload 滤镜路径必须再挂
#     -filter_hw_device，且实测仍 EINVAL；720p 下提速仅 0.96~1.04x
#     （SSIM 0.9967 达标），未过 1.5x 门 → 不采用；
#   - 维持 libx264 crf18 faster。换机器 / 1080p+ 素材 / 驱动更新后重跑本
#     脚本复核即可，通过门槛会打印「✓ 达标」。
# 用法：python scripts/hw_encoder_gate.py（需先跑 scripts/dedup_verify.py 生成素材）
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ych.services.s1_media.ffmpeg_runner import FFmpegRunner  # noqa: E402

RUNNER = FFmpegRunner()
FFMPEG, _ = RUNNER.locate_binaries()
SRC = (ROOT / "build" / "dedup_verify" / "workdir" / "生活好物" / "收纳好物"
       / "2026-10-07" / "base.mp4")
FILTERS = "hflip,crop=1024:576:128:72,scale=1280:720,eq=contrast=1.08"  # 中度档同型滤镜链
SSIM_FLOOR = 0.99
SPEED_MIN = 1.5

CANDIDATES: list[tuple[str, list[str]]] = [
    ("libx264 crf18 faster（基准）", [
        "-c:v", "libx264", "-crf", "18", "-preset", "faster", "-pix_fmt", "yuv420p",
    ]),
    ("nvenc p6 cq19 vbr", [
        "-c:v", "h264_nvenc", "-preset", "p6", "-tune", "hq",
        "-rc", "vbr", "-cq", "19", "-b:v", "0", "-pix_fmt", "yuv420p",
    ]),
    # QSV 可用形态：系统内存帧直喂（oneVPL/D3D11VA 子设备自动建立），
    # 不走 hwupload 滤镜（实测 EINVAL）
    ("qsv global_quality 19", [
        "-init_hw_device", "qsv=hw",
        "-c:v", "h264_qsv", "-global_quality", "19", "-pix_fmt", "nv12",
    ]),
    ("qsv global_quality 21", [
        "-init_hw_device", "qsv=hw",
        "-c:v", "h264_qsv", "-global_quality", "21", "-pix_fmt", "nv12",
    ]),
]


def encode(name: str, out: Path, enc_args: list[str]) -> float:
    cmd = [str(FFMPEG), "-y", "-i", str(SRC), "-vf", FILTERS,
           "-an", *enc_args, str(out)]
    t0 = time.perf_counter()
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          errors="replace", creationflags=0x08000000)
    dt = time.perf_counter() - t0
    if proc.returncode != 0:
        print(f"  [{name}] 编码失败: {proc.stderr[-300:]}")
        raise SystemExit(1)
    return dt


def ssim(a: Path, b: Path) -> tuple[float, float]:
    """SSIM 与时长对齐检查（变速不存在，两输出应同长）。"""
    proc = subprocess.run(
        [str(FFMPEG), "-i", str(a), "-i", str(b),
         "-lavfi", "[0:v][1:v]ssim", "-f", "null", "-"],
        capture_output=True, text=True, errors="replace",
        creationflags=0x08000000,
    )
    m = re.search(r"SSIM.*All:([\d.]+)", proc.stderr)
    return (float(m.group(1)) if m else 0.0, proc.returncode)


def size_of(p: Path) -> int:
    return p.stat().st_size


def main() -> int:
    if not SRC.exists():
        print("缺少测试素材，请先跑 scripts/dedup_verify.py")
        return 1
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        print(f"素材：{SRC.name}（36s 720p，中度档滤镜链）\n")
        results: list[tuple[str, float, float, int]] = []
        base_out = tmp / "base.mp4"
        base_t = encode("基准", base_out, CANDIDATES[0][1])
        results.append((CANDIDATES[0][0], base_t, 1.0, size_of(base_out)))
        print(f"  [基准] {CANDIDATES[0][0]}: {base_t:.2f}s "
              f"({size_of(base_out) // 1024}KB)")
        for name, args in CANDIDATES[1:]:
            out = tmp / f"{abs(hash(name))}.mp4"
            try:
                dt = encode(name, out, args)
            except SystemExit:
                results.append((name, -1, 0.0, 0))
                continue
            s, _rc = ssim(out, base_out)
            speedup = base_t / dt
            ok = s >= SSIM_FLOOR and speedup >= SPEED_MIN
            results.append((name, dt, s, size_of(out)))
            print(f"  [{name}] {dt:.2f}s  SSIM={s:.4f}  "
                  f"提速={speedup:.2f}x  大小={size_of(out) // 1024}KB  "
                  f"{'✓ 采用' if ok else '✗ 不达标'}")
    return 0


if __name__ == "__main__":
    SystemExit(main())
