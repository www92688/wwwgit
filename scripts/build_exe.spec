# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 打包配置（详设 第八章 / SP-8 D4 剪裁范围：spec 编写+语法校验）
#
# 用法（仓库根目录执行）：
#   pyinstaller scripts/build_exe.spec --noconfirm
#
# 产物：
#   dist/源重构/  目录模式（便于 Inno Setup 直接采集）
#
# 说明：
# - runtime/ffmpeg.exe、ffprobe.exe 随包分发（详设 8.2 定位顺序第一位）；
# - runtime/models/*.onnx 为基础包可选：模型文件缺失时 S2 抛 AI001，
#   UI 引导按需下载（URL+sha256+断点续传，复用 S4.download_stream）；
# - i18n/*.qm 与 templates/* 一并打入。

import os
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent          # 仓库根


def _existing(*parts: str) -> list[tuple[str, str]]:
    """构造 datas 项；源不存在时跳过并告警（基础包允许缺模型）。"""
    src = ROOT.joinpath(*parts)
    if src.exists():
        return [(str(src), os.path.join(*Path(parts[:-1]).parts))]
    print(f"[build_exe.spec] WARN 缺少打包资源：{src}")
    return []


datas: list[tuple[str, str]] = []
for exe in ("ffmpeg.exe", "ffprobe.exe"):
    src = ROOT / "runtime" / exe
    if src.exists():
        datas.append((str(src), "runtime"))
    else:
        print(f"[build_exe.spec] WARN 缺少 {exe}（将依赖用户 PATH）")

models = ROOT / "runtime" / "models"
if models.exists():
    for f in models.glob("*.onnx"):
        datas.append((str(f), "runtime/models"))

i18n_dir = ROOT / "i18n"
if i18n_dir.exists():
    for qm in i18n_dir.glob("*.qm"):
        datas.append((str(qm), "i18n"))

templates = ROOT / "templates"
if templates.exists():
    for f in templates.glob("*"):
        if f.is_file():
            datas.append((str(f), "templates"))

hiddenimports = [
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "keyring.backends.Windows",
]

a = Analysis(
    [str(ROOT / "scripts" / "entry_point.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 精简体积：排除用不到的重型库（按需增删）
        "tkinter",
        "matplotlib",
        "pandas",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="YuChongGou",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                       # upx 易误报病毒，默认关闭
    console=False,                   # GUI 应用
    icon=str(ROOT / "resources" / "app.ico")
    if (ROOT / "resources" / "app.ico").exists()
    else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="YuChongGou",
)
