# 一次性工具：为 i18n/*.ts 中 unfinished 条目填充翻译。
# zh_CN 恒等；en_US 按下表（context, source) → 译文。
# 用法：python scripts/fill_i18n_translations.py
from __future__ import annotations

import xml.etree.ElementTree as ET

EN: dict[tuple[str, str], str] = {
    ("AssetTree", "未分类"): "Uncategorized",
    ("AssetTree", "未命名"): "Untitled",
    ("AssetTree", "未知日期"): "Unknown date",
    ("AssetTree", "时长 {d}s"): "Duration {d}s",
    ("CapturePage", "请先输入关键词再搜索"): "Enter a keyword before searching",
    ("CapturePage", "搜索服务未就绪，无法搜索"): "Search service not ready, cannot search",
    ("CapturePage", "请至少勾选一个采集平台"): "Select at least one platform to capture",
    ("CapturePage", "正在搜索：{kw} …"): "Searching: {kw} …",
    ("CapturePage", "外网不可达"): "Internet unreachable",
    ("CapturePage", "当前网络环境无法访问外网平台，请检查 VPN/代理设置"):
        "Global platforms are unreachable from this network. "
        "Check your VPN/proxy settings",
    ("CapturePage", "下载服务未就绪，无法入队"): "Download service not ready, cannot enqueue",
    ("CapturePage", "文件定位功能未装配"): "File locator is not available",
    ("CapturePage", "外网检测失败：{msg}"): "Foreign network check failed: {msg}",
    ("CapturePage", "搜索发起失败：{msg}"): "Failed to start the search: {msg}",
    ("CapturePage", "打开文件管理器失败"): "Failed to open the file manager",
    ("CapturePage", "国内平台（抖音可用，其余为占位）"):
        "CN platforms (Douyin available; others are placeholders)",
    ("CapturePage", "登录抖音"): "Log in to Douyin",
    ("CapturePage", "弹出浏览器登录抖音并保存 Cookie；登录状态长期有效，"
                    "无需每次采集都登录"):
        "Opens a browser to log in to Douyin and save cookies; "
        "the login persists, no need to log in every time",
    ("CapturePage", "登录状态已保存在本机，重启应用无需重新登录；"
                    "Cookie 失效时会提示重新登录"):
        "Login state is saved locally; no re-login after restart. "
        "You will be asked to log in again if cookies expire",
    ("CapturePage", "在关键词框粘贴博主主页链接（douyin.com/user/…）后搜索"):
        "Paste a creator homepage link (douyin.com/user/…) "
        "into the keyword box, then search",
    ("CapturePage", "抖音登录功能未装配"): "Douyin login is not wired up",
    ("CapturePage", "已弹出登录窗口：在浏览器完成登录后，回到弹出的控制台窗口按回车"):
        "Login window opened: finish login in the browser, then press "
        "Enter in the popped-up console window",
    ("CapturePage", "抖音登录成功，现在可以粘贴博主主页链接搜索了"):
        "Douyin login succeeded; paste a creator homepage link to search",
    ("CapturePage", "已弹出登录窗口：在浏览器中完成抖音登录后会自动保存，无需按回车"):
        "Login window opened: Douyin login in the browser saves "
        "automatically once completed; no need to press Enter",
    ("CapturePage", "登录窗口打开中…"): "Opening login window…",
    ("CapturePage", "抖音登录未完成：{msg}"): "Douyin login not completed: {msg}",
    ("CapturePage", "已登录：{name}"): "Logged in: {name}",
    ("CapturePage", "已登录抖音（长期有效）"): "Logged in to Douyin (persists)",
    ("CapturePage", " · 风控冷却中（{hint}）"): " · Risk-control cooldown ({hint})",
    ("CapturePage", "\n今日抖音搜索额度：{used}/{limit}（密集采集容易触发"
                    "平台风控，被拒后需等待冷却）"):
        "\nToday's Douyin search quota: {used}/{limit} (dense crawling "
        "easily triggers platform risk control; wait out the cooldown)",
    ("CapturePage", "今日抖音搜索额度已用 {used}/{limit}，"
                    "密集采集容易触发平台风控，请注意节制"):
        "Today's Douyin search quota used {used}/{limit}; dense crawling "
        "easily triggers platform risk control, please slow down",
    ("CapturePage", "正在搜索中，请稍候…"): "A search is already running, please wait…",
    ("CapturePage", "搜索中 {s}s…"): "Searching… {s}s",
    ("CapturePage", "搜索失败：{msg}"): "Search failed: {msg}",
    ("SettingsPage", "模型下载组件未装配"): "Model downloader is not available",
    ("SettingsPage", "已有模型在下载中，请先等待或取消"):
        "A model download is already in progress; wait or cancel it first",
    ("DedupPage", "打开文件管理器失败"): "Failed to open the file manager",
    ("DedupPage", "该批任务已提交，请勿重复点击"):
        "This batch has been submitted; no need to click again",
    ("DedupPage", "打开失败：文件不存在或系统没有关联的播放器"):
        "Failed to open: the file does not exist or no player is "
        "associated with it",
    ("DedupResultBar", "打开输出目录"): "Open output folder",
    ("DedupResultBar", "去重结果"): "Dedup result",
    ("DedupResultBar", "成功 {n} 条"): "{n} succeeded",
    ("DedupResultBar", "跳过 {n} 条"): "{n} skipped",
    ("DedupResultBar", "失败 {n} 条"): "{n} failed",
    ("DedupResultBar", " 等 {n} 个"): " and {n} more",
    ("DedupResultBar", "已存在未重处理：{names}"):
        "Already existed (not reprocessed): {names}",
    ("DedupResultBar", "失败原因：{msgs}"): "Failure reason: {msgs}",
    ("DedupResultBar", "重复度 {a}% → {b}%"): "Similarity {a}% → {b}%",
    ("DedupResultBar", "打开失败：目录不存在或无法访问"):
        "Failed to open: the folder does not exist or is inaccessible",
    ("DedupResultBar", "重新生成"): "Regenerate",
    ("DedupResultBar", "删除「已去重」目录下的同名输出文件，并按当前勾选素材与方案重新去重"):
        "Delete the existing output files of the same names in the 已去重/ "
        "folder and dedup again with the current scheme",
    ("FailRecordModel", "文件名"): "File name",
    ("DedupPage", "轻度"): "Light",
    ("DedupPage", "中度"): "Medium",
    ("DedupPage", "重度"): "Heavy",
    ("DedupPage", "（推荐档）"): " (Recommended)",
    ("DedupPage", "请先在左侧勾选素材，再分析重复度"):
        "Select assets on the left before analyzing duplicates",
    ("DedupPage", "请先在左侧勾选素材，再开始去重"):
        "Select assets on the left before starting dedup",
    ("DedupPage", "重新生成去重输出"): "Regenerate dedup output",
    ("DedupPage", "将删除「已去重」目录下 {n} 个同名输出文件，并按当前方案重新去重。继续？"):
        "{n} output file(s) of the same names will be deleted from the "
        "已去重/ folder and deduplicated again with the current scheme. "
        "Continue?",
    ("DedupPage", "删除旧输出失败：{msgs}"):
        "Failed to delete the old output: {msgs}",
    ("FailurePage", "读取失败记录出错：{msg}"):
        "Failed to read the failure records: {msg}",
    ("FailurePage", "已重新提交 {n} 条，{m} 条失败：{detail}"):
        "Resubmitted {n}; {m} failed: {detail}",
    ("FailurePage", "已删除 {n} 条，{m} 条删除失败，请重试"):
        "Deleted {n}; {m} could not be deleted, please retry",
    ("DownloadQueueView", "取消"): "Cancel",
    ("DownloadQueueView", "任务已结束"): "Task finished",
    ("FailRecordModel", "失败原因"): "Failure reason",
    ("FailRecordModel", "错误码"): "Error code",
    ("FailRecordModel", "时间"): "Time",
    ("FailRecordModel", "类型"): "Type",
    ("FilterPanel", "筛选条件"): "Filters",
    ("FilterPanel", "时长范围"): "Duration range",
    ("FilterPanel", "画质要求"): "Quality",
    ("FilterPanel", "文件大小"): "File size",
    ("FilterPanel", "水印情况"): "Watermark",
    ("HelpDialog", "使用说明"): "User Guide",
    ("HelpDialog", "采集（输词 → 搜 → 下）"): "Capture (keywords → search → download)",
    ("HelpDialog", "顶部输入关键词（逗号分隔可批量）→ "):
        "Enter keywords at the top (comma-separated for batch) → ",
    ("HelpDialog", "点击「搜索」→ "): "click \"Search\" → ",
    ("HelpDialog", "勾选结果点击「下载选中」。下载数量上限在列表下方设置。"):
        "check results and click \"Download selected\". "
        "The download limit is set below the list.",
    ("HelpDialog", "预处理（勾素材 → 选项 → 开始）"):
        "Preprocess (select assets → options → start)",
    ("HelpDialog", "左侧勾选素材 → "): "check assets on the left → ",
    ("HelpDialog", "右侧选择处理项（去水印/去字幕/裁剪/比例/去原声，手动模式可在画布框选区域）→ "):
        "choose options on the right (watermark/subtitle removal, crop, aspect "
        "ratio, audio strip; manual mode lets you box regions on the canvas) → ",
    ("HelpDialog", "点击「开始处理」。"): "click \"Start Processing\".",
    ("HelpDialog", "去重（选素材 → 选方案 → 开始）"):
        "Dedup (select assets → choose scheme → start)",
    ("HelpDialog", "先「分析重复度」获得推荐档位，选择轻/中/重度或自定义参数 → "):
        "run \"Analyze duplicates\" to get the recommended preset, then pick "
        "light/medium/heavy or custom parameters → ",
    ("HelpDialog", "点击「开始去重」。"): "click \"Start Dedup\".",
    ("HelpDialog", "输出保存在 已去重/ 目录并展示前后重复度对比。"):
        "Output is saved in the 已去重/ (deduplicated) folder with a "
        "before/after similarity comparison.",
    ("HelpDialog", "失败列表"): "Failures",
    ("HelpDialog", "失败任务可一键重新处理；网络/密钥问题请先到设置页检查。"):
        "Failed tasks can be resubmitted with one click; for network/key "
        "issues, check the Settings page first.",
    ("HelpDialog", "关闭"): "Close",
    ("MainWindow", "初始设置"): "Initial Setup",
    ("MainWindow", "开始使用前，请先选择素材保存的工作目录。\n建议选择空间充足的磁盘分区。"):
        "Before you start, choose the working directory where assets are "
        "saved.\nA drive with plenty of free space is recommended.",
    ("MainWindow", "选择素材工作目录"): "Select Asset Working Directory",
    ("MainWindow", "目录不可用"): "Directory Unavailable",
    ("MainWindow", "该目录无法作为工作目录：\n{err}"):
        "This directory cannot be used as the working directory:\n{err}",
    ("OptionPanel", "去水印"): "Watermark removal",
    ("OptionPanel", "去字幕"): "Subtitle removal",
    ("OptionPanel", "启用裁剪"): "Enable crop",
    ("OptionPanel", "裁剪框 X / Y"): "Crop box X / Y",
    ("OptionPanel", "裁剪框 宽 / 高"): "Crop box W / H",
    ("OptionPanel", "不调整"): "Keep original",
    ("OptionPanel", "9:16 竖屏"): "9:16 portrait",
    ("OptionPanel", "16:9 横屏"): "16:9 landscape",
    ("OptionPanel", "1:1 方形"): "1:1 square",
    ("OptionPanel", "裁切"): "Crop",
    ("OptionPanel", "黑边"): "Letterbox",
    ("OptionPanel", "目标比例 / 策略"): "Aspect ratio / strategy",
    ("OptionPanel", "去除原声"): "Remove original audio",
    ("PlayerWidget", "预览不可用"): "Preview unavailable",
    ("PreprocessPage", "请先在左侧勾选要处理的素材"):
        "Select assets to process on the left first",
    ("PreprocessPage",
     "「手动框选」需要先「预览框选帧」并在画布上框选区域；"
     "若不需要去水印/去字幕，请改为「关闭」或「自动检测」"):
        "\"Manual\" requires previewing a frame and boxing regions on the "
        "canvas first; if you don't need watermark/subtitle removal, set it "
        "to \"Off\" or \"Auto detect\"",
    ("PreprocessPage", "请先在左侧勾选素材，再预览框选帧"):
        "Select assets on the left before previewing a frame",
    ("PreprocessPage", "预览帧正在加载，请稍候…"):
        "The preview frame is loading, please wait…",
    ("PreprocessPage", "抽帧失败：{msg}"): "Frame extraction failed: {msg}",
    ("PreprocessPage", "该批任务已提交，请勿重复点击"):
        "This batch has been submitted; no need to click again",
    ("PreprocessPage", "处理服务未就绪，无法开始处理"):
        "Processing service not ready, cannot start",
    ("PreprocessPage", "预览能力未装配，无法加载素材帧"):
        "Preview is not wired up; cannot load the frame",
    ("ReportView", "综合重复度"): "Overall similarity",
    ("ReportView", "构图"): "Composition",
    ("ReportView", "运镜"): "Camera motion",
    ("ReportView", "节奏"): "Pacing",
    ("ReportView", "处理前 → 处理后：—"): "Before → after: —",
    ("ReportView", "来源"): "Source",
    ("ReportView", "平台"): "Platform",
    ("ReportView", "标题"): "Title",
    ("ReportView", "相似度%"): "Similarity %",
    ("ReportView", "状态"): "Status",
    ("ReportView", "处理前 → 处理后：{a} → {b}"): "Before → after: {a} → {b}",
    ("ReportView", "手动"): "Manual",
    ("ReportView", "自动"): "Auto",
    ("ReportView", "本地库"): "Local",
    ("ReportView", "本地素材库"): "Local library",
    ("ReportView",
     "未找到可比对的视频：在线平台 {p} 均不可用，本地素材库中也没有同关键词的"
     "其它素材。此处的 0 分不代表重复度低。"):
        "No comparable videos found: online platforms ({p}) are all "
        "unavailable, and the local library has no other videos with the "
        "same keyword. The 0 score here does NOT mean low similarity.",
    ("ReportView",
     "未找到可比对的视频：素材不在工作目录归档结构中（需要 大类/关键词/日期/ "
     "文件路径），且没有在线平台可用。此处的 0 分不代表重复度低。"):
        "No comparable videos found: the file is not in the workdir archive "
        "layout (category/keyword/date/filename) and no online platform is "
        "available. The 0 score here does NOT mean low similarity.",
    ("ReportView",
     "在线平台 {p} 不可用，以上结果基于其余 {n} 个对比对象"
     "（其中本地素材库 {m} 个）。"):
        "Online platforms {p} are unavailable; the results above are based "
        "on the other {n} targets ({m} from the local library).",
    ("ResultList", "大小未知"): "size unknown",
    ("ResultList", "全选"): "Select all",
    ("ResultList", "下载选中"): "Download selected",
    ("ResultList", "还没有搜索结果"): "No search results yet",
    ("ResultList", "在顶部输入关键词，点击「搜索」试试"):
        "Enter a keyword above and click \"Search\"",
    ("ResultList", "暂不可用平台：{}"): "Temporarily unavailable platforms: {}",
    ("ResultList", "占位未开放"): "placeholder, not enabled",
    ("ResultList", "未登录或未配置 Key"): "not logged in / API key missing",
    ("ResultList", "Key 无效"): "API key invalid",
    ("ResultList", "触发限频"): "rate limited",
    ("ResultList", "暂不可用"): "unavailable",
    ("ResultList", "接口结构变更"): "site API changed",
    ("ResultList", "连接超时"): "connection timed out",
    ("ResultList", "域名解析失败"): "DNS resolution failed",
    ("ResultList", "代理不可用"): "proxy unreachable",
    ("ResultList", "外网不可达"): "internet unreachable",
    ("ResultList", "未知原因"): "unknown reason",
    ("ResultList", "无水印"): "no watermark",
    ("ResultList", "有水印"): "watermarked",
    ("ResultList", "时长 {d}s"): "{d}s",
    ("ResultList", "下载选中（{}）"): "Download selected ({})",
    ("ResultList", "该素材没有来源页链接"): "This asset has no source page link",
    ("ResultList", "该行不可打开来源页"): "This row has no source page to open",
    ("ResultList", "打开浏览器失败"): "Failed to open the browser",
    ("PlayerWidget", "文件不存在：{path}"): "File not found: {path}",
    ("PreprocessPage", "打开文件管理器失败"): "Failed to open the file manager",
    ("ResultList", "请先勾选要下载的结果"): "Check the results to download first",
    ("SchemeEditor", "方案参数"): "Scheme Parameters",
    ("SettingsPage", "启用代理"): "Enable proxy",
    ("SettingsPage", "原始素材只读保护"): "Read-only protection for raw assets",
    ("SettingsPage", "下载并行数"): "Download concurrency",
    ("SettingsPage", "处理并行数"): "Processing concurrency",
    ("SettingsPage", "失败重试次数"): "Retries on failure",
    ("SettingsPage", "✓ 已就绪（{mb} MB）"): "✓ Ready ({mb} MB)",
    ("SettingsPage", "下载中 {pct}%"): "Downloading {pct}%",
    ("SettingsPage", "未命名"): "Untitled",
    ("SettingsPage", "未选模型"): "No model selected",
    ("SettingsPage", "选择素材工作目录"): "Select Asset Working Directory",
    ("SettingsPage", "API Key 写入系统凭据库失败：{msg}"):
        "Failed to write the API Key to the system credential store: {msg}",
    ("SettingsPage", "Key 写入系统凭据库失败：{msg}"):
        "Failed to write the key to the system credential store: {msg}",
    ("SettingsPage", "取消"): "Cancel",
    ("SettingsPage", "已请求取消，将保留已下载断点…"):
        "Cancel requested; the partial download is kept for resume…",
    ("SettingsPage", "目录不可用"): "Directory Unavailable",
    ("SettingsPage", "该目录无法作为工作目录：\n{err}"):
        "This directory cannot be used as the working directory:\n{err}",
    # 代理地址单框（可整段粘贴）
    ("SettingsPage",
     "可整段粘贴代理地址，支持 IP:端口 或 http://IP:端口"
     "（Clash 默认 127.0.0.1:7890，v2rayN 默认 10809）。"
     "VPN 的订阅链接不是代理地址。若 VPN 使用 TUN/系统代理模式，无需启用本项。"):
        "Paste the full proxy address; both IP:port and http://IP:port work "
        "(Clash default 127.0.0.1:7890, v2rayN default 10809). A VPN "
        "subscription link is not a proxy address. If your VPN uses "
        "TUN/system proxy mode, you don't need this.",
    ("SettingsPage",
     "无法识别的代理地址：{text}"
     "（示例：127.0.0.1:7890 或 http://127.0.0.1:7890）"):
        "Unrecognized proxy address: {text} "
        "(examples: 127.0.0.1:7890 or http://127.0.0.1:7890)",
    ("SettingsPage", "已清空代理地址。"): "Proxy address cleared.",
    ("SettingsPage", "代理地址缺少端口：请写成 IP:端口（如 127.0.0.1:7890）。"):
        "The proxy address has no port: use IP:port (e.g. 127.0.0.1:7890).",
    ("SettingsPage", "已启用"): "Enabled",
    ("SettingsPage", "已保存，勾选「启用代理」后生效"):
        "Saved; tick \"Enable proxy\" to activate",
    ("SettingsPage", "✓ {state}：http://{host}:{port}"):
        "✓ {state}: http://{host}:{port}",
    # API Key 明文/密文切换
    ("SettingsPage", "显示 / 隐藏 API Key"): "Show / hide API Key",
    # 模型手动导入（无公开下载源的自训练模型）
    ("SettingsPage", "导入…"): "Import…",
    ("SettingsPage", "缺失（无公开下载源；点「导入…」选择模型文件）"):
        "Missing (no public download source; click \"Import…\" to pick the "
        "model file)",
    ("SettingsPage", "导入中：复制并校验模型契约…"):
        "Importing: copying and verifying the model contract…",
    ("SettingsPage", "选择模型文件"): "Select Model File",
    ("SettingsPage", "ONNX 模型 (*.onnx);;所有文件 (*.*)"):
        "ONNX models (*.onnx);;All files (*.*)",
    ("SettingsPage", "{name} 导入成功并通过契约校验，即刻可用。"):
        "{name} imported and verified; ready to use immediately.",
    ("SettingsPage", "导入失败：{msg}（文件需与模型用途的输入/输出契约一致）"):
        "Import failed: {msg} (the file must match the input/output "
        "contract of its intended use)",
    ("SettingsPage", "已有模型在下载/导入中，请先等待完成"):
        "A model download/import is already in progress; wait for it to finish",
    ("SettingsPage", "已有模型在导入中，请先等待完成"):
        "A model import is already in progress; wait for it to finish",
    ("Toast", "查看日志"): "View logs",
    ("_Bridge", "预处理"): "Preprocess",
    ("_Bridge", "去重"): "Dedup",
    ("_Bridge", "重复度分析"): "Duplicate analysis",
    ("_Bridge", "，{word} {count} 条"): ", {word}: {count}",
    ("_Bridge", "失败"): "failed",
    ("_Bridge", "跳过"): "skipped",
    ("_Bridge", "预处理完成：成功 {n} 条{extra}；输出与原文件同目录（_cleaned 后缀）"):
        "Preprocess completed: {n} succeeded{extra}; output is saved next to "
        "the source files (with _cleaned suffix)",
    ("_Bridge", "；重复度 {a}% → {b}%"): "; similarity {a}% → {b}%",
    ("_Bridge", "去重完成：成功 {n} 条{extra}{tail}；输出在 已去重/ 目录"):
        "Dedup completed: {n} succeeded{extra}{tail}; output is in the "
        "已去重/ folder",
    ("_Bridge", "输出已存在，未重新处理：{names}\n"
     "如需重新生成，请删除「已去重」目录下的同名文件"):
        "Output already exists; not reprocessed: {names}\n"
        "To regenerate, delete the file of the same name in the 已去重/ folder",
    ("_Bridge", " 等 {n} 个"): " and {n} more",
    ("_Bridge", "，跳过 {n} 条（{names} 已存在）"):
        ", {n} skipped ({names} already exist)",
    ("_Bridge", "，跳过 {n} 条"): ", {n} skipped",
    ("_Bridge", "，失败 {n} 条：{msgs}"): ", {n} failed: {msgs}",
    ("_Bridge",
     "重复度分析完成：最高相似度 {score}%，对比 {n} 个视频"
     "（本地素材库 {local}、在线平台 {online}），已按结果标注推荐档位"):
        "Duplicate analysis finished: highest similarity {score}% across "
        "{n} targets ({local} local, {online} online); the recommended "
        "preset has been marked based on the result",
    ("_Bridge",
     "重复度分析完成：没有可比对的视频（在线平台均不可用，本地素材库也没有"
     "同关键词的其它素材）。0 分不代表重复度低"):
        "Duplicate analysis finished: no comparable videos (online platforms "
        "are all unavailable and the local library has no other videos with "
        "the same keyword). The 0 score does NOT mean low similarity",
    ("_Bridge", "。可到 设置 → AI 模型 下载所需模型"):
        ". Download the required models in Settings → AI Models",
    ("_Bridge", "{label}失败：{msg}{extra}"): "{label} failed: {msg}{extra}",
    ("_Bridge", "{label}任务已取消"): "{label} task canceled",
    ("_CanvasLabel", "加载素材帧后在此框选区域\n（左键拖拽，可多次框选）"):
        "Load a frame to box regions here\n(left-drag; you can box multiple "
        "regions)",
}


