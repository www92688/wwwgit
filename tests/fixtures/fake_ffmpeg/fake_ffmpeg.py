# 假 ffmpeg：单元测试替身，输出固定 rawvideo / 支持脚本化失败
# 用法（.bat 包装后与真实 ffmpeg 同名调用）：
#   fake_ffmpeg -y -f rawvideo -s 64x48 -r 10 -i pipe:0 out.mp4
#   环境变量 FAKE_FFMPEG_FAIL=1 → 立即 exit 1（模拟转码失败）
#   环境变量 FAKE_FFMPEG_FRAMES=N → pipe 模式输出帧数（默认 10）
from __future__ import annotations

import os
import sys
from pathlib import Path


def _parse_size(args: list[str]) -> tuple[int, int]:
    """从参数中解析 -s WxH。"""
    for i, a in enumerate(args):
        if a == "-s" and i + 1 < len(args):
            w, _, h = args[i + 1].partition("x")
            try:
                return int(w), int(h)
            except ValueError:
                break
    return 64, 48


def main(argv: list[str]) -> int:
    if os.environ.get("FAKE_FFMPEG_FAIL") == "1":
        print("fake ffmpeg: scripted failure", file=sys.stderr)
        return 1

    args = list(argv)
    is_rawvideo_pipe = (
        "-f" in args and "rawvideo" in args and any(a.startswith("pipe:") for a in args)
    )

    # 输出文件：最后一个不以 - 开头且非选项值的位置参数（简化处理：取末尾参数）
    out_path: Path | None = None
    if not is_rawvideo_pipe:
        filtered: list[str] = []
        skip_next = False
        for a in args:
            if skip_next:
                skip_next = False
                continue
            if a == "-i":
                skip_next = True
                continue
            if not a.startswith("-"):
                filtered.append(a)
        if filtered:
            out_path = Path(filtered[-1])

    if is_rawvideo_pipe:
        w, h = _parse_size(args)
        frames = int(os.environ.get("FAKE_FFMPEG_FRAMES", "10"))
        buf = bytes([0, 0, 255]) * (w * h)  # BGR 纯红帧
        sys.stdout.buffer.write(buf * frames)
        sys.stdout.buffer.flush()
        return 0

    if out_path is not None:
        # 普通转码模式：创建空输出文件即视为成功
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
