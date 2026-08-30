# S4 - 网络服务

> 模块目标：全应用唯一网络出口——会话管理、代理、超时、限速、请求级重试、流式下载断点续传。插件不得自建 session。
> 设计依据：`design/max_design.md` 第六章
> 前置依赖：01-common、02-s5-base
> 输出位置：`src/ych/services/s4_net/`

## 任务清单

### RateLimiter（`rate_limiter.py`）
- [x] 按 host 的令牌桶：`acquire(host, timeout_s=10.0)`
- [x] 时钟可注入（假时钟测试令牌发放节奏）

### HttpClient 会话构建（`http_client.py`）
- [x] `_build_session()`：Retry(total=max_retry, backoff_factor=1.5, status_forcelist=(429,500,502,503,504), allowed_methods=("GET","HEAD"))
- [x] 超时 (连接 5s, 读 30s)；UA="YuanChongGou/<ver>"
- [x] proxy_enabled 时设置 session.proxies（读 S5 配置）
- [x] `get_json(url, params, headers)` / `head(url)`
- [x] `net_error = Signal(str, str)` 供全局错误提示；请求日志强制过 LogService.sanitize

### 断点续传下载
- [x] `download_stream(url, dest, resume, on_progress, token) -> ResumeState`
- [x] 续传分支：resume 存在且临时文件在 → `Range: bytes={n}-`；206 且 ETag 一致 → 追加写；200/416/ETag 变化 → 丢弃重下
- [x] 无 resume → 普通 GET 创建 `dest+".part"`
- [x] 循环 iter_content(chunk=256KB)：token.check() → 写文件 → 累计字节数 → 每 ≥1s 或 ≥1MB 回调一次进度
- [x] 完成 fsync 返回 ResumeState（原子重命名留给调用方）
- [x] 取消/异常：保留 .part 与 ResumeState 供续传（ResumeState 持久化由调用方负责，如 M1 落 download_task.resume_state）
- [x] 不支持 Range（无 Accept-Ranges/非206）→ 静默降级整段重下，ResumeState.etag="" 标记

### 外网探测
- [x] `probe_url(url, timeout_s=5.0) -> "ok"|"dns_fail"|"conn_fail"`：DNS 解析错误→dns_fail；其余连接/TLS/超时→conn_fail；HEAD 失败退化 GET(range: bytes=0-0)

### 测试（对照 6.4）
- [x] responses mock 四场景：206 续传起点正确 / 200 重下 / 416 / ETag 变化，断言续传起点与 .part 内容
- [x] 限速器假时钟节奏断言
- [x] chunk 回调内触发 cancel → 抛 TaskCanceled 且 .part 保留
- [x] probe_url 三态矩阵

## 完成标准
6.4 全部替身用例通过；上层模块可仅依赖 HttpClient 完成下载与探测。

