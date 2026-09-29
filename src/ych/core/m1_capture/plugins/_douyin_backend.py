# 抖音采集后端：驱动 vendor/douyin_downloader 内置下载器（独立子进程，不 import）
# 设计要点：全部参数经「文本模板替换」生成运行配置，避免在 src 内引入 YAML 依赖；
# 子进程模式仿 S1 FFmpegRunner（排水线程 + 轮询 + 取消/超时杀进程树）
from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

from ych.common.cancellation import CancellationToken, TaskCanceled
from ych.common.errors import AppError
from ych.common.schemas import VideoMeta

logger = logging.getLogger("ych.m1")

# 主页链接形态：https://www.douyin.com/user/<sec_uid>
_HOME_URL_RE = re.compile(r"https?://(?:www\.)?douyin\.com/user/[0-9A-Za-z_-]+")
_TEMPLATE_PLACEHOLDER = "__DOUYIN_HOME_URL__"
_MS_TOKEN_RE = re.compile(r"(?m)^  msToken: (\S+)\s*$")
# 登录态 Cookie：真实登录才会写入；匿名会话只有 msToken/ttwid 等
_LOGIN_COOKIE_RE = re.compile(r"(?m)^  (?:sessionid|sid_tt): \S+\s*$")

_HARVEST_TIMEOUT_S = 300.0
_DOWNLOAD_TIMEOUT_S = 3600.0
_TAIL_LINES = 30
_DEFAULT_MAX_POSTS = 30
_MEDIA_EXTS = frozenset({".mp4", ".mov", ".avi", ".mkv", ".flv", ".webm"})
# 滚动回补参数以代码为准：运行态 config_plugin.yml 可能残留调优前的
# 旧默认（240/8 滚动没完），叠加风控就会吃满 300s 超时
_MAX_SCROLLS = 60
_IDLE_ROUNDS = 4


def _cli_env() -> dict[str, str]:
    """子进程环境：强制 UTF-8 标准流。

    中文 Windows 下管道 stdout 默认 GBK，内置下载器的 ℹ️ 等日志字符
    无法编码会让子进程在首个 print 处直接崩溃（UnicodeEncodeError）。
    PYTHONUTF8 整体开 UTF-8 模式，防子孙进程丢 PYTHONIOENCODING。
    """
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def resolve_vendor_root(override: str = "") -> Path | None:
    """定位内置下载器目录：显式配置优先，否则仓库根 vendor/douyin_downloader。

    打包发行（sys.frozen）时 vendor 不会随包存在，需用 douyin_vendor_dir
    指向外部安装位；找不到返回 None。
    """
    text = override.strip()
    if text:
        path = Path(text)
        return path if (path / "run.py").is_file() else None
    root = Path(__file__).resolve().parents[5] / "vendor" / "douyin_downloader"
    return root if (root / "run.py").is_file() else None


def extract_home_url(keyword: str) -> str | None:
    """从用户输入中提取抖音博主主页链接；不是主页链接返回 None。"""
    match = _HOME_URL_RE.search(keyword.strip())
    return match.group(0) if match else None


def read_live_config(vendor_root: Path) -> str:
    """读取运行态配置 config_plugin.yml 文本（含登录后的 Cookie）。

    首次使用时从入库模板 config_plugin.template.yml 引导生成；
    两者都缺失抛 PLG010（视为组件不完整）。
    """
    path = vendor_root / "config_plugin.yml"
    if not path.exists():
        template = vendor_root / "config_plugin.template.yml"
        try:
            shutil.copyfile(template, path)
        except OSError as exc:
            raise AppError(
                "PLG010", "内置下载器缺少配置模板 config_plugin.template.yml",
                cause=exc,
            ) from exc
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AppError("PLG010", "内置下载器配置读取失败", cause=exc) from exc


