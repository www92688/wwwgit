"""interfaces 单元测试：协议可导入、签名存在、且不引入 Qt。"""
from __future__ import annotations

import inspect
import sys

from ych.core.interfaces import IDownloadArchiveTarget, ITaskSchedulerProtocol


class TestProtocols:
    def test_scheduler_protocol_methods(self) -> None:
        for name in ("submit", "cancel", "register_handler"):
            assert hasattr(ITaskSchedulerProtocol, name), f"缺少方法 {name}"
            assert callable(getattr(ITaskSchedulerProtocol, name))

    def test_archive_target_method(self) -> None:
        assert callable(IDownloadArchiveTarget.archive_download)

    def test_submit_signature_uses_task_payload(self) -> None:
        sig = inspect.signature(ITaskSchedulerProtocol.submit)
        params = list(sig.parameters.values())
        assert params[1].name == "payload"
        assert params[2].name == "priority" and params[2].default == 0

    def test_register_handler_signature(self) -> None:
        sig = inspect.signature(ITaskSchedulerProtocol.register_handler)
        assert list(sig.parameters) == ["self", "task_type", "handler"]

    def test_no_qt_import(self) -> None:
        """协议文件禁止引入 Qt（防 UI 依赖渗透进 core）。"""
        module = sys.modules["ych.core.interfaces"]
        source = inspect.getsource(module)
        assert "PySide6" not in source and "QtCore" not in source
