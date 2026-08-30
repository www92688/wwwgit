# M1 - 素材采集模块

> 模块目标：插件式平台采集框架、搜索协调筛选、下载队列（断点续传/并发/上限）、外网能力检测。
> 设计依据：`design/max_design.md` 第十二章；需求模块一
> 前置依赖：03-s4-net、04-s3-db、07-m5-library、08-m4-scheduler；SP-6 结论回填限频参数
> 输出位置：`src/ych/core/m1_capture/`

## 任务清单

### 插件框架（12.1）
- [x] `plugin_base.py` PlatformPlugin ABC：id/display_name/region/requires_api_key/enabled_by_default + check_available(≤5s 不抛异常)/search/pick_download_variant(默认高度≥min_height 最小体积优先)/download 默认实现=download_stream
- [x] `plugin_manager.py` discover()（扫描 *_plugin.py）/ by_region() / enabled()(settings.enabled_plugins JSON + foreign_platforms_enabled 总开关) / availability(ttl 缓存)

### Pexels 插件完整实现（12.1.3）
- [x] 可用性检查 GET /videos/search?per_page=1（Header `Authorization: <key>`）：200→可用、401/403→PLG002、超时→PLG010
- [x] 搜索映射：GET /videos/search（params：query、per_page=min(max_count,80)、page=1）；响应映射 id→video_key/url→page_url/duration/width/height(最高规格)/image→thumbnail_url；video_files 过滤 file_type=="video/mp4" → pick_download_variant（选定项 link→download_url、file_size→file_size_bytes）
- [x] watermark_tag 恒 "no"；RateLimiter 配额 1 req/18s；429 → PLG003

### Pixabay 插件完整实现（12.1.4）
- [x] 可用性检查 GET /api/videos/?key=<k>&q=test&per_page=3：400 含 "invalid key" → PLG002；200 含 hits → 可用
- [x] hits 映射（id/duration/views 等）+ 缩略图由 picture_id 拼 `https://i.vimeocdn.com/video/<picture_id>_640.jpg`；videos 字典 large/medium/small/tiny 展开为变体，tiny 按 gif 扩展名过滤
- [x] 限频保守 1 req/2s（SP-6 实测后回填）

### 六平台骨架占位（12.1.5）
- [x] douyin/kuaishou/bilibili/xiaohongshu/tiktok/youtube 统一骨架：search/download 抛 AppError("PLG010")，check_available=(False,"not_implemented")

### 搜索协调与筛选（12.2）
- [x] ResultFilter.apply 纯函数：时长区间/min_height/大小上下限(None 不限)/watermark 三态过滤；排序分辨率降序→时长升序
- [x] HistoryService.record/suggestions（SearchHistoryDao 薄封装）
- [x] SearchCoordinator.search_multi(keywords, filters, per_platform_limit, token)：异步执行；单平台异常隔离记 unavailable_platforms；结果合并→ResultFilter→history.add→发 search_finished/search_failed

### 下载队列（12.3）
- [x] DownloadManager.enqueue_downloads(metas, keyword, limit)：截前 limit 条 → 每条 create download_task(pending) → 组装 download batch payload 提交 M4 → limit 生效停止并入 DL010 提示任务
- [x] handle_one(task)：semaphore(默认3).acquire → resume 读库 → temp=.downloading/<uuid>.part → plugin.download → ArchiveService.archive_download → 进度节流回写 ≥1s → finally release
- [x] item_updated = Signal(int,str,float,str)

### 外网检测（12.4）
- [x] ForeignNetChecker.check(force=False)：PROBE_URLS 三站 probe_url → 任一 ok=OK / 全 dns_fail=OFFLINE / 混 conn_fail=BLOCKED；TTL=config.net_probe_ttl_seconds 缓存；结果写 app_settings 跨重启可见

### 测试（对照 12.6）
- [x] responses mock Pexels/Pixabay 四分支（200/401/429/超时）+ fixtures 样例 JSON
- [x] 六骨架插件断言 PLG010 + not_implemented
- [x] FakePluginManager（2 假插件其一抛错）断言隔离性与 unavailable 列表
- [x] ResultFilter 表驱动
- [x] FakePlugin+tmp 目录：limit 截断、并发闸门计时断言分批、中断后续传字节数正确
- [x] 探测结果矩阵三态映射

## 完成标准
12.6 全过；Pexels/Pixabay 真实 Key 下手动冒烟完成一次「搜→筛→下→归档」闭环。
