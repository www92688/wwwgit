# 预处理流水线入口（详设 13.2/13.3）：路径分派、原子收尾、批量 handler
from __future__ import annotations

import logging
from collections.abc import Callable
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
from ych.services.s2_ai.postprocess import RegionSpan

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
        # 输出命名 <原名>_cleaned.mp4，统一放原视频同目录的「预处理」文件夹
        # （完成标准强制 MP4 后缀）；与原素材不混放，便于查找
        out = self._archive.mirror_path_for_output(src, "_cleaned").with_suffix(".mp4")
        if out.exists():
            raise SkippedSignal(f"{out.name} 已存在，跳过重复处理")
        out.parent.mkdir(parents=True, exist_ok=True)

        route = self._subtitles.route(probe, ops.remove_subtitle_mode)
        path_kind = decide_path(ops, bool(probe.soft_subtitle_codec), route)
        if path_kind == "skip":
            # 结果语义：仅勾选去字幕但软硬均未检出 → skipped 不报错
            raise SkippedSignal("未检出需要处理的字幕或水印，已跳过")

        run_token = token if token is not None else CancellationToken()

        # 帧级预检：检测不到任何修复区域时，全片逐帧解码重编码是纯浪费。
        # 沿 decide_path 阶梯按剩余处理项降级——单趟滤镜/remux 与帧级路径
        # 用同一 EncoderSpec，一遍全质量转码，画质不降级；若除修复外无任何
        # 处理项，产物内容与原片相同 → 直接跳过不产出。
        spans: list[RegionSpan] | None = None
        if path_kind == "frame":
            spans = self._frames.detect_spans(src, probe, ops, run_token, None)
            if not spans:
                if ops.crop_rect is not None or ops.aspect_target is not None:
                    logger.info("未检出修复区域，改走滤镜单趟：%s", src.name)
                    path_kind = "filter"
                elif ops.strip_audio:
                    logger.info("未检出修复区域，改走 remux 去原声：%s", src.name)
                    path_kind = "remux"
                else:
                    raise SkippedSignal("未检出需要处理的字幕或水印，已跳过")

        def writer(tmp_target: Path) -> None:
            if path_kind == "frame":
                self._frames.process(
                    src, probe, ops, tmp_target, on_progress, run_token,
                    spans=spans,
                )
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
    progress: Callable[[float], None] | None = None,
) -> TaskResult:
    """M2 批量执行体（注册到 M4 type=preprocess；13.3 部分失败隔离）。

    progress 为可选的批次进度上报 (0~1)，单条内部进度按 (idx+ratio)/total
    折算；单条 AppError 捕获 → 失败列表 → continue；
    SkippedSignal 计入 skipped（含文件名，供终态提示解释原因）；
    其余异常上抛交 M4 兜底。
    """
    import time

    payload: TaskPayload = task.payload   # type: ignore[attr-defined]
    items_raw = cast(list[object], payload.data.get("items") or [])
    outputs: list[str] = []
    failed = 0
    skipped = 0
    skipped_names: list[str] = []
    failed_msgs: list[str] = []
    token: CancellationToken | None = getattr(task, "token", None)
    total = max(1, len(items_raw))
    # 产物目录：成功时为产物所在目录；全部跳过时产物在原文件同目录，
    # 取首条素材目录兜底（终态提示"查看产物"用）
    output_dir = ""

    def emit(ratio: float) -> None:
        if progress is not None:
            progress(max(0.0, min(1.0, ratio)))

    def progress_for(idx: int) -> ProgressFn:
        last = [0.0]

        def on_progress(ratio: float) -> None:
            now = time.monotonic()
            if ratio < 1.0 and now - last[0] < 0.25:
                return
            last[0] = now
            emit((idx + ratio) / total)

        return on_progress

    emit(0.0)
    for idx, raw in enumerate(items_raw):
        if not isinstance(raw, dict):
            continue
        src = Path(str(raw.get("src", "")))
        try:
            ops = revive_ops(raw.get("ops") or {})
            out = str(pipeline.execute_item(src, ops, progress_for(idx), token))
            outputs.append(out)
            output_dir = str(Path(out).parent)
        except SkippedSignal as exc:
            logger.info("跳过 %s：%s", src.name, exc)
            skipped += 1
            skipped_names.append(src.name)
            if not output_dir:
                # 产物在原视频同目录的「预处理」文件夹（现存文件就在那）
                output_dir = str(src.parent / "预处理")
        except AppError as exc:
            failed += 1
            single_payload = TaskPayload(type="preprocess", data={"items": [raw]})
            fails.record(SimpleTask(single_payload), exc)
            failed_msgs.append(f"{src.name}：{exc.message}")
            logger.warning("预处理失败 %s：[%s] %s", src.name, exc.code, exc.message)
        finally:
            emit((idx + 1) / total)

    first = outputs[0] if len(outputs) == 1 else (outputs[0] if outputs else None)
    return TaskResult(
        summary={"outputs": outputs, "failed": failed, "skipped": skipped,
                 "skipped_names": skipped_names, "failed_msgs": failed_msgs,
                 "output_dir": output_dir},
        output_path=first,
    )


class SimpleTask:
    """失败记录用最小任务替身（FailRecordManager.record 只读 payload）。"""

    def __init__(self, payload: TaskPayload) -> None:
        self.payload = payload

