# FFmpeg/ffprobe 子进程唯一封装（详设 8.2/8.4；T-6 防管道死锁）
from __future__ import annotations

import contextlib
import logging
import re
import shutil
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from ych.common.cancellation import CancellationToken, LineFn, ProgressFn, TaskCanceled
from ych.common.errors import (
    ERR_MED_FFMPEG_NOT_FOUND,
    ERR_MED_TIMEOUT_KILLED,
    ERR_MED_TRANSCODE_FAILED,
    AppError,
)

logger = logging.getLogger("ych.s1")

# winget 安装位兜底（DEC-008：PATH 未刷新环境下的已知安装路径）
_WINGET_LINKS = Path(r"C:\Users\rememberme\AppData\Local\Microsoft\WinGet\Links")

_TIME_RE = re.compile(r"time=(\d+):(\d+):([\d.]+)")

_STDERR_TAIL_LINES = 20


def _wait_or_kill(proc: subprocess.Popen[Any], timeout_s: float) -> int:
    """等待退出；超时强制终止进程树（防僵尸 ffmpeg 锁住输出文件）。"""
    try:
        return proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        logger.warning("ffmpeg(pid=%s) %ss 未退出，强制终止", proc.pid, timeout_s)
        FFmpegRunner._kill_tree(proc)
        try:
            return proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            return -1


def _find_binary(name: str) -> Path | None:
    """查找顺序：仓库 runtime/ → PATH → winget 固定路径（详设 8.2 + DEC-008）。"""
    repo_runtime = Path(__file__).resolve().parents[4] / "runtime" / f"{name}.exe"
    if repo_runtime.exists():
        return repo_runtime
    found = shutil.which(name)
    if found:
        return Path(found)
    cand = _WINGET_LINKS / f"{name}.exe"
    return cand if cand.exists() else None


