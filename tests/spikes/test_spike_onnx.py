# 冒烟②：onnxruntime CPU 会话可建立推理（D1 替身冒烟，对应 SP-2/SP-3 工程前提）
# 真实模型权重缺失（无人工资源下载），按 D1 走「捕获加载失败降级」路径：
# 验证包可用 + CPU provider 在位 + 模型缺失时异常可被捕获（即生产代码
# AI001/AI002 的触发路径在本机可复现）。
import pytest


def test_onnxruntime_import_and_cpu_provider():
    import onnxruntime as ort

    providers = ort.get_available_providers()
    assert "CPUExecutionProvider" in providers, (
        f"CPU 执行提供者不可用: {providers}"
    )
    # 会话选项 API 可用（生产 ModelRegistry 需要 intra_op 线程控制）
    so = ort.SessionOptions()
    assert hasattr(so, "intra_op_num_threads")


def test_onnxruntime_missing_model_fails_gracefully(tmp_path):
    import onnxruntime as ort

    missing = tmp_path / "no_such_model.onnx"
    with pytest.raises(Exception) as exc_info:
        ort.InferenceSession(
            str(missing), providers=["CPUExecutionProvider"]
        )
    # 加载失败必须抛出明确异常（供上层映射 AI001/AI002 降级，不静默崩溃）
    assert exc_info.value is not None
