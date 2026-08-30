# 小样本媒体生成脚本（≤3s，供集成测试使用；产物不进版本库）
# 用法：python make_media.py <输出目录>
# 产物：
#   solid.mp4      纯色 1s（lavfi color）
#   checker.mp4    测试卡 1.5s（smptebars）
#   with_subs.mkv  带 mov_text 软字幕轨 1s
# ffmpeg 定位：PATH → winget 固定路径；找不到时打印提示并正常退出。
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

_WINGET_LINKS = r"C:\Users\rememberme\AppData\Local\Microsoft\WinGet\Links"


def find_ffmpeg() -> Path | None:
    found = shutil.which("ffmpeg")
    if found:
        return Path(found)
    cand = Path(_WINGET_LINKS) / "ffmpeg.exe"
    return cand if cand.exists() else None


JOBS = [
    # (文件名, lavfi 源, 输出参数)
    ("solid.mp4", "color=c=red:size=64x48:rate=10:duration=1",
     ["-c:v", "libx264", "-pix_fmt", "yuv420p"]),
    ("checker.mp4", "smptebars=size=64x48:rate=10:duration=1.5",
     ["-c:v", "libx264", "-pix_fmt", "yuv420p"]),
]


def main(out_dir: str) -> int:
    ffmpeg = find_ffmpeg()
    if ffmpeg is None:
        print("ffmpeg not available —— 跳过样本生成（集成测试将自动 skip）")
        return 0
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    for name, source, extra in JOBS:
        dest = out / name
        subprocess.run(
            [str(ffmpeg), "-y", "-f", "lavfi", "-i", source, *extra, str(dest)],
            check=True,
            capture_output=True,
        )
        print(f"generated: {dest}")

    # 带软字幕轨的 mkv：先出纯色视频，再挂 mov_text 字幕轨
    base = out / "with_subs_base.mp4"
    subprocess.run(
        [str(ffmpeg), "-y", "-f", "lavfi", "-i",
         "color=c=blue:size=64x48:rate=10:duration=1",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(base)],
        check=True, capture_output=True,
    )
    srt = out / "subs.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n测试字幕\n",
        encoding="utf-8",
    )
    dest = out / "with_subs.mkv"
    subprocess.run(
        [str(ffmpeg), "-y", "-i", str(base), "-i", str(srt),
         "-map", "0:v", "-map", "0:a?", "-map", "1:0",
         "-c:v", "copy", "-c:a", "copy", "-c:s", "srt",
         str(dest)],
        check=True, capture_output=True,
    )
    base.unlink(missing_ok=True)
    srt.unlink(missing_ok=True)
    print(f"generated: {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "."))

