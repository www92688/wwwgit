# M4 - 任务调度中心

> 模块目标：所有长任务统一入口与编排者：队列、并发控制、状态机、重试、失败记录、崩溃恢复。M1/M2/M3 以 register_handler 注册执行体，互相解耦。
> 设计依据：`design/max_design.md` 第十一章
> 前置依赖：01-common、04-s3-db
> 输出位置：`src/ych/core/m4_scheduler/`

## 任务清单

### 数据结构（`task_scheduler.py` 头部）
- [x] `ManagedTask(task_id uuid4 hex / payload / state / retry_count / db_row_id / token)`
- [x] `TaskResult(summary dict, output_path)`
- [x] `CrashRecoverySummary(resumed_downloads, moved_to_fail)`

### TaskScheduler
- [x] 4 个信号：task_submitted(str) / task_state(str,str,str) / task_progress(str,float) / queue_stats(int,int)
- [x] `register_handler(type, handler)`：download/preprocess/dedup/compare 四类注册点
- [x] `submit(payload, priority=0) -> str`：建 ManagedTask + 落库 pending → 入优先队列 → 尝试派发
- [x] `cancel(task_id)`：token.cancel()，TASK004
- [x] `submit_from_fail_record(record_id) -> str`：读 payload 全量重建任务（U4 一键重新处理）
- [x] 派发并发闸门：处理类 ProcessSemaphore(2)；下载类由 DownloadManager.semaphore(3)
- [x] T-1~T-5 线程约定落实：QRunnable 提交 QThreadPool、跨线程仅 Qt 信号、信号参数可拷贝对象

### TaskWorker（`task_worker.py`）
- [x] run() 五步：dao.mark_running 先写库 → handler(task) → 成功 finish(success,summary) → TaskCanceled→finish(canceled) → 其他异常交 RetryController
- [x] 异常仅在 worker 内捕获，不逃逸 run()（部分失败隔离）
- [x] finally：派发下一任务 + queue_stats 刷新

### RetryController（`retry_controller.py`）
- [x] `should_retry`：AppError 且 code ∈ {NET*, DL001, DL002, MED010, AI004, PLG003} 且 retry_count < max_retry
- [x] `backoff_seconds(i)`：[2,8] 指数退避；超限 → TASK003 + FailRecordManager.record

### FailRecordManager（`fail_record_manager.py`）
- [x] `record(task, err)`：file_name 取源文件名/meta.title；payload 存全量重建信息；中文 fail_reason
- [x] `requeue(record_id) -> str`

### CrashRecovery（`crash_recovery.py`）
- [x] `scan() -> CrashRecoverySummary`：download_task running → interrupted → 有 resume_state 重建 pending 续传，无则入失败列表；process_task running → interrupted → 写 fail_record("软件中断，请重新处理")

### 批量语义
- [x] batch_id 放入 payload.data，queue_stats 按 batch 聚合；任意一条终态失败不影响其余

### 测试（对照 11.5）
- [x] SyncFakeHandler 替身（立即成功/抛错/挂起可控）驱动状态机全迁移路径与信号序列断言
- [x] 重试：前 N 次失败第 N+1 次成功 → 最终 success 且 retry_count 正确
- [x] 重试超限 → fail_record 落行
- [x] 崩溃恢复：手工置 running → recover_on_startup 断言续传/转失败两分支
- [x] 挂起 handler 内 cancel → TASK004 终态
- [x] 批量 10 条中 3 条抛错 → 其余 7 条全部终态 success

## 完成标准
11.5 用例全过；M1/M2/M3 仅凭 register_handler+TaskPayload 即可接入，无相互 import。

