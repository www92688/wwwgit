# 业务模块间服务接口协议（防 core 内模块反向依赖，详设第三章）
# M1/M2/M3 之间不直接 import 对方实现，跨模块协作只经本文件协议或 M4 调度中心。
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ych.common.cancellation import CancellationToken
from ych.common.schemas import TaskPayload, VideoMeta


@runtime_checkable
class IDownloadArchiveTarget(Protocol):
    """M5 归档能力协议（M1 下载落盘 / M2/M3 输出落盘的依赖入口）。"""

    def archive_download(
        self,
        meta: VideoMeta,
        temp_file: Path,
        keyword: str,
        token: CancellationToken | None = None,
    ) -> Path:
        """下载产物归档：三级目录 + 序号命名 + 原子移动 + 只读保护 + 索引。"""
        ...

    def mirror_path_for_output(
        self,
        src: Path,
        suffix: str,
        out_root: Path | None = None,
    ) -> Path:
        """计算预处理/去重输出的镜像路径（cleaned 同目录 / deduped 镜像层级）。"""
        ...


@runtime_checkable
class ITaskSchedulerProtocol(Protocol):
    """M4 任务调度中心对外唯一入口协议。"""

    def submit(self, payload: TaskPayload, priority: int = 0) -> str:
        """提交任务，返回 task_id。"""
        ...

    def cancel(self, task_id: str) -> None:
        """协作式取消任务。"""
        ...

    def register_handler(
        self,
        task_type: str,
        handler: Callable[[Any], Any],
    ) -> None:
        """注册任务类型执行体（M1/M2/M3 启动时各自注册）。"""
        ...

