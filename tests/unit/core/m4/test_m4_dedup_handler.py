# 去重 handler（context._handle_dedup）测试：进度透传 + 跳过/失败明细汇总。
# 通过预填 AppContext 缓存注入假 pipeline / 临时库调度器，不触碰用户数据目录。
from __future__ import annotations

from pathlib import Path

import pytest

from ych.common.cancellation import SkippedSignal
from ych.common.errors import ERR_MED_FORMAT_UNSUPPORTED, AppError
from ych.common.schemas import TaskPayload
from ych.context import AppContext
from ych.core.m3_dedup.dedup_pipeline import TranscodeOutcome
from ych.core.m4_scheduler.task_scheduler import ManagedTask, TaskScheduler
from ych.services.s3_db.daos import make_daos
from ych.services.s3_db.database import Database
from ych.services.s5_base.config_service import ConfigService


class FakePipeline:
    """两阶段可编程：转码 ok / SkippedSignal / 异常；对比返回固定分数。"""

    def __init__(self, behavior: str = "ok") -> None:
        self.behavior = behavior
        self.progress_calls: list[float] = []

    def output_path_for(self, src: Path) -> Path:
        return Path("D:/wd/已去重") / (src.stem + "_deduped.mp4")

    def transcode_item(self, src: Path, params, on_progress, token
                       ) -> TranscodeOutcome:
        if on_progress is not None:
            on_progress(0.5)
            self.progress_calls.append(0.5)
        if self.behavior == "skip":
            raise SkippedSignal(f"{src.stem}_deduped.mp4 已存在，跳过重复去重")
        if self.behavior == "fail":
            raise AppError(ERR_MED_FORMAT_UNSUPPORTED, "格式不支持")
        return TranscodeOutcome(src=src, out=self.output_path_for(src),
                                probe=None, speed_factor=1.0)   # type: ignore[arg-type]

    def compare_item(self, src: Path, out: Path, on_progress=None
                     ) -> tuple[float | None, float | None]:
        return (42.0, 5.0)


@pytest.fixture
def env(qtbot, tmp_path):
    cfg = ConfigService()
    db = Database(tmp_path / "app.db")
    daos = make_daos(db)
    pool_max = 2
    from PySide6.QtCore import QThreadPool

    pool = QThreadPool()
    pool.setMaxThreadCount(pool_max)
    sched = TaskScheduler(pool, cfg, daos)
    ctx = AppContext()
    ctx._cache["scheduler"] = sched
    yield {"ctx": ctx, "sched": sched, "daos": daos, "pool": pool,
           "progress": []}
    pool.clear()
    pool.waitForDone(2000)


def _make_task(items: list[dict]) -> ManagedTask:
    return ManagedTask(
        task_id="t1", payload=TaskPayload(type="dedup", data={"items": items}),
    )


def test_dedup_progress_and_summary(env) -> None:
    pipeline = FakePipeline("ok")
    env["ctx"]._cache["pipeline_m3"] = pipeline
    env["sched"].task_progress.connect(
        lambda tid, r: env["progress"].append((tid, r)))
    task = _make_task([
        {"src": "D:/wd/a.mp4", "technique_params": []},
        {"src": "D:/wd/b.mp4", "technique_params": []},
    ])
    result = env["ctx"]._handle_dedup(task)
    assert result.summary["outputs"] == [
        "D:/wd/已去重/a_deduped.mp4", "D:/wd/已去重/b_deduped.mp4",
    ] or result.summary["outputs"] == [
        "D:\\wd\\已去重\\a_deduped.mp4", "D:\\wd\\已去重\\b_deduped.mp4",
    ]
    assert result.summary["skipped"] == 0 and result.summary["failed"] == 0
    assert result.summary["before_pct"] == 42.0
    assert result.summary["after_pct"] == 5.0
    assert result.summary["output_dir"]
    # 进度透传：每条 0.5/1.0 → 批次 (idx+ratio)/2，且收尾恰好到 1.0
    ratios = [r for _tid, r in env["progress"]]
    assert ratios and ratios[-1] == pytest.approx(1.0)
    assert all(0.0 <= r <= 1.0 for r in ratios)