class FFmpegRunner:
    """阻塞执行、stderr 逐行回调、超时/取消杀进程树、流式帧管线。"""

    def __init__(
        self,
        ffmpeg_path: Path | None = None,
        ffprobe_path: Path | None = None,
    ) -> None:
        # 显式注入优先（测试替身入口），否则按标准顺序定位
        self._ffmpeg = ffmpeg_path
        self._ffprobe = ffprobe_path

    def locate_binaries(self) -> tuple[Path, Path]:
        """定位 ffmpeg/ffprobe；任一缺失抛 MED001。"""
        if self._ffmpeg is None:
            self._ffmpeg = _find_binary("ffmpeg")
        if self._ffprobe is None:
            self._ffprobe = _find_binary("ffprobe")
        if self._ffmpeg is None or self._ffprobe is None:
            raise AppError(ERR_MED_FFMPEG_NOT_FOUND, "未找到 ffmpeg/ffprobe，请检查安装")
        return self._ffmpeg, self._ffprobe

    # ---- 进程树终止（Windows）----
    @staticmethod
    def _kill_tree(proc: subprocess.Popen[Any]) -> None:
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True, check=False, timeout=10,
            )
        except OSError:
            proc.kill()
        except subprocess.TimeoutExpired:
            logger.warning("taskkill 超时（pid=%s），退化 kill", proc.pid)
            with contextlib.suppress(OSError):
                proc.kill()

    # ---- 阻塞执行 ----
    def run(
        self,
        args: list[str],
        timeout_s: float | None = None,
        on_line: LineFn | None = None,
        token: CancellationToken | None = None,
    ) -> int:
        """执行 ffmpeg 子命令；stderr 逐行回调（T-6），返回退出码。

        超时 → 杀进程树并抛 MED011；取消 → 杀进程树并抛 TaskCanceled。
        """
        ffmpeg, _ = self.locate_binaries()
        cmd = [str(ffmpeg), *args]
        logger.debug("ffmpeg run: %s", " ".join(cmd[:8]))
        proc: subprocess.Popen[str] = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        tail: deque[str] = deque(maxlen=_STDERR_TAIL_LINES)
        assert proc.stderr is not None

        def _drain() -> None:
            for line in proc.stderr:  # type: ignore[union-attr]
                line = line.rstrip("\r\n")
                tail.append(line)
                if on_line is not None:
                    on_line(line)

        reader = threading.Thread(target=_drain, daemon=True)
        reader.start()

        start = time.monotonic()
        try:
            while True:
                code = proc.poll()
                if code is not None:
                    break
                if token is not None and token.cancelled:
                    self._kill_tree(proc)
                    raise TaskCanceled("转码已被取消")
                if timeout_s is not None and time.monotonic() - start > timeout_s:
                    self._kill_tree(proc)
                    raise AppError(ERR_MED_TIMEOUT_KILLED, "转码超时被终止")
                time.sleep(0.05)
        finally:
            reader.join(timeout=5)

        if token is not None and token.cancelled:
            raise TaskCanceled("转码已被取消")
        return proc.returncode or 0

    # ---- 进度解析（纯函数）----
    @staticmethod
    def parse_progress_line(line: str) -> float | None:
        """从 `time=00:01:23.45` 提取秒数；非进度行返回 None。"""
        m = _TIME_RE.search(line)
        if m is None:
            return None
        hh, mm, ss = m.groups()
        return int(hh) * 3600 + int(mm) * 60 + float(ss)

    # ---- 帧级流式管线（详设 8.4）----
    def run_pipe(
        self,
        decode_args: list[str],
        encode_args: list[str],
        frame_cb: Callable[[npt.NDArray[np.uint8]], npt.NDArray[np.uint8]],
        total_frames: int,
        on_progress: ProgressFn | None,
        token: CancellationToken,
        frame_size: tuple[int, int],
        fps: float,
    ) -> Path:
        """双子进程流式管线：解码→rawvideo→frame_cb→rawvideo→编码。

        decode_args：输入侧参数（如 [-i, in.mp4, -vf, fps=10]），输出规格由本方法追加；
        encode_args：编码侧完整命令尾（含 -i pipe:0、可选滤镜链与输出路径）。
        内存占用恒定 ≈2 帧；进度 = 已回调帧数/total_frames。
        """
        ffmpeg, _ = self.locate_binaries()
        w, h = frame_size
        frame_bytes = w * h * 3

        dec_cmd = [
            str(ffmpeg), *decode_args,
            "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1",
        ]
        enc_cmd = [str(ffmpeg), *encode_args]

        dec: subprocess.Popen[bytes] = subprocess.Popen(
            dec_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        enc: subprocess.Popen[bytes] = subprocess.Popen(
            enc_cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, text=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert dec.stdout is not None and enc.stdin is not None
        dec_tail: deque[str] = deque(maxlen=_STDERR_TAIL_LINES)
        enc_tail: deque[str] = deque(maxlen=_STDERR_TAIL_LINES)

        def _drain(pipe: Any, tail: deque[str]) -> None:
            readline = pipe.readline
            for raw in iter(readline, b""):
                tail.append(raw.decode("utf-8", errors="replace").rstrip())

        t1 = threading.Thread(target=_drain, args=(dec.stderr, dec_tail), daemon=True)
        t2 = threading.Thread(target=_drain, args=(enc.stderr, enc_tail), daemon=True)
        t1.start()
        t2.start()

        n_frames = 0
        stop_watch = threading.Event()

        def _watch_cancel() -> None:
            # 取消看护：token 触发即刻杀树。主循环的 read/write 可能在
            # 阻塞中永远等不到循环顶部的取消检查（解码停滞/磁盘卡顿），
            # 不杀进程会令 worker 无限期持有处理并发闸门，后续任务全部卡死
            while not stop_watch.is_set():
                if token.cancelled:
                    self._kill_tree(dec)
                    self._kill_tree(enc)
                    return
                stop_watch.wait(0.05)

        watcher = threading.Thread(
            target=_watch_cancel, daemon=True, name="ych-ffmpeg-cancel-watch",
        )
        watcher.start()

        try:
            while True:
                if token.cancelled:
                    break
                try:
                    buf = dec.stdout.read(frame_bytes)
                except OSError:
                    buf = b""   # 进程被看护杀死致管道失效：按 EOF 走收尾
                if not buf:
                    break
                if len(buf) != frame_bytes:
                    break   # 尾部残帧：解码端异常退出由返回码兜底
                frame = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 3)
                out = frame_cb(frame)
                try:
                    enc.stdin.write(np.ascontiguousarray(out).tobytes())
                except (BrokenPipeError, OSError):
                    # 编码端已退出（如滤镜/编码器参数错误）：杀树并带 stderr 报错
                    raise AppError(
                        ERR_MED_TRANSCODE_FAILED,
                        "帧级处理失败（编码端提前退出）",
                    ) from RuntimeError("\n".join(list(enc_tail)[-5:]))
                n_frames += 1
                if on_progress is not None and total_frames > 0:
                    on_progress(min(1.0, n_frames / total_frames))
        except BaseException:
            # 异常路径（frame_cb/写入/取消检查抛错）：不留僵尸进程与文件锁
            self._kill_tree(dec)
            self._kill_tree(enc)
            raise
        finally:
            stop_watch.set()
            # 管道关闭失败不影响主流程（进程退出即回收）
            with contextlib.suppress(OSError):
                if dec.stdout is not None:
                    dec.stdout.close()
            with contextlib.suppress(OSError):
                enc.stdin.close()
            t1.join(timeout=5)
            t2.join(timeout=5)
            watcher.join(timeout=2)

        if token.cancelled:
            # 显式杀树（看护可能尚未命中）：防编码端在 stdin 关闭后
            # 正常收尾写出残缺产物
            self._kill_tree(dec)
            self._kill_tree(enc)
            raise TaskCanceled("帧级处理已被取消")

        dec_code = _wait_or_kill(dec, 30)
        enc_code = _wait_or_kill(enc, 30)
        if dec_code != 0 or enc_code != 0:
            tail = "\n".join(list(dec_tail)[-_STDERR_TAIL_LINES:]
                             + list(enc_tail)[-_STDERR_TAIL_LINES:])
            raise AppError(
                ERR_MED_TRANSCODE_FAILED,
                f"帧级处理失败（decode={dec_code}, encode={enc_code}）",
            ) from RuntimeError(tail)

        # 输出路径 = encode_args 最后一个非选项参数
        out_candidates = [a for a in encode_args if not a.startswith("-")]
        out_path = Path(out_candidates[-1]) if out_candidates else Path("")
        if on_progress is not None:
            on_progress(1.0)
        return out_path





