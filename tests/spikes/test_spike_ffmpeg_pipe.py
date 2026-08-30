# 冒烟①：ffmpeg 解码 → Python 逐帧 → 编码 双子进程管道模式可行
# 验证详设 8.4 run_pipe 机制在本机成立（D1 替身冒烟，对应 SP-1 的工程前提）
import subprocess

FFMPEG_PIPE_SMOKE = "冒烟① ffmpeg 解码→逐帧→编码管道"


def test_ffmpeg_decode_frames_encode_pipe(
    tmp_path, ffmpeg_bin, ffprobe_bin
):
    w, h, fps, sec = 64, 48, 10, 1
    frame_bytes = w * h * 3

    # 1. 合成输入：lavfi 纯色源生成 1 秒视频（非破坏性，全部在 tmp 目录）
    src = tmp_path / "src.mp4"
    gen = [
        str(ffmpeg_bin), "-y", "-f", "lavfi", "-i",
        f"color=c=red:size={w}x{h}:rate={fps}:duration={sec}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src),
    ]
    subprocess.run(gen, check=True, capture_output=True)

    # 2. 解码进程：rawvideo 到 stdout（T-6：stderr 持续丢弃防管道塞满）
    dec = subprocess.Popen(
        [
            str(ffmpeg_bin), "-i", str(src), "-vf", "format=bgr24",
            "-f", "rawvideo", "pipe:1",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )

    # 3. 编码进程：rawvideo 从 stdin 读入并编码 mp4
    dst = tmp_path / "dst.mp4"
    enc = subprocess.Popen(
        [
            str(ffmpeg_bin), "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-s", f"{w}x{h}", "-r", str(fps), "-i", "pipe:0",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(dst),
        ],
        stdin=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    assert dec.stdout is not None and enc.stdin is not None

    # 4. Python 单线程搬运帧（与 run_pipe 相同的 shuttle 循环）
    n_frames = 0
    while True:
        buf = dec.stdout.read(frame_bytes)
        if not buf:
            break
        assert len(buf) == frame_bytes, "rawvideo 帧长必须恰为 w*h*3"
        enc.stdin.write(buf)
        n_frames += 1
    dec.stdout.close()
    enc.stdin.close()

    # 5. 两进程都必须零退出
    assert dec.wait(timeout=30) == 0, "解码进程异常退出"
    assert enc.wait(timeout=30) == 0, "编码进程异常退出"

    # 6. 帧数与输出可读性校验
    assert n_frames == fps * sec, f"期望 {fps * sec} 帧，实得 {n_frames}"
    probe = subprocess.run(
        [str(ffprobe_bin), "-v", "quiet", "-show_entries",
         "format=duration", "-of", "csv=p=0", str(dst)],
        check=True, capture_output=True, text=True,
    )
    duration = float(probe.stdout.strip())
    assert 0.5 <= duration <= 2.5, f"输出时长异常: {duration}s"
