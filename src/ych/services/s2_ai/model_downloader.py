# 模型下载器（详设 9.3 按需下载）：清单驱动 + 多候选源 + sha256 + 断点续传。
# 复用 S4 HttpClient.download_stream（Range 续传/ETag 一致性由其实现）。
# 下载完成后做 I/O 契约校验（输入名/输出维度），不兼容即删除报错，避免坏模型混入。
from __future__ import annotations

import hashlib
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ych.common.cancellation import CancellationToken, TaskCanceled
from ych.common.errors import (
    ERR_AI_MODEL_MISSING,
    ERR_FILE_NO_WRITE_PERMISSION,
    AppError,
)
from ych.common.schemas import ResumeState
from ych.services.s2_ai.model_registry import ModelName, models_dir
from ych.services.s4_net.http_client import HttpClient

logger = logging.getLogger("ych.s2")


@dataclass(frozen=True)
class ModelSpec:
    key: ModelName
    file: str
    desc: str
    urls: list[str] = field(default_factory=list)   # 依序尝试；空 = 无自动下载源
    sha256: str = ""                                # 空 = 不校验哈希


# 候选源均为社区镜像（HF 官方无现成 ONNX）。校验不通过会自动删除并明确报错，
# 因此列出不兼容的候选源是安全的。
MODEL_MANIFEST: dict[str, ModelSpec] = {
    "subtitle": ModelSpec(
        key="subtitle", file="subtitle_det_ppocrv4_mobile.onnx",
        desc="字幕检测（DBNet 文本检测，去字幕=自动检测 必需）",
        urls=[
            # SWHL/RapidOCR 仓库里 det 模型直接放在版本目录下（无 det/ 子目录，
            # 写成 PP-OCRv4/det/... 会 404）；RapidAI/RapidOCR 仓库已转为
            # 鉴权仓库，不能再作候选源
            "https://hf-mirror.com/SWHL/RapidOCR/resolve/main/PP-OCRv4/ch_PP-OCRv4_det_infer.onnx",
            "https://hf-mirror.com/SWHL/RapidOCR/resolve/main/PP-OCRv3/ch_PP-OCRv3_det_infer.onnx",
        ],
    ),
    "inpaint": ModelSpec(
        key="inpaint", file="lama_fp32_512.onnx",
        desc="图像修复 LaMa（大面积水印修复；缺失自动降级 TELEA）",
        # 仓库曾提供 lama_fp32_512.onnx，现已下架，仅剩 lama_fp32.onnx
        # （固定 512 输入，与推理端 _INPAINT_TILE 契约一致，已实测通过校验）
        urls=[
            "https://hf-mirror.com/Carve/LaMa-ONNX/resolve/main/lama_fp32.onnx",
        ],
    ),
    # watermark 为项目自训练 YOLO（输出已带 NMS 的 (N,6) 契约），无公开同构模型；
    # clip 需要与本地预处理/嵌入契约一致的 ViT-B/32 图像塔导出，公开导出输入名不统一。
    "watermark": ModelSpec(
        key="watermark", file="watermark_yolov8n_640.onnx",
        desc="水印检测（自训练；缺失自动降级模板匹配，无需下载）",
        urls=[],
    ),
    "clip": ModelSpec(
        key="clip", file="clip_vitb32_image.onnx",
        desc="CLIP 图像嵌入（重复度分析；缺失自动降级经典特征，无需下载）",
        urls=[],
    ),
}


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class ModelDownloader:
    """按清单下载/校验模型；纯逻辑无 Qt 依赖，UI 经 LlmWorker 调用。"""

    def __init__(
        self, http: HttpClient, base_dir: Path | None = None,
    ) -> None:
        self._http = http
        self._dir = base_dir if base_dir is not None else models_dir()
        self.progress: dict[str, float] = {}    # key → 0~1（供 UI 轮询，GIL 安全）

    def spec(self, key: str) -> ModelSpec:
        return MODEL_MANIFEST[key]

    def all_specs(self) -> list[ModelSpec]:
        return list(MODEL_MANIFEST.values())

    def exists(self, key: str) -> bool:
        return self.path_of(key).exists()

    def path_of(self, key: str) -> Path:
        return self._dir / MODEL_MANIFEST[key].file

    def size_of(self, key: str) -> int:
        p = self.path_of(key)
        return p.stat().st_size if p.exists() else 0

    def download(
        self,
        key: str,
        token: CancellationToken | None = None,
    ) -> Path:
        """下载 → sha256 校验（如有）→ 契约校验 → 原子落位。返回模型路径。"""
        spec = MODEL_MANIFEST[key]
        if not spec.urls:
            raise AppError(
                ERR_AI_MODEL_MISSING,
                f"{spec.file} 暂无自动下载源：请手动放置到 {self._dir}",
            )
        self._dir.mkdir(parents=True, exist_ok=True)
        dest = self._dir / spec.file
        part = Path(str(dest) + ".part")
        last_exc: Exception | None = None
        prev_url: str | None = None

        for url in spec.urls:
            if prev_url is not None and url != prev_url:
                # 候选源是不同文件（如 v4/v3 检测模型）：跨源续传会把两个
                # 模型拼接成损坏文件，切换源必须丢弃前一个源的 .part
                part.unlink(missing_ok=True)
            prev_url = url
            try:
                self.progress[key] = 0.0
                resume = self._resume_for(part)
                self._http.download_stream(
                    url, dest, resume,
                    on_progress=lambda ratio: self.progress.__setitem__(
                        key, min(1.0, max(0.0, ratio))),
                    token=token,
                )
            except TaskCanceled:
                # 保留 .part：同源重试/重启应用可续传
                raise
            except OSError as exc:
                # 磁盘满/无写权限等：映射 FILE 域错误码（.part 保留可续传）
                logger.warning("模型写盘失败 %s：%s", url, exc)
                last_exc = AppError(
                    ERR_FILE_NO_WRITE_PERMISSION,
                    "模型写入失败（磁盘空间不足或无写权限）",
                    cause=exc,
                )
                continue
            except AppError as exc:
                logger.warning("模型源失败 %s：%s", url, exc.message)
                last_exc = exc
                continue
            # 完整性：优先 sha256，否则做 I/O 契约校验
            try:
                self._verify(spec, part)
            except AppError as exc:
                logger.warning("模型校验失败 %s：%s", url, exc.message)
                part.unlink(missing_ok=True)
                last_exc = exc
                continue
            part.replace(dest)
            self.progress[key] = 1.0
            logger.info("model downloaded: %s <- %s", spec.file, url)
            return dest
        self.progress.pop(key, None)
        raise last_exc or AppError(ERR_AI_MODEL_MISSING, "所有下载源均失败")

    def import_file(self, key: str, src: Path) -> Path:
        """手动导入模型（无公开下载源的自训练模型）：复制 → 契约校验 → 落位。

        校验不通过即删除临时副本并报错，坏文件不会混入模型目录。
        """
        spec = MODEL_MANIFEST[key]
        if not src.is_file():
            raise AppError(ERR_AI_MODEL_MISSING, f"文件不存在：{src}")
        self._dir.mkdir(parents=True, exist_ok=True)
        dest = self._dir / spec.file
        tmp = Path(f"{dest}.importing")
        try:
            shutil.copyfile(src, tmp)
        except OSError as exc:
            raise AppError(
                ERR_FILE_NO_WRITE_PERMISSION,
                "模型复制失败（磁盘空间不足或无写权限）",
                cause=exc,
            ) from exc
        try:
            self._verify(spec, tmp)
        except AppError:
            tmp.unlink(missing_ok=True)
            raise
        tmp.replace(dest)
        logger.info("model imported: %s <- %s", spec.file, src)
        return dest

    def _resume_for(self, part: Path) -> ResumeState:
        if part.exists():
            return ResumeState(
                downloaded_bytes=part.stat().st_size, etag="",
                total_bytes=0, temp_path=str(part),
            )
        return ResumeState(downloaded_bytes=0, etag="", total_bytes=0,
                           temp_path=str(part))

    def _verify(self, spec: ModelSpec, part: Path) -> None:
        if spec.sha256:
            got = _sha256_of(part)
            if got != spec.sha256:
                raise AppError(ERR_AI_MODEL_MISSING,
                               f"哈希不匹配（{got[:12]}…），已丢弃")
        try:
            self._check_contract(spec.key, part)
        except AppError:
            raise
        except Exception as exc:
            raise AppError(ERR_AI_MODEL_MISSING,
                           "模型文件损坏或格式不支持", cause=exc) from exc

    def _check_contract(self, key: str, path: Path) -> None:
        """下载后即时校验 I/O 契约，与 LocalProvider 的调用方式一致。"""
        import onnxruntime as ort

        options = ort.SessionOptions()
        sess = ort.InferenceSession(str(path), sess_options=options,
                                    providers=["CPUExecutionProvider"])
        input_names = [i.name for i in sess.get_inputs()]
        output_shapes = [o.shape for o in sess.get_outputs()]

        def feed(hw: int) -> dict[str, np.ndarray[tuple[int, ...], np.dtype[np.float32]]]:
            """按模型声明的输入形状构造全零输入（动态维取 hw，静态维照抄）。"""
            out: dict[str, np.ndarray[tuple[int, ...], np.dtype[np.float32]]] = {}
            for i in sess.get_inputs():
                shape = []
                for j, d in enumerate(i.shape):
                    if isinstance(d, int) and d > 0:
                        shape.append(d)
                    else:
                        shape.append(hw if j >= 1 else 1)
                out[i.name] = np.zeros(shape, dtype=np.float32)
            return out

        if key == "subtitle":
            prob = sess.run(None, feed(64))[0]
            if np.asarray(prob).ndim != 4:
                raise ValueError(f"字幕模型输出应为概率图，实际 {output_shapes}")
        elif key == "inpaint":
            if "image" not in input_names or "mask" not in input_names:
                raise ValueError(f"修复模型输入名需为 image/mask，实际 {input_names}")
            out = sess.run(None, {
                "image": np.zeros((1, 3, 512, 512), dtype=np.float32),
                "mask": np.zeros((1, 1, 512, 512), dtype=np.float32),
            })[0]
            if np.asarray(out).ndim != 4:
                raise ValueError(f"修复模型输出维度异常 {output_shapes}")
        elif key == "clip":
            out = sess.run(None, feed(224))[0]
            if np.asarray(out).shape[-1] != 512:
                raise ValueError(
                    f"CLIP 嵌入维度需为 512，实际 {np.asarray(out).shape}")
        elif key == "watermark":
            out = sess.run(None, feed(640))[0]
            if np.asarray(out)[0].ndim != 2 or np.asarray(out)[0].shape[-1] < 5:
                raise ValueError(f"水印模型输出需为 (N,≥5)，实际 {output_shapes}")
