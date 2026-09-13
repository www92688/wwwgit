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
    ("DedupPage", "该批任务已提交，请勿重复点击"):
        "This batch has been submitted; no need to click again",
    ("DedupPage", "打开失败：文件不存在或系统没有关联的播放器"):
        "Failed to open: the file does not exist or no player is "
        "associated with it",
    ("FailRecordModel", "文件名"): "File name",
    ("DedupPage", "轻度"): "Light",
    ("DedupPage", "中度"): "Medium",
    ("DedupPage", "重度"): "Heavy",
    ("DedupPage", "（推荐档）"): " (Recommended)",
    ("DedupPage", "请先在左侧勾选素材，再分析重复度"):
        "Select assets on the left before analyzing duplicates",
    ("DedupPage", "请先在左侧勾选素材，再开始去重"):
        "Select assets on the left before starting dedup",
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
    ("ResultList", "大小未知"): "size unknown",
    ("ResultList", "全选"): "Select all",
    ("ResultList", "下载选中"): "Download selected",
    ("ResultList", "还没有搜索结果"): "No search results yet",
    ("ResultList", "在顶部输入关键词，点击「搜索」试试"):
        "Enter a keyword above and click \"Search\"",
    ("ResultList", "暂不可用平台：{}"): "Temporarily unavailable platforms: {}",
    ("ResultList", "无水印"): "no watermark",
    ("ResultList", "有水印"): "watermarked",
    ("ResultList", "时长 {d}s"): "{d}s",
    ("ResultList", "下载选中（{}）"): "Download selected ({})",
    ("ResultList", "该素材没有来源页链接"): "This asset has no source page link",
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
    ("_Bridge", "重复度分析完成{extra}，已按结果标注推荐档位"):
        "Duplicate analysis finished{extra}; the recommended preset has been "
        "marked based on the result",
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
