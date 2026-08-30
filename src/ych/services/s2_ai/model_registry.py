# ONNX 会话懒加载缓存（详设 9.3）；CPU 基准，模型缺失/损坏走 AI001/AI002
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Literal

import onnxruntime as ort

from ych.common.errors import ERR_AI_MODEL_LOAD_FAILED, ERR_AI_MODEL_MISSING, AppError

logger = logging.getLogger("ych.s2")

ModelName = Literal["watermark", "subtitle", "inpaint", "clip"]

_MODEL_FILES: dict[str, str] = {
    "watermark": "watermark_yolov8n_640.onnx",
    "subtitle": "subtitle_det_ppocrv4_mobile.onnx",
    "inpaint": "lama_fp32_512.onnx",
    "clip": "clip_vitb32_image.onnx",
}


def models_dir() -> Path:
    """runtime/models 目录（仓库根；打包后随包定位同目录）。"""
    return Path(__file__).resolve().parents[4] / "runtime" / "models"


def intra_op_threads() -> int:
    """物理核-2，下限 2（详设 9.3）。"""
    cores = os.cpu_count() or 4
    return max(2, cores - 2)


class ModelRegistry:
    """会话懒加载 + 缓存；同一模型只加载一次。"""

    def __init__(self, base_dir: Path | None = None) -> None:
        self._dir = base_dir if base_dir is not None else models_dir()
        self._sessions: dict[str, ort.InferenceSession] = {}

    def model_path(self, name: ModelName) -> Path:
        return self._dir / _MODEL_FILES[name]

    def session(self, name: ModelName) -> ort.InferenceSession:
        """获取推理会话；缺失 AI001 / 加载失败 AI002（不崩溃由调用方降级）。"""
        if name in self._sessions:
            return self._sessions[name]
        path = self.model_path(name)
        if not path.exists():
            raise AppError(ERR_AI_MODEL_MISSING, f"模型文件缺失：{path.name}")
        options = ort.SessionOptions()
        options.intra_op_num_threads = intra_op_threads()
        try:
            sess = ort.InferenceSession(
                str(path), sess_options=options,
                providers=["CPUExecutionProvider"],
            )
        except Exception as exc:
            raise AppError(
                ERR_AI_MODEL_LOAD_FAILED,
                f"模型加载失败：{path.name}",
                cause=exc,
            ) from exc
        self._sessions[name] = sess
        logger.info("model loaded: %s", path.name)
        return sess

    def clear(self) -> None:
        """清空缓存（测试用）。"""
        self._sessions.clear()