def build_run_config(
    template: str,
    home_url: str,
    max_posts: int,
    start_date: str = "",
    end_date: str = "",
    download_media: bool = False,
    use_database: bool = True,
) -> str:
    """把本次运行参数写进模板文本（纯文本替换，键位唯一性由模板保证）。

    number.post=0 表示不限数量（配合 start/end 日期窗口使用）。
    download_media：下载阶段须显式打开 video——模板默认 video:false
    （搜索只要元数据），若不翻转，下载运行永远产出不了媒体文件。
    use_database：搜索阶段关闭增量库——搜索只取元数据，落库反而会让
    重复任务/并发实例在 SQLite 上互相踩（下载阶段保留增量去重）。

    两处历史踩坑（2026-09-28）：
    - 日期行的引号必须两种都认：模板是双引号，但运行态 config_plugin.yml
      会被 cookie_fetcher 按其风格重写成单引号——只认一种会让日期窗口
      静默失效，number.post=0 时等于全量下载整个主页。
    - link 段不依赖占位符：占位符可能被任何重写吞掉，直接按段整体替换，
      杜绝"残留旧链接→采错博主"。
    """
    text = _force_link_section(template, home_url)
    text = re.sub(r"(?m)^(  post: )\d+$", rf"\g<1>{max(0, max_posts)}", text, count=1)
    text = re.sub(
        r"(?m)^start_time: ['\"].*['\"]$",
        f'start_time: "{start_date}"', text, count=1,
    )
    text = re.sub(
        r"(?m)^end_time: ['\"].*['\"]$",
        f'end_time: "{end_date}"', text, count=1,
    )
    if download_media:
        text = re.sub(r"(?m)^video: false$", "video: true", text, count=1)
    if not use_database:
        text = re.sub(r"(?m)^database: true$", "database: false", text, count=1)
    text = re.sub(
        r"(?m)^(\s*max_scrolls: )\d+$", rf"\g<1>{_MAX_SCROLLS}", text, count=1,
    )
    text = re.sub(
        r"(?m)^(\s*idle_rounds: )\d+$", rf"\g<1>{_IDLE_ROUNDS}", text, count=1,
    )
    # 替换是纯文本手术，任一关键键位没写进去都会让子进程静默跑偏
    # （link 段留着旧地址→采错人；日期丢失→全量下载），这里逐项断言
    problems: list[str] = []
    if f"  - {home_url}" not in text:
        problems.append("主页链接写入失败")
    if start_date and f'start_time: "{start_date}"' not in text:
        problems.append("start_time 写入失败")
    if end_date and f'end_time: "{end_date}"' not in text:
        problems.append("end_time 写入失败")
    if download_media and "\nvideo: true" not in f"\n{text}":
        problems.append("video 开关写入失败")
    if problems:
        raise AppError(
            "PLG010",
            f"内置下载器运行配置生成异常（{('、'.join(problems))}），"
            "config_plugin.yml 结构可能与预期不符",
        )
    return text


def _force_link_section(text: str, home_url: str) -> str:
    """把 link 段整体替换为只含目标主页一项（任何缩进/占位/多条目形态）。

    link 段下若残留第二条链接，CLI 会一并采集（采错范围）；占位符缺失时
    replace 式注入会静默失效。逐行扫描保证这两种坑都不存在。
    """
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    replaced = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if not replaced and re.match(r"^link:\s*(#.*)?$", line):
            out.append(f"link:\n  - {home_url}\n")
            replaced = True
            i += 1
            # 跳过 link 段原有的全部列表项（可能不止一条、缩进不定）
            while i < len(lines) and re.match(r"^[ \t]*-[ \t]", lines[i]):
                i += 1
            continue
        out.append(line)
        i += 1
    if not replaced:
        # 没有 link 段：模板不完整，调用方断言会报错；这里原样返回
        return text
    return "".join(out)


def write_run_config(vendor_root: Path, text: str) -> Path:
    """落盘按次生成的运行配置（固定文件名；调用方持有进程锁串行化）。"""
    path = vendor_root / "_run_config.yml"
    path.write_text(text, encoding="utf-8")
    return path


def _kill_tree(proc: subprocess.Popen[Any]) -> None:
    """终止进程树（run.py 会再拉起浏览器兜底子进程，必须连树一起杀）。"""
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True, check=False, timeout=10,
        )
    except OSError:
        with contextlib.suppress(OSError):
            proc.kill()
    except subprocess.TimeoutExpired:
        logger.warning("taskkill 超时（pid=%s），退化 kill", proc.pid)
        with contextlib.suppress(OSError):
            proc.kill()


def run_cli(
    vendor_root: Path,
    args: list[str],
    token: CancellationToken | None,
    timeout_s: float,
) -> tuple[int, list[str]]:
    """阻塞运行 vendored CLI；返回 (退出码, 输出尾部)。

    取消 → 杀进程树并抛 TaskCanceled；超时 → 杀进程树并抛 PLG010。
    """
    # -X utf8：解释器级 UTF-8 模式，比环境变量更早生效（首条 print 即受保护）
    cmd = [sys.executable, "-X", "utf8", "run.py", *args]
    logger.debug("douyin cli: %s", " ".join(cmd[:6]))
    proc: subprocess.Popen[str] = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_cli_env(),
        cwd=str(vendor_root),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    tail: deque[str] = deque(maxlen=_TAIL_LINES)
    assert proc.stdout is not None

    def _drain() -> None:
        for line in proc.stdout:  # type: ignore[union-attr]
            tail.append(line.rstrip("\r\n"))

    reader = threading.Thread(target=_drain, daemon=True)
    reader.start()

    start = time.monotonic()
    try:
        while True:
            code = proc.poll()
            if code is not None:
                break
            if token is not None and token.cancelled:
                _kill_tree(proc)
                raise TaskCanceled("抖音采集已取消")
            if time.monotonic() - start > timeout_s:
                _kill_tree(proc)
                raise AppError("PLG010", "抖音下载器执行超时，已终止")
            time.sleep(0.05)
    finally:
        reader.join(timeout=5)

    if token is not None and token.cancelled:
        raise TaskCanceled("抖音采集已取消")
    return proc.returncode or 0, list(tail)


