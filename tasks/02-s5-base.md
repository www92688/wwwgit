# S5 - 基础服务（配置 / 日志 / 国际化）

> 模块目标：配置读写、滚动日志、界面多语言。被所有模块依赖，自身不依赖业务模块。
> 设计依据：`design/max_design.md` 第五章
> 前置依赖：01-common（S3 可后置，启动早期用内存模式）
> 输出位置：`src/ych/services/s5_base/`

## 任务清单

### ConfigService（`config_service.py`）
- [x] `_DEFAULTS` 字典：完整落 2.4 参数表全部键（download_concurrency=3、process_concurrency=2、max_retry=2、retry_backoff_seconds=[2,8]、compare_candidates_per_platform=20、candidate_cache_ttl_days=7、feature_max_frames=300、detect_sample_frames=24、inpaint_tile_size=512、dedup_weights=(0.5,0.25,0.25)、net_probe_ttl_seconds=600、foreign_platforms_enabled=false、readonly_protect_raw=true、language=zh_CN、workdir=""、proxy 三件套、pexels/pixabay key 标记）
- [x] `get(key)` 未设置返回默认值；`get_typed(key, tp)` 泛型兼容 3.10
- [x] `set(key, value)` 写库 + 发射 `changed = Signal(str, object)`
- [x] 内存模式退化：SQLite 未就绪时仅内存，`database_ready()` 后回填持久化（支撑 UI 先行 ≤5s）
- [x] `secret_get/secret_set`：keyring 读写系统凭据管理器，表中只存布尔标记
- [x] CFG001/CFG002 错误映射

### LogService（`log_service.py`）
- [x] `setup(level="INFO")`：RotatingFileHandler `logs/app.log` 5MB×5 个；格式 `%(asctime)s %(levelname)s %(name)s %(message)s`
- [x] logger 名约定：ych.s1~ych.s5 / ych.m1~ych.m5 / ych.ui
- [x] `sanitize(msg)` 过滤 URL query 中 `key=`/`token=` 参数值

### I18nService（`i18n_service.py`）
- [x] QObject + `locale_changed = Signal(str)`
- [x] `switch_locale("zh_CN"|"en_US")`：卸载旧 QTranslator → 加载 i18n/<locale>.qm → installTranslator → 发信号
- [x] `available_locales()` 扫描 i18n 目录
- [ ] i18n 资源骨架：zh_CN.ts（源语言）/ en_US.ts 及 lrelease 产物 .qm（延后至阶段5 12-ui：需先有 tr() 源串，lrelease 工具已就绪）
- [ ] 切换提示文案：需重启才完全生效的项明确告知（属 UI 弹窗文案，延后至阶段5 12-ui）

### 单元测试（对照 5.4）
- [x] 内存模式 get 未设置键 → 返回默认值
- [x] set/get 往返（临时 sqlite 文件注入）
- [x] keyring 替身（fixture 内存字典后端）读写往返
- [x] switch 后 translator 安装并发出 locale_changed；tr() 样例串比对 qm 内容
- [x] sanitize 表驱动用例

## 完成标准
三服务可独立实例化并通过 5.4 全部测试点；无任何对 core/ui 的反向依赖。


