# ONNX 会话懒加载缓存（详设 9.3）；CPU 基准，模型缺失/损坏走 AI001/AI002
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Literal

import onnxruntime as ort

from ych.common.errors import ERR_AI_MODEL_LOAD_FAILED, ERR_AI_MODEL_MISSING, AppError

logger = logging.getLogger("ych.s2")

ModelName = Literal["watermark", "subtitle", "inpaint", "clip"]

_MODEL_FILES: dict[str, str] = {
    "watermark": "watermark_det_640.onnx",
    "subtitle": "subtitle_det_ppocrv4_mobile.onnx",
    "inpaint": "lama_fp32_512.onnx",
    "clip": "clip_vitb32_image.onnx",
}

# DML 白名单（2026-10 RTX 4060 实测，scripts 临时环境四模型基准）：
#   subtitle 41.7x / clip 30.6x；watermark DML 反而慢 4 倍、lama FFC 算子
#   DML 直接报错——故按模型分流，白名单外恒走 CPU。
# 会话构建失败或运行期 DML 报错由调用方既有降级路径兜底（不阻断处理）。
_DML_MODELS = frozenset({"subtitle", "clip"})


def _providers_for(name: str) -> list[str]:
    """按模型选择执行提供者：白名单模型优先 DML（可用时），其余恒 CPU。"""
    if name in _DML_MODELS and "DmlExecutionProvider" in ort.get_available_providers():
        return ["DmlExecutionProvider", "CPUExecutionProvider"]
    return ["CPUExecutionProvider"]


def models_dir() -> Path:
    """runtime/models 目录（仓库根；打包后随包定位同目录）。"""
    return Path(__file__).resolve().parents[4] / "runtime" / "models"


def intra_op_threads() -> int:
    """物理核-2，下限 2（详设 9.3）。DML 会话忽略该配置，无害。"""
    cores = os.cpu_count() or 4
    return max(2, cores - 2)


class ModelRegistry:
    """会话懒加载 + 缓存；同一模型只加载一次。"""

    def __init__(self, base_dir: Path | None = None) -> None:
        self._dir = base_dir if base_dir is not None else models_dir()
        self._sessions: dict[str, ort.InferenceSession] = {}
        # 双检锁：处理并发 ≥2 时两 worker 首次同用一模型，防止重复构建会话
        self._lock = threading.Lock()

    def model_path(self, name: ModelName) -> Path:
        return self._dir / _MODEL_FILES[name]

    def session(self, name: ModelName) -> ort.InferenceSession:
        """获取推理会话；缺失 AI001 / 加载失败 AI002（不崩溃由调用方降级）。"""
        cached = self._sessions.get(name)
        if cached is not None:
            return cached
        with self._lock:
            if name in self._sessions:
                return self._sessions[name]
            path = self.model_path(name)
            if not path.exists():
                raise AppError(ERR_AI_MODEL_MISSING, f"模型文件缺失：{path.name}")
            options = ort.SessionOptions()
            options.intra_op_num_threads = intra_op_threads()
            providers = _providers_for(name)
            try:
                sess = ort.InferenceSession(
                    str(path), sess_options=options,
                    providers=providers,
                )
            except Exception as exc:
                # DML 初始化失败（驱动/算子不支持）→ 退回纯 CPU 重试一次
                if providers != ["CPUExecutionProvider"]:
                    logger.warning(
                        "模型 %s GPU 初始化失败，退回 CPU：%s", path.name, exc,
                    )
                    try:
                        sess = ort.InferenceSession(
                            str(path), sess_options=options,
                            providers=["CPUExecutionProvider"],
                        )
                    except Exception as exc2:
                        raise AppError(
                            ERR_AI_MODEL_LOAD_FAILED,
                            f"模型加载失败：{path.name}",
                            cause=exc2,
                        ) from exc2
                else:
                    raise AppError(
                        ERR_AI_MODEL_LOAD_FAILED,
                        f"模型加载失败：{path.name}",
                        cause=exc,
                    ) from exc
            self._sessions[name] = sess
            logger.info("model loaded: %s（%s）", path.name,
                        "GPU" if providers != ["CPUExecutionProvider"] else "CPU")
            return sess

    def clear(self) -> None:
        """清空缓存（测试用）。"""
        self._sessions.clear()
