# M5 - 素材库管理模块

> 模块目标：工作目录生命周期、三级归档与命名、素材扫描索引、原子写入与只读保护。是 M1/M2/M3 的落盘后端，自身不发网络/推理调用。
> 设计依据：`design/max_design.md` 第十章；需求 1.6 自动分类存储、2.5 处理结果
> 前置依赖：01-common、03-s4-net(无)、04-s3-db、05-s1-media
> 输出位置：`src/ych/core/m5_library/`

## 任务清单

### WorkDirManager（`workdir_manager.py`）
- [x] `validate(path)`：存在/可写/非系统关键目录校验 → FILE001/FILE002
- [x] `ensure_layout()`：创建 `已去重/` 根目录
- [x] `workdir_changed = Signal(Path)` + `workdir()` getter

### CategoryService（`category_service.py`）
- [x] `resolve_category(keyword)`：查 keyword_category 表；未命中新建映射（大类=关键词本身）
- [x] `rename_category(old, new)`：事务内更新两表
- [x] `merge(src, dst)`：物理搬移目录树 + 两表更新，先搬移成功再更新库

### ArchiveService 归档（`archive_service.py`）
- [x] `next_filename(platform, keyword, date_str, ext=".mp4")`：命名 `平台_关键词_三位序号.ext`；序号=max(磁盘现有, asset_index 计数)+1 零填充；仍冲突递增兜底
- [x] `archive_download(meta, temp_file, keyword, token)` 完整 10.3 流程：resolve_category → 逐级创建 workdir/C/关键词/YYYY-MM-DD/ → next_filename → atomic os.replace → protect_readonly(config 开关) → asset_index.upsert(raw) → 返回最终路径
- [x] `mirror_path_for_output(src, suffix, out_root=None)`：None→cleaned 同目录加后缀；out_root=已去重/→镜像 src 相对层级再加后缀

### ScanIndexer（`scan_indexer.py`）
- [x] `incremental_scan()`：os.walk 工作目录 *.mp4 → 与 all_paths() 差集 → 新文件懒 probe（仅时长分辨率）→ upsert；path 失效行删除；发 `scan_finished(int)`；全程工作线程执行
- [x] `classify_kind(name, rel_dir)`：含 _cleaned→cleaned；位于 已去重/ 或含 _deduped→deduped；否则 raw（纯函数）

### 只读保护接线
- [x] `archive_download` 按 config.readonly_protect_raw 开关调用 protect_readonly（atomic_write/protect_readonly 本体已在 01-common/common/fsutil 实现，本模块不重复实现，只做配置接线）

### 测试（对照 10.4）
- [x] tmp 工作目录+内存库：三级路径正确、序号连续性、同名冲突递增
- [x] mirror_path：深层嵌套源路径断言镜像输出路径
- [x] atomic_write：writer 中途抛异常 → 无半成品且 target 未变
- [x] 只读保护开关开/关行为
- [x] 预置 raw/cleaned/deduped 混合目录树断言 kind 分类与增量扫描行为

## 完成标准
10.4 全部用例通过；M1 下载落盘 / M2 _cleaned 输出 / M3 _deduped 输出三条路径均可由本模块产出。