def collect_harvest(harvest_dir: Path, max_posts: int) -> list[dict[str, object]]:
    """收集本次搜索目录下的作品元数据 JSON（*_data.json，新者在前）。"""
    if not harvest_dir.is_dir():
        return []
    files = sorted(
        harvest_dir.rglob("*_data.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    out: list[dict[str, object]] = []
    for path in files[: max(0, max_posts)]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("跳过无法解析的元数据文件：%s", path.name)
            continue
        if isinstance(data, dict):
            out.append(data)
    return out


def match_media_file(download_dir: Path, aweme_id: str) -> Path | None:
    """按文件名模板中的 {id} 片段匹配已下载的主媒体文件（新者优先）。"""
    if not aweme_id or not download_dir.is_dir():
        return None
    candidates = sorted(
        (p for p in download_dir.rglob(f"*{aweme_id}*") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for path in candidates:
        if path.suffix.lower() in _MEDIA_EXTS:
            return path
    return None


def has_real_cookies(vendor_root: Path) -> bool:
    """cookies 段须同时有真实 msToken 与登录态 Cookie（sessionid/sid_tt）。

    未登录按回车同样会落盘匿名会话（只有 msToken/ttwid 等），只查
    msToken 会把匿名态误判为已登录，故必须叠加登录态键。
    """
    try:
        text = (vendor_root / "config_plugin.yml").read_text(encoding="utf-8")
    except OSError:
        return False
    match = _MS_TOKEN_RE.search(text)
    value = match.group(1) if match else ""
    if not value or value == "YOUR_MS_TOKEN" or len(value) < 20:
        return False
    return _LOGIN_COOKIE_RE.search(text) is not None


_SELF_URL_RE = re.compile(r"/user/(MS4wLjAB[0-9A-Za-z_-]+)")


def read_self_sec_uid(vendor_root: Path) -> str:
    """读取登录流程捕获的「自己主页」sec_uid（self_user.txt）；缺失返回空串。"""
    try:
        text = (vendor_root / "self_user.txt").read_text(encoding="utf-8")
    except OSError:
        return ""
    match = _SELF_URL_RE.search(text)
    return match.group(1) if match else ""


def spawn_cookie_login(vendor_root: Path) -> subprocess.Popen[Any]:
    """新控制台运行 cookie_fetcher：浏览器登录抖音，自动检测登录后保存。

    新控制台用于展示登录日志并提供回车兜底确认（自动检测为主）。
    必须换用控制台版 python.exe：应用经 pythonw 启动时 sys.executable
    无标准流，cookie_fetcher 会在首个 print/input 处崩溃（窗口一闪而过）。
    """
    python = Path(sys.executable)
    if python.name.lower() in {"pythonw.exe", "pythonw"}:
        console = python.with_name("python.exe")
        if console.is_file():
            python = console
    return subprocess.Popen(
        [str(python), "-m", "tools.cookie_fetcher", "--config", "config_plugin.yml"],
        cwd=str(vendor_root),
        creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
    )


def new_harvest_dir(vendor_root: Path) -> Path:
    """每次搜索独立的暂存目录，避免跨关键词的旧元数据混入结果。"""
    path = vendor_root / "_harvest" / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    return path


def downloads_dir(vendor_root: Path) -> Path:
    """下载暂存目录（跨任务共享，配合内置增量库避免重复下载）。"""
    path = vendor_root / "_downloads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def meta_from_aweme(data: dict[str, object], home_url: str) -> VideoMeta | None:
    """aweme 原始元数据 → VideoMeta；字段缺失宽松归零，无 id 丢弃。

    参照 plugin_base._as_int/_as_float 的宽松取值约定。
    """
    from ych.core.m1_capture.plugin_base import _as_float, _as_int

    aweme_id = str(data.get("aweme_id") or data.get("id") or "")
    if not aweme_id:
        return None
    video_raw = data.get("video")
    video = video_raw if isinstance(video_raw, dict) else {}
    # 抖音 video.duration 单位为毫秒；顶层 duration 兜底视为秒
    duration_s = _as_int(video.get("duration")) / 1000.0 or _as_float(data.get("duration"))
    create_time = _as_int(data.get("create_time"))
    date_str = ""
    if create_time > 0:
        date_str = datetime.fromtimestamp(create_time).strftime("%Y-%m-%d")
    cover_raw = video.get("cover")
    cover = cover_raw if isinstance(cover_raw, dict) else {}
    url_list = cover.get("url_list")
    cover_url = ""
    if isinstance(url_list, list) and url_list:
        first = url_list[0]
        if isinstance(first, str):
            cover_url = first
    return VideoMeta(
        plugin_id="douyin",
        video_key=aweme_id,
        title=str(data.get("desc") or ""),
        page_url=f"https://www.douyin.com/video/{aweme_id}",
        duration_s=duration_s,
        width=_as_int(video.get("width")),
        height=_as_int(video.get("height")),
        watermark_tag="no",   # 内置下载器取无水印源
        download_url="",      # 无直链：download 由插件子进程实现
        thumbnail_url=cover_url,
        extra={"home_url": home_url, "date": date_str},
    )