# 手法注册表（核心层）的显示名/参数标签：源码经变量传入 tr()，
# lupdate 无法提取，这里手工维护进对应上下文。
MANUAL: dict[str, dict[str, str]] = {
    "MainWindow": {
        "采集工作台": "Capture",
        "预处理工作台": "Preprocess",
        "去重工作台": "Dedup",
        "失败列表": "Failures",
        "设置": "Settings",
    },
    "OptionPanel": {
        "关闭": "Off",
        "自动检测": "Auto detect",
        "手动框选": "Manual",
    },
    "FilterPanel": {
        "原始画质": "Original",
        "720p 及以上": "720p and above",
        "1080p 及以上": "1080p and above",
        "不限制": "Any",
        "无水印": "No watermark",
        "有水印": "Watermarked",
    },
    "SettingsPage": {
        "OpenAI 官方": "OpenAI (Official)",
        "智谱 GLM": "Zhipu GLM",
        "Kimi 月之暗面": "Kimi (Moonshot)",
        "硅基流动": "SiliconFlow",
        "魔搭 ModelScope": "ModelScope",
        "阿里云百炼": "Alibaba Cloud Bailian",
        "火山方舟（豆包）": "Volcano Ark (Doubao)",
        "Ollama 本地": "Ollama (local)",
        "LM Studio 本地": "LM Studio (local)",
        # 以下源串经变量传入 tr()，lupdate 提取不到（占位提示/动态标题）
        "未配置": "Not configured",
        "已配置（留空=不修改）": "Configured (leave empty to keep)",
        "已配置（留空并保存=清除）": "Configured (leave empty and save to clear)",
        "编辑 AI 服务": "Edit AI Service",
    },
}

