# 决策日志（追加式）

字段：日期 | 编号 | 背景 | 决定 | 影响

---

| 日期 | 编号 | 背景 | 决定 | 影响 |
|------|------|------|------|------|
| 2026-08-25 | DEC-001 | 主 Agent 启动时发现系统仅有 Python 3.14.3，不满足详设 2.1 的 >=3.10,<3.13 基线（PySide6>=6.6/onnxruntime>=1.17 在 3.14 无兼容保证） | 经 winget 安装 Python 3.12.10（用户级），`.venv` 基于 3.12.10 创建 | pyproject `requires-python = ">=3.10,<3.13"` 可原样执行；无遗留 |
| 2026-08-25 | DEC-002 | D1 Spike 替身化：00-spike.md 不做真实性能验证（无人工资源、无真实素材/模型/Key） | 在 `tests/spikes/` 写合成数据冒烟测试四项（ffmpeg 管道 / onnxruntime 会话 / PySide6+QThreadPool / SQLite WAL 并发）；全部通过即 P0 放行；详设「实测调整」参数一律取 2.4 默认值 | SP-1~SP-8 标记「已降级（替身冒烟）」；真实环境校准列入 final_report 遗留人工事项 |
| 2026-08-25 | DEC-003 | 环境无 ffmpeg/ffprobe，integration 用例将全部跳过，S1/M2/M3 端到端无法真实验证 | 经 winget 安装 Gyan.FFmpeg 9.0 full build；二进制定位于 `C:\Users\rememberme\AppData\Local\Microsoft\WinGet\Links\`（PATH 新终端生效） | integration 用例可真实运行；打包阶段仍需随包分发 runtime/ffmpeg.exe |
| 2026-08-25 | DEC-005 | opencode Task 工具（subagent 派发通道）连续 3 次返回「Upstream request failed: Endpoint is unavailable」，子 Agent 机制暂不可用 | 按 8 章降级规则，主 Agent 接管各模块亲自实现+自验；每个新阶段开始时重试 Task 工具一次，恢复即切回派发模式 | 各模块质量门标准不降低；汇报格式由主 Agent 自记于 progress 与 commit message |
| 2026-08-25 | DEC-006 | mypy 以 python_version=3.10 解析时，numpy 2.x 的类型存根使用 PEP 695 `type` 语句导致语法报错（详设 2.1 写作 numpy>=1.26 时 2.x 尚未发布） | 按 8 章「锁保守版本」：pyproject 将依赖钉为 `numpy>=1.26,<2`、`opencv-python-headless>=4.9,<5`（均在详设声明范围内），mypy 基线 python_version=3.10 保持不变 | 运行时实际安装 numpy 1.26.4 / cv2 4.11；后续模块若确需 numpy 2 再另行评估 |
| 2026-08-25 | DEC-007 | ruff RUF001/002/003 将中文全角标点标记为「歧义字符」，与全局硬约束第 8 条「注释中文」冲突（中文正常排版即全角标点） | 在 [tool.ruff.lint] ignore 中显式豁免 RUF001/RUF002/RUF003，其余规则全量保留 | 非弱化质量门：仅关闭与项目语言约定直接冲突的三条字符类规则 |
| 2026-09-25 | DEC-009 | 用户要求并入抖音批量下载能力；原声明「仅调用官方接口」与之冲突；上游 douyin-downloader（jiji262，MIT）已在本机实测可下载主页作品 | 采用 vendor+子进程集成：整库裁剪搬入 vendor/douyin_downloader（上游逻辑零改动），ych 侧 DouyinPlugin 经文本模板生成配置、子进程调用 run.py；搜索=video:false+json:true 元数据抓取，下载=按作品日期开时间窗口（end 含当天）+按文件名 {id} 匹配媒体；Cookie 经新控制台 cookie_fetcher 写入运行态 config_plugin.yml（入库模板占位、运行态 gitignore）；PluginManager 增 invalidate() 支持登录后即时生效 | README 合规声明同步修订；分发含抖音能力的构建前需重新评估平台条款风险；vendor 升级走手动同步（对照 VENDORED.md） |
| 2026-09-26 | DEC-010 | 抖音链路联调暴露五处运行期缺陷：①登录子进程经 pythonw 启动无标准流（窗口一闪而过）；②登录浏览器被系统残留死代理（Clash 7897）劫持（ERR_PROXY_CONNECTION_FAILED）；③登录态判定只查 msToken，匿名会话误判"已登录"；④抖音已封锁纯 HTTP 作品列表（ArgusSecurity 403），CLI 唯一可行路径是浏览器回补，但默认滚屏参数（240 次/8 轮）使其超过宿主 300s 超时；⑤下载阶段从未翻转模板的 video:false，媒体文件永远产出不了 | ①spawn 换控制台版 python.exe；②cookie_fetcher 浏览器加 `--no-proxy-server` 强制直连；③has_real_cookies 叠加 sessionid/sid_tt 校验 + 登录成功先看子进程退出码 + 启动时按文件态回滚残留标记；④回补参数调优 240/8→60/4（实测 20 条 15s）+ 登录改为 cookies 轮询自动确认（回车降为兜底）；⑤build_run_config 增 download_media/use_database 两参：下载阶段显式 video:true，搜索阶段 database:false（不污染增量库、规避多实例 SQLite 锁）；另：设置页代理改单框宽容解析、API Key 加明文切换、无源模型改「导入…」+契约校验、pixabay 对 URL 关键词返空 | 详见 `doc/HANDOFF-抖音登录与采集修复.md`（含未完成的干净库 4 步验收清单）；改动未提交；需用户单实例运行 |

## 阶段0 冒烟结果与 P0 放行记录

- 冒烟① ffmpeg 解码→Python 逐帧→编码双进程管道：通过（64x48@10fps 合成视频，10 帧往返，输出可被 ffprobe 读取）；
- 冒烟② onnxruntime CPU 会话：通过（CPUExecutionProvider 可用；真实模型缺失走加载失败降级路径验证——对应生产代码 AI001/AI002 触发路径）；
- 冒烟③ PySide6 导入 + QThreadPool 满载主线程响应 <1s：通过；
- 冒烟④ SQLite WAL 4 线程并发写 200 行零异常：通过。

**结论：四项全过 → P0 放行（K-4）。详设「实测调整」参数一律取 2.4 默认值。SP-1~SP-8 状态见 `design/spike_results.md`。**

---

## 遗留人工事项（滚动汇总，收尾并入 final_report.md）

- （待各阶段补充）
