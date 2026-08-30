# 假 ffprobe：单元测试替身，输出与真实 ffprobe 结构一致的固定 JSON
# 样例：64x48 h264 视频 + aac 音轨 + mov_text 软字幕轨，时长约 1s
from __future__ import annotations

import json
import sys

FIXTURE_JSON = {
    "streams": [
        {
            "index": 0,
            "codec_name": "h264",
            "codec_type": "video",
            "width": 64,
            "height": 48,
            "r_frame_rate": "10/1",
            "avg_frame_rate": "10/1",
            "duration": "1.000000",
            "nb_frames": "10",
            "pix_fmt": "yuv420p",
        },
        {
            "index": 1,
            "codec_name": "aac",
            "codec_type": "audio",
            "sample_rate": "44100",
            "channels": 2,
            "duration": "1.041667",
        },
        {
            "index": 2,
            "codec_name": "mov_text",
            "codec_type": "subtitle",
            "duration": "1.000000",
        },
    ],
    "format": {
        "filename": "fixture_input.mp4",
        "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
        "duration": "1.041667",
        "size": "12345",
        "nb_streams": 3,
    },
}


def main(argv: list[str]) -> int:
    # 环境变量 FAKE_FFPROBE_FAIL=1 → exit 1（模拟解析失败）
    import os

    # 真实 ffprobe 恒定输出 UTF-8（与控制台代码页无关），替身必须对齐：
    # 否则中文区域下子进程按 GBK 编码输出，父进程 utf-8 解码必失败，回归失真
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if os.environ.get("FAKE_FFPROBE_FAIL") == "1":
        print("fake ffprobe: scripted failure", file=sys.stderr)
        return 1
    data = dict(FIXTURE_JSON)
    data["format"] = dict(FIXTURE_JSON["format"])
    # 回显输入路径（真实 ffprobe 行为）：format.filename = 最后一个非选项参数，
    # 供 S1 中文路径缺陷回归测试断言解码往返无损
    inputs = [a for a in argv if not a.startswith("-")]
    data["format"]["filename"] = inputs[-1] if inputs else "fixture_input.mp4"
    json.dump(data, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