# 已有条目的译文修正（lupdate 会保留旧译文，覆盖质量差/过时的）
FIXUPS: dict[tuple[str, str], str] = {
    ("DedupPage", "分析重复度"): "Analyze duplicates",
    ("DedupPage", "开始去重"): "Start Dedup",
}
# 手法注册表（核心层）的显示名/参数标签：源码经变量传入 tr()，手工维护
MANUAL["SchemeEditor"] = {
        "边框": "Border",
        "画面镜像": "Mirror",
        "调色滤镜": "Color Filter",
        "变速": "Speed",
        "裁切缩放": "Crop & Scale",
        "亮度": "Brightness",
        "对比度": "Contrast",
        "饱和度": "Saturation",
        "色温": "Temperature",
        "预设风格": "Style preset",
        "边框宽度": "Border width",
        "样式": "Style",
        "颜色": "Color",
        "模式": "Mode",
        "裁切比例": "Crop ratio",
        "倍速": "Speed factor",
        "作用范围": "Scope",
        "镜像方向": "Mirror axis",
}


def _ensure_message(ctx_el: ET.Element, source: str) -> ET.Element:
    """在上下文中查找/创建指定 source 的 message 节点（标记待翻译）。"""
    for m in ctx_el.findall(".//message"):
        if (m.find("source").text or "") == source:
            return m
    m = ET.SubElement(ctx_el, "message")
    ET.SubElement(m, "source").text = source
    tr_el = ET.SubElement(m, "translation")
    tr_el.set("type", "unfinished")   # 交由下方填充循环补译文
    return m


