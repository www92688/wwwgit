"""启动前环境预检（供 app.py / CI / 打包后双击调用）。

检查清单（所有探测异常均被捕获，决不抛出）：
  1. ffmpeg / ffprobe 是否可执行（runtime/ 自带或 PATH）
  2. 必备 AI 模型是否就位（runtime/models/*.onnx）；缺失仅提示，不阻止
  3. 代理 / 外网连通性（可选，--net 时探测）

用法：
  python -m scripts.env_check            # 静默模式，退出码 0/1
  python -m scripts.env_check --verbose  # 打印明细
  python -m scripts.env_check --json     # JSON 报告（CI 解析）
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

# 模型文件清单：name -> 说明
REQUIRED_MODELS: dict[str, str] = {
    "watermark_yolov8n_640.onnx": "水印检测",
    "subtitle_det_ppocrv4_mobile.onnx": "字幕检测",
    "lama_fp32_512.onnx": "图像修复",
    "clip_vitb32_image.onnx": "构图特征",
}

# Winget 安装位兜底（本地 PATH 可能未刷新）
_WINGET = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/WinGet/Links"


def _runtime_dir() -> Path:
    """打包后位于可执行文件旁；源码运行位于仓库根。"""
    here = Path(sys.argv[0] if sys.argv[0] else __file__)
    candidate = here.resolve().parent / "runtime"
    if candidate.is_dir():
        return candidate
    return Path.cwd() / "runtime"


@dataclass
class EnvReport:
    ffmpeg_ok: bool = False
    ffmpeg_path: str = ""
    ffprobe_ok: bool = False
    ffprobe_path: str = ""
    models: dict[str, bool] = field(default_factory=dict)
    missing_models: list[str] = field(default_factory=list)
    net_ok: bool = False
    net_reason: str = ""

    @property
    def ready(self) -> bool:
        return self.ffmpeg_ok and self.ffprobe_ok

    def to_dict(self) -> dict:
        return {
            "ffmpeg_ok": self.ffmpeg_ok,
            "ffmpeg_path": self.ffmpeg_path,
            "ffprobe_ok": self.ffprobe_ok,
            "ffprobe_path": self.ffprobe_path,
            "models": self.models,
            "missing_models": self.missing_models,
            "net_ok": self.net_ok,
            "net_reason": self.net_reason,
            "ready": self.ready,
        }


def _find(name: str) -> Path | None:
    found = shutil.which(name)
    if found:
        return Path(found)
    cand = _WINGET / f"{name}.exe"
    runtime = _runtime_dir() / name
    if runtime.exists():
        return runtime
    return cand if cand.exists() else None


def check_binaries() -> tuple[bool, str]:
    ffmpeg = _find("ffmpeg")
    if ffmpeg:
        return True, str(ffmpeg)
    return False, ""


def check_models() -> dict[str, bool]:
    root = _runtime_dir() / "models"
    return {name: (root / name).exists() for name in REQUIRED_MODELS}


def check_net(timeout: float = 5.0) -> tuple[bool, str]:
    """探测外网（Pexels/Pixabay 直连）。失败不抛。"""
    import urllib.request

    for url in ("https://www.pexels.com", "https://www.pixabay.com"):
        try:
            urllib.request.urlopen(url, timeout=timeout)
            return True, "ok"
        except Exception as exc:
            last = str(exc)
    return False, last or "unknown"


def run(verbose: bool = False, check_net: bool = False) -> EnvReport:
    report = EnvReport()

    ok_f, path = check_binaries()
    report.ffmpeg_ok = ok_f
    report.ffmpeg_path = path
    if not ok_f:
        report.ffmpeg_ok = False
    # ffprobe
    bp = _find("ffprobe")
    report.ffprobe_path = str(bp) if bp else ""
    report.ffprobe_ok = bool(bp)

    models = check_models()
    report.models = models
    report.missing_models = [k for k, v in models.items() if not v]

    if check_net:
        net_ok, reason = check_net()
        report.net_ok = net_ok
        report.net_reason = reason

    if verbose:
        print(f"[env_check] ffmpeg: {'ok' if report.ffmpeg_ok else 'MISSING'}"
              f" @ {report.ffmpeg_path or '-'}")
        print(f"[env_check] ffprobe: {'ok' if report.ffprobe_ok else 'MISSING'}"
              f" @ {report.ffprobe_path or '-'}")
        for name, present in models.items():
            tag = "ok" if present else "MISSING"
            print(f"[env_check] model {name} ({REQUIRED_MODELS[name]}): {tag}")
        if check_net:
            print(f"[env_check] net: {'ok' if report.net_ok else 'BLOCKED'}"
                  f" ({report.net_reason})")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="环境预检")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--net", action="store_true",
                        help="同时探测外网连通性")
    args = parser.parse_args()

    report = run(verbose=args.verbose or args.json, check_net=args.net)
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))

    # 退出码约定：ffmpeg/ffprobe 缺失 → 1；模型缺失仅提示，仍视为 0
    if not report.ready:
        if not args.json:
            print("⚠ 环境不完整：ffmpeg/ffprobe 缺失，部分功能不可用。",
                  file=sys.stderr)
        return 1
    if report.missing_models and not args.json:
        print(f"⚠ 缺失 AI 模型：{', '.join(report.missing_models)}。"
              " 部分 AI 功能将降级。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