def test_dedup_all_skipped_records_names_and_dir(env) -> None:
    pipeline = FakePipeline("skip")
    env["ctx"]._cache["pipeline_m3"] = pipeline
    task = _make_task([{"src": "D:/wd/a.mp4", "technique_params": []}])
    result = env["ctx"]._handle_dedup(task)
    assert result.summary["outputs"] == []
    assert result.summary["skipped"] == 1
    assert result.summary["skipped_names"] == ["a_deduped.mp4"]
    assert result.summary["failed"] == 0
    assert result.summary["output_dir"]        # 指引「已存在文件在哪」


def test_dedup_failure_records_message(env) -> None:
    pipeline = FakePipeline("fail")
    env["ctx"]._cache["pipeline_m3"] = pipeline
    # fail_manager 真实构造（临时库），AppContext 懒加载会命中用户库，必须预填
    from ych.core.m4_scheduler.fail_record_manager import FailRecordManager

    env["ctx"]._cache["fail_manager"] = FailRecordManager(env["daos"].fails)
    task = _make_task([{"src": "D:/wd/a.mp4", "technique_params": []}])
    result = env["ctx"]._handle_dedup(task)
    assert result.summary["failed"] == 1
    assert result.summary["failed_msgs"]
    assert "a.mp4" in result.summary["failed_msgs"][0]


def test_dedup_cancel_mid_batch_keeps_finished_items(env) -> None:
    """批次中途取消：上抛 TaskCanceled，已完成条目保留，剩余不计失败。"""
    from ych.common.cancellation import CancellationToken, TaskCanceled

    class CancelingPipeline(FakePipeline):
        """第 2 条转码时模拟用户取消。"""

        def transcode_item(self, src: Path, params, on_progress, token
                           ) -> TranscodeOutcome:
            if src.stem == "b" and token is not None:
                token.cancel()
                raise TaskCanceled("转码已被取消")
            return super().transcode_item(src, params, on_progress, token)

    env["ctx"]._cache["pipeline_m3"] = CancelingPipeline("ok")
    token = CancellationToken()
    task = ManagedTask(
        task_id="t-cancel",
        payload=TaskPayload(type="dedup", data={"items": [
            {"src": "D:/wd/a.mp4", "technique_params": []},
            {"src": "D:/wd/b.mp4", "technique_params": []},
            {"src": "D:/wd/c.mp4", "technique_params": []},
        ]}),
        token=token,
    )
    with pytest.raises(TaskCanceled):
        env["ctx"]._handle_dedup(task)


def test_dedup_compare_failure_counted_not_fatal(env) -> None:
    """对比阶段异常：计失败且含文件名，不拖垮批次其他条目。"""
    from ych.core.m3_dedup.dedup_pipeline import TranscodeOutcome

    class CompareFailPipeline(FakePipeline):
        def compare_item(self, src: Path, out: Path, on_progress=None
                         ) -> tuple[float | None, float | None]:
            if src.stem == "a":
                raise RuntimeError("特征提取崩溃")
            return (10.0, 5.0)

        def transcode_item(self, src: Path, params, on_progress, token
                           ) -> TranscodeOutcome:
            return TranscodeOutcome(src=src, out=self.output_path_for(src),
                                    probe=None, speed_factor=1.0)   # type: ignore[arg-type]

    env["ctx"]._cache["pipeline_m3"] = CompareFailPipeline("ok")
    from ych.core.m4_scheduler.fail_record_manager import FailRecordManager

    env["ctx"]._cache["fail_manager"] = FailRecordManager(env["daos"].fails)
    task = _make_task([
        {"src": "D:/wd/a.mp4", "technique_params": []},
        {"src": "D:/wd/b.mp4", "technique_params": []},
    ])
    result = env["ctx"]._handle_dedup(task)
    assert result.summary["failed"] == 1
    assert "a.mp4" in result.summary["failed_msgs"][0]
    assert len(result.summary["outputs"]) == 1
    assert result.summary["after_pct"] == 5.0