def main() -> None:
    missing: list[tuple[str, str]] = []
    for loc, identity in (("zh_CN", True), ("en_US", False)):
        path = f"i18n/{loc}.ts"
        tree = ET.parse(path)
        root = tree.getroot()
        contexts = {c.find("name").text: c for c in root.findall("context")}
        # 手工条目：确保 message 节点存在
        for cname, entries in MANUAL.items():
            ctx_el = contexts.get(cname)
            if ctx_el is None:
                ctx_el = ET.SubElement(root, "context")
                ET.SubElement(ctx_el, "name").text = cname
            for src in entries:
                _ensure_message(ctx_el, src)
        for ctx in root.findall("context"):
            cname = ctx.find("name").text
            for m in ctx.findall(".//message"):
                tr_el = m.find("translation")
                if tr_el is None:
                    continue
                src = m.find("source").text or ""
                ttype = tr_el.get("type")
                # 手工条目每次 lupdate 都会被改回 vanished，这里强制重置
                if not identity and src in MANUAL.get(cname, {}):
                    tr_el.text = MANUAL[cname][src]
                    tr_el.set("type", "finished")
                    continue
                if identity:
                    if ttype in (None, "unfinished", "vanished"):
                        tr_el.text = src
                        tr_el.set("type", "finished")
                    continue
                if ttype != "unfinished":
                    continue
                text = EN.get((cname, src))
                if text is None:
                    missing.append((cname, src))
                    continue
                tr_el.text = text
                tr_el.set("type", "finished")
        # 已有条目的译文修正
        for (cname, src), text in FIXUPS.items():
            ctx_el = contexts.get(cname)
            if ctx_el is None:
                continue
            for m in ctx_el.findall(".//message"):
                if (m.find("source").text or "") == src:
                    tr_el = m.find("translation")
                    tr_el.text = text
                    tr_el.set("type", "finished")
        tree.write(path, encoding="utf-8", xml_declaration=True)
        print(loc, "已填充")
    if missing:
        print("缺少翻译:", missing)
        raise SystemExit(1)
    print("全部翻译就绪")


if __name__ == "__main__":
    main()
