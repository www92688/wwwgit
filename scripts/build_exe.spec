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

# 调色预设（color_filter 经 bundle_data_root() 定位 _internal/runtime/luts）
luts = ROOT / "runtime" / "luts"
if luts.exists():
    for f in luts.glob("*.cube"):
        datas.append((str(f), "runtime/luts"))
else:
    print("[build_exe.spec] WARN 缺少 runtime/luts（调色预设将退化为 colorbalance 近似）")

i18n_dir = ROOT / "i18n"
if i18n_dir.exists():
    for qm in i18n_dir.glob("*.qm"):
        datas.append((str(qm), "i18n"))

# 主题样式（app.py 经 fsutil.bundle_root() 定位到 _internal/ych/ui/u6_common）
qss = ROOT / "src" / "ych" / "ui" / "u6_common" / "theme.qss"
if qss.exists():
    datas.append((str(qss), os.path.join("ych", "ui", "u6_common")))
else:
    print("[build_exe.spec] WARN 缺少 theme.qss（打包版将无主题样式）")

templates = ROOT / "templates"
if templates.exists():
    for f in templates.glob("*"):
        if f.is_file():
            datas.append((str(f), "templates"))

# 应用图标（exe 内嵌 + 打包后 setWindowIcon 经 bundle_data_root 定位）
app_ico = ROOT / "resources" / "app.ico"
if app_ico.exists():
    datas.append((str(app_ico), "resources"))

hiddenimports = [
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "keyring.backends.Windows",
]

# 平台插件由 plugin_manager 经 importlib 动态发现，静态分析不可见，
# 不列进来打包版启动即崩（ModuleNotFoundError: ...m1_capture.plugins）；
# 从目录枚举，新增 *_plugin.py 无需改此处
_plugins_dir = ROOT / "src" / "ych" / "core" / "m1_capture" / "plugins"
hiddenimports += ["ych.core.m1_capture.plugins"] + [
    f"ych.core.m1_capture.plugins.{f.stem}"
    for f in sorted(_plugins_dir.glob("*_plugin.py"))
]

# 未使用的 PySide6 模块整族排除（体积裁剪）：对应的 Qt6*.dll 与 .pyd
# 不再入包。⚠ QtMultimedia/QtNetwork 在用（视频预览/缩略图抓取），勿加
_excluded_qt = [
    "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.QtQuickControls2",
    "PySide6.QtQml", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtVirtualKeyboard", "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets", "PySide6.QtWebChannel", "PySide6.QtWebSockets",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs",
    "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtHelp",
    "PySide6.QtUiTools", "PySide6.QtSensors", "PySide6.QtSerialPort",
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning",
    "PySide6.QtLocation", "PySide6.QtTextToSpeech", "PySide6.QtScxml",
    "PySide6.QtStateMachine", "PySide6.QtRemoteObjects", "PySide6.QtHttpServer",
    "PySide6.QtNetworkAuth", "PySide6.QtOpcUa", "PySide6.QtSpatialAudio",
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
        *_excluded_qt,
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

# ---- 构建后裁剪（PyInstaller 的 PySide6 钩子会无视 excludes 收齐全部
# Qt6*.dll，只能在这里删）。仅删源代码确认未引用的模块族；QtMultimedia
# 只依赖 Core/Gui/Network（已验证其 PE 导入表），视频预览不受影响 ----
import shutil as _shutil

_PRUNE_PATTERNS = [
    "Qt6Quick*.dll", "Qt6Qml*.dll", "Qt6Pdf*.dll", "Qt6VirtualKeyboard.dll",
    "QtQuick*.pyd", "QtQml*.pyd", "QtPdf*.pyd", "QtPdfWidgets*.pyd",
    "QtQuickWidgets*.pyd", "QtQuick3D*.pyd", "QtVirtualKeyboard*.pyd",
]
_PRUNE_DIRS = ["qml", "QtQuick", "QtQml", "plugins/virtualkeyboard"]

_pyside_dir = Path(DISTPATH) / coll.name / "_internal" / "PySide6"
_saved = 0
for _pat in _PRUNE_PATTERNS:
    for _f in _pyside_dir.glob(_pat):
        _saved += _f.stat().st_size
        _f.unlink()
for _name in _PRUNE_DIRS:
    _d = _pyside_dir / _name
    if _d.is_dir():
        _saved += sum(f.stat().st_size for f in _d.rglob("*") if f.is_file())
        _shutil.rmtree(_d, ignore_errors=True)
print(f"[build_exe.spec] 裁剪未用 Qt 模块：{_saved / 1048576:.1f} MB")
