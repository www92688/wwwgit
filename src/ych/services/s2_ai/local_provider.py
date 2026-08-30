# LocalProvider（详设 9.3）：本地四类推理实现 + D2 降级兜底
from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np
import numpy.typing as npt

from ych.common.errors import AppError
from ych.common.schemas import BBox
from ych.services.s2_ai.model_registry import ModelRegistry
from ych.services.s2_ai.postprocess import merge_boxes, nms
from ych.services.s2_ai.provider import Detection
from ych.services.s2_ai.template_matcher import TemplateMatcher

logger = logging.getLogger("ych.s2")

_WM_CONF_THRESHOLD = 0.45
_WM_NMS_IOU = 0.45
_SUB_MERGE_IOU = 0.3
_INPAINT_TILE = 512
_SMALL_PATCH_PX = 32
# CLIP 归一化常量（OpenAI CLIP ViT-B/32）
_CLIP_MEAN = (0.48145466, 0.4578275, 0.40821073)
_CLIP_STD = (0.26862954, 0.26130258, 0.27577711)


class LocalProvider:
    """ONNX 本地推理；模型缺失/失败自动降级，功能不崩溃（D2）。"""

    def __init__(self, registry: ModelRegistry, templates_dir: Path | None = None) -> None:
        self._registry = registry
        self._matcher = TemplateMatcher(templates_dir) if templates_dir else None

    # ---- 内部：letterbox 到方形输入 ----
    @staticmethod
    def _letterbox(
        frame: npt.NDArray[np.uint8], size: int
    ) -> tuple[npt.NDArray[np.float32], float, tuple[float, float]]:
        h, w = frame.shape[:2]
        scale = min(size / w, size / h)
        nw, nh = round(w * scale), round(h * scale)
        resized = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_LINEAR)
        canvas = np.full((size, size, 3), 114, dtype=np.uint8)
        pad_x = (size - nw) // 2
        pad_y = (size - nh) // 2
        canvas[pad_y:pad_y + nh, pad_x:pad_x + nw] = resized
        arr = canvas.astype(np.float32) / 255.0
        return np.ascontiguousarray(arr.transpose(2, 0, 1)), scale, (pad_x, pad_y)

    # ---- 水印检测 ----
    def detect_watermark(
        self, frames: list[tuple[float, npt.NDArray[np.uint8]]]
    ) -> list[list[Detection]]:
        """YOLO 检测 → conf≥0.45 → NMS(0.45) → 反算坐标；
        结果不足且模板库可用时 TemplateMatcher 补充；模型缺失走纯模板兜底。"""
        per_frame: list[list[Detection]] = []
        try:
            sess = self._registry.session("watermark")
        except AppError as exc:
            logger.warning("水印检测模型不可用（%s），降级 TemplateMatcher", exc.code)
            sess = None

        for ts, frame in frames:
            dets: list[Detection] = []
            if sess is not None:
                blob, scale, (pad_x, pad_y) = self._letterbox(frame, 640)
                input_name = sess.get_inputs()[0].name
                outputs = sess.run(None, {input_name: blob[None].astype(np.float32)})
                preds = np.asarray(outputs[0])[0]   # (N, 6)
                h, w = frame.shape[:2]
                for row in preds:
                    cx, cy, bw, bh, conf = (float(v) for v in row[:5])
                    if conf < _WM_CONF_THRESHOLD:
                        continue
                    # 反 letterbox 回原分辨率归一化坐标
                    px = (cx - pad_x) / scale / w
                    py = (cy - pad_y) / scale / h
                    pw = bw / scale / w
                    ph = bh / scale / h
                    dets.append(Detection(
                        bbox=BBox(x=max(0.0, px - pw / 2), y=max(0.0, py - ph / 2),
                                  w=min(pw, 1.0), h=min(ph, 1.0)),
                        confidence=conf,
                        label="watermark",
                        ts=ts,
                    ))
                dets = nms(dets, _WM_NMS_IOU)
            if len(dets) < 1 and self._matcher is not None and self._matcher.available():
                dets.extend(self._matcher.match(frame, ts=ts))
            per_frame.append(dets)
        return per_frame

    # ---- 字幕检测 ----
    def detect_subtitle(
        self, frames: list[tuple[float, npt.NDArray[np.uint8]]]
    ) -> list[list[Detection]]:
        """长边≤960 且边为 32 倍数 → DBNet det → 多边形转最小外接矩形 → 合并。"""
        sess = self._registry.session("subtitle")   # 缺失抛 AI001（上层路由手动框选）
        per_frame: list[list[Detection]] = []
        for ts, frame in frames:
            h, w = frame.shape[:2]
            scale = min(960 / max(w, h), 1.0)
            nw = max(32, round(w * scale / 32) * 32)
            nh = max(32, round(h * scale / 32) * 32)
            resized = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_LINEAR)
            arr = resized.astype(np.float32).transpose(2, 0, 1) / 255.0
            arr = (arr - arr.mean()) / max(arr.std(), 1e-6)
            input_name = sess.get_inputs()[0].name
            outputs = sess.run(None, {input_name: arr[None].astype(np.float32)})
            prob = np.asarray(outputs[0])[0]
            mask = (prob > 0.3).astype(np.uint8)
            contours, _ = cv2.findContours(
                mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            dets: list[Detection] = []
            for contour in contours:
                x, y, cw, ch = cv2.boundingRect(contour)
                if cw < 4 or ch < 4:
                    continue
                dets.append(Detection(
                    bbox=BBox(x=x / nw, y=y / nh, w=cw / nw, h=ch / nh),
                    confidence=1.0,
                    label="subtitle",
                    ts=ts,
                ))
            per_frame.append(merge_boxes(dets, _SUB_MERGE_IOU))
        return per_frame

    # ---- 图像修复 ----
    def inpaint(
        self,
        frame: npt.NDArray[np.uint8],
        mask: npt.NDArray[np.uint8],
    ) -> npt.NDArray[np.uint8]:
        """mask 区域修复：LaMa tile 优先；小区域/模型缺失走 TELEA 快速通道。"""
        ys, xs = np.where(mask > 0)
        if len(xs) == 0:
            return frame
        x0, x1 = int(xs.min()), int(xs.max())
        y0, y1 = int(ys.min()), int(ys.max())
        pw, ph = x1 - x0 + 1, y1 - y0 + 1

        if pw < _SMALL_PATCH_PX or ph < _SMALL_PATCH_PX:
            return cv2.inpaint(frame, mask, 3, cv2.INPAINT_TELEA).astype(np.uint8).astype(np.uint8)

        try:
            sess = self._registry.session("inpaint")
        except AppError as exc:
            # D2 降级：LaMa 不可用 → TELEA 全图修复（效果打折，UI 明示基础修复）
            logger.warning("修复模型不可用（%s），降级 INPAINT_TELEA", exc.code)
            return cv2.inpaint(frame, mask, 3, cv2.INPAINT_TELEA).astype(np.uint8)

        # 外扩 patch 含上下文，resize 到 512 tile 推理
        margin = int(max(pw, ph) * 0.12) + 8
        cx0, cy0 = max(0, x0 - margin), max(0, y0 - margin)
        cx1 = min(frame.shape[1], x1 + margin + 1)
        cy1 = min(frame.shape[0], y1 + margin + 1)
        patch = frame[cy0:cy1, cx0:cx1]
        pmask = mask[cy0:cy1, cx0:cx1]
        orig_h, orig_w = patch.shape[:2]
        resized_img = cv2.resize(patch, (_INPAINT_TILE, _INPAINT_TILE))
        resized_mask = cv2.resize(pmask, (_INPAINT_TILE, _INPAINT_TILE),
                                  interpolation=cv2.INTER_NEAREST)
        img_in = resized_img.astype(np.float32) / 127.5 - 1.0
        img_in = np.ascontiguousarray(img_in.transpose(2, 0, 1))[None]
        msk_in = (resized_mask > 0).astype(np.float32)[None, None]
        outputs = sess.run(None, {
            "image": img_in.astype(np.float32),
            "mask": msk_in,
        })
        result = np.asarray(outputs[0])[0]
        result = ((result.transpose(1, 2, 0) + 1.0) * 127.5).clip(0, 255).astype(np.uint8)
        back = cv2.resize(result, (orig_w, orig_h))

        # 羽化 alpha 混合回贴（仅贴 mask 膨胀羽化区域）
        feather = cv2.GaussianBlur((pmask > 0).astype(np.float32), (17, 17), 8)
        blended = (back.astype(np.float32) * feather[..., None]
                   + patch.astype(np.float32) * (1 - feather[..., None]))
        out = frame.copy()
        out[cy0:cy1, cx0:cx1] = blended.astype(np.uint8)
        return out

    # ---- 关键帧嵌入 ----
    def embed_frames(
        self, frames: list[tuple[float, npt.NDArray[np.uint8]]]
    ) -> npt.NDArray[np.float32]:
        """CLIP 预处理 → 批推理 batch=16 → L2 归一化 (n,512)。"""
        sess = self._registry.session("clip")   # AI001/AI002 由调用方捕获降级
        vectors: list[npt.NDArray[np.float32]] = []
        batch: list[npt.NDArray[np.float32]] = []

        def flush() -> None:
            if not batch:
                return
            arr = np.stack(batch).astype(np.float32)
            input_name = sess.get_inputs()[0].name
            outs = sess.run(None, {input_name: arr})
            vecs = np.asarray(outs[0]).astype(np.float32)
            vectors.extend(vecs)
            batch.clear()

        for _ts, frame in frames:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            resized = cv2.resize(rgb, (224, 224), interpolation=cv2.INTER_LINEAR)
            arr = resized.astype(np.float32) / 255.0
            arr = (arr - np.array(_CLIP_MEAN, dtype=np.float32)) / np.array(
                _CLIP_STD, dtype=np.float32
            )
            batch.append(np.ascontiguousarray(arr.transpose(2, 0, 1)))
            if len(batch) >= 16:
                flush()
        flush()
        mat = np.stack(vectors).astype(np.float32) if vectors else np.zeros(
            (0, 512), dtype=np.float32
        )
        norms: npt.NDArray[np.float32] = np.linalg.norm(mat, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        result_arr: npt.NDArray[np.float32] = (mat / norms).astype(np.float32)
        return result_arr







