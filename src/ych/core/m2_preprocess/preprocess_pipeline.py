# 预处理流水线入口（详设 13.2/13.3）：路径分派、原子收尾、批量 handler
from __future__ import annotations

import logging
from pathlib import Path
from typing import cast

from ych.common.cancellation import CancellationToken, ProgressFn, SkippedSignal
from ych.common.errors import AppError
from ych.common.schemas import TaskPayload
from ych.core.interfaces import IDownloadArchiveTarget
from ych.core.m2_preprocess.filter_only_processor import FilterOnlyProcessor
from ych.core.m2_preprocess.frame_level_processor import FrameLevelProcessor
from ych.core.m2_preprocess.ops import PreprocessOps, decide_path, revive_ops
from ych.core.m2_preprocess.subtitle_handler import SubtitleHandler
from ych.core.m4_scheduler.fail_record_manager import FailRecordManager
from ych.core.m4_scheduler.task_scheduler import TaskResult
from ych.services.s1_media.probe_service import ProbeService

logger = logging.getLogger("ych.m2")


class PreprocessPipeline:
    """单条素材预处理：probe 校验 → 路径分派 → 原子落盘。"""

    def __init__(
        self,
        prober: ProbeService,
        filters: FilterOnlyProcessor,
        subtitles: SubtitleHandler,
        frames: FrameLevelProcessor,
        archive: IDownloadArchiveTarget,
    ) -> None:
        self._prober = prober
        self._filters = filters
        self._subtitles = subtitles
        self._frames = frames
        self._archive = archive

    def execute_item(
        self,
        src: Path,
        ops: PreprocessOps,
        on_progress: ProgressFn | None,
        token: CancellationToken | None = None,
    ) -> Path:
        probe = self._prober.probe(src)          # MED002/MED003 直接上抛
        # 输出命名 <原名>_cleaned.mp4 同目录（完成标准强制 MP4 后缀）
        out = self._archive.mirror_path_for_output(src, "_cleaned").with_suffix(".mp4")
        if out.exists():
            raise SkippedSignal(f"{out.name} 已存在，跳过重复处理")

        route = self._subtitles.route(probe, ops.remove_subtitle_mode)
        path_kind = decide_path(ops, bool(probe.soft_subtitle_codec), route)
        if path_kind == "skip":
            # 结果语义：仅勾选去字幕但软硬均未检出 → skipped 不报错
            raise SkippedSignal("未检出需要处理的字幕或水印，已跳过")

        run_token = token if token is not None else CancellationToken()

        def writer(tmp_target: Path) -> None:
            if path_kind == "frame":
                self._frames.process(src, probe, ops, tmp_target, on_progress, run_token)
            elif path_kind == "filter":
                self._filters.run(src, probe, ops, tmp_target, on_progress, run_token)
            else:   # remux：软字幕剥离（可同时去原声）
                self._subtitles.strip_to(
                    src, tmp_target, drop_audio=ops.strip_audio, token=run_token
                )

        from ych.common.fsutil import SafeFileOps

        SafeFileOps.atomic_write(out, writer)

        # 结果语义 success：落盘且 ffprobe 可读
        self._prober.probe(out)
        logger.info("预处理完成：%s → %s", src.name, out.name)
        return out


def handle_preprocess_task(
    task: object,
    pipeline: PreprocessPipeline,
    fails: FailRecordManager,
) -> TaskResult:
    """M2 批量执行体（注册到 M4 type=preprocess；13.3 部分失败隔离）。

    单条 AppError 捕获 → 失败列表 → continue；
    SkippedSignal 计入 skipped；其余异常上抛交 M4 兜底。
    """
    payload: TaskPayload = task.payload   # type: ignore[attr-defined]
    items_raw = cast(list[object], payload.data.get("items") or [])
    outputs: list[str] = []
    failed = 0
    skipped = 0
    token: CancellationToken | None = getattr(task, "token", None)

    for raw in items_raw:
        if not isinstance(raw, dict):
            continue
        src = Path(str(raw.get("src", "")))
        try:
            ops = revive_ops(raw.get("ops") or {})
            outputs.append(str(pipeline.execute_item(src, ops, None, token)))
        except SkippedSignal as exc:
            logger.info("跳过 %s：%s", src.name, exc)
            skipped += 1
        except AppError as exc:
            failed += 1
            single_payload = TaskPayload(type="preprocess", data={"items": [raw]})
            fails.record(SimpleTask(single_payload), exc)
            logger.warning("预处理失败 %s：[%s] %s", src.name, exc.code, exc.message)

    first = outputs[0] if len(outputs) == 1 else (outputs[0] if outputs else None)
    return TaskResult(
        summary={"outputs": outputs, "failed": failed, "skipped": skipped},
        output_path=first,
    )


class SimpleTask:
    """失败记录用最小任务替身（FailRecordManager.record 只读 payload）。"""

    def __init__(self, payload: TaskPayload) -> None:
        self.payload = payload

