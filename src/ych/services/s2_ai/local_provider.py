# LocalProvider（详设 9.3）：本地四类推理实现 + D2 降级兜底
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, cast

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
_BREAK_AFTER = 2     # 推理连续失败达此次数即熔断（本会话直接走兜底）
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
        # 推理熔断：key → (session 对象, 连续失败数)。同一 session 连续失败
        # 达阈值后本会话直接走兜底（模型文件坏了每次重试只是白耗时）；
        # session 换新（设置页重新下载/替换模型）自动复位恢复尝试。
        self._breakers: dict[str, tuple[object, int]] = {}
        # ONNX 推理串行锁：DML 会话并发 run 会触发 D3D12 设备竞争
        # （实测段错误/驱动报错，且错误消息含 GBK 时还会引发 utf-8 解码
        # 异常）。解码/CV 等重活仍各线程并行，仅推理临界区串行——推理
        # 占单条特征提取耗时的很小部分，不构成吞吐瓶颈
        self._infer_lock = threading.Lock()

    def _run_session(self, sess: Any,
                     *input_arrays: npt.NDArray[np.float32],
                     feed: dict[str, npt.NDArray[np.float32]] | None = None,
                     ) -> list[npt.NDArray[np.float32]]:
        """进程内唯一推理入口：串行化 sess.run（线程安全边界）。

        单输入模型按位置喂入；多输入模型（LaMa）显式传 feed 以免
        依赖 session 的输入声明顺序。
        """
        with self._infer_lock:
            if feed is None:
                names = [i.name for i in sess.get_inputs()]
                feed = dict(zip(names, input_arrays, strict=True))
            return list(sess.run(None, feed))

    def _breaker_tripped(self, key: str, sess: object) -> bool:
        entry = self._breakers.get(key)
        if entry is None or entry[0] is not sess:
            self._breakers.pop(key, None)
            return False
        return entry[1] >= _BREAK_AFTER

    def _breaker_record(self, key: str, sess: object) -> int:
        entry = self._breakers.get(key)
        count = entry[1] + 1 if entry is not None and entry[0] is sess else 1
        self._breakers[key] = (sess, count)
        return count

    def _breaker_reset(self, key: str) -> None:
        self._breakers.pop(key, None)

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
        结果不足且模板库可用时 TemplateMatcher 补充；模型缺失走纯模板兜底。
        兼容两种输出布局：自训模型 (N,≥5) 已带 NMS；标准 ultralytics
        导出 (C,anchors) 预 NMS（公开水印模型即此格式）。"""
        try:
            sess = self._registry.session("watermark")
        except AppError as exc:
            logger.warning("水印检测模型不可用（%s），降级 TemplateMatcher", exc.code)
            sess = None

        if sess is not None and self._breaker_tripped("watermark", sess):
            return self._watermark_template_only(frames)
        per_frame: list[list[Detection]] = []
        try:
            for ts, frame in frames:
                dets: list[Detection] = []
                if sess is not None:
                    blob, scale, (pad_x, pad_y) = self._letterbox(frame, 640)
                    outputs = self._run_session(
                        sess, blob[None].astype(np.float32),
                    )
                    preds = np.asarray(outputs[0], dtype=np.float32)[0]
                    if preds.ndim == 2 and preds.shape[0] <= 10 \
                            and preds.shape[1] >= 100:
                        # 通道优先预 NMS：转成行布局，分数取各通道最大
                        conf_col = preds[4:].max(axis=0)
                        preds = np.column_stack([preds[:4].T, conf_col])
                    if preds.ndim == 2 and len(preds):
                        # 向量化预过滤（预 NMS 有 8400 锚点行，避免逐行 Python 循环）
                        preds = preds[preds[:, 4] >= _WM_CONF_THRESHOLD]
                    h, w = frame.shape[:2]
                    for row in preds:
                        cx, cy, bw, bh, conf = (
                            float(v) for v in row[:5]
                        )
                        if conf < _WM_CONF_THRESHOLD:
                            continue
                        # 反 letterbox 回原分辨率归一化坐标
                        px = (cx - pad_x) / scale / w
                        py = (cy - pad_y) / scale / h
                        pw = bw / scale / w
                        ph = bh / scale / h
                        dets.append(Detection(
                            bbox=BBox(
                                x=max(0.0, px - pw / 2),
                                y=max(0.0, py - ph / 2),
                                w=min(pw, 1.0), h=min(ph, 1.0),
                            ),
                            confidence=conf,
                            label="watermark",
                            ts=ts,
                        ))
                    dets = nms(dets, _WM_NMS_IOU)
                if len(dets) < 1 and self._matcher is not None \
                        and self._matcher.available():
                    dets.extend(self._matcher.match(frame, ts=ts))
                per_frame.append(dets)
        except Exception as exc:
            # D2 降级：模型契约不符/推理失败不致命，模板兜底仍可用则继续
            count = self._breaker_record("watermark", sess)
            if count >= _BREAK_AFTER:
                logger.warning(
                    "水印模型推理失败（%s）；连续 %d 次失败，本次运行内"
                    "走纯模板兜底（重新下载/替换模型后自动恢复）",
                    exc, count,
                )
            else:
                logger.warning("水印模型推理失败，降级模板兜底：%s", exc)
            return self._watermark_template_only(frames)
        if sess is not None:
            self._breaker_reset("watermark")
        return per_frame

    def _watermark_template_only(
        self, frames: list[tuple[float, npt.NDArray[np.uint8]]],
    ) -> list[list[Detection]]:
        if self._matcher is not None and self._matcher.available():
            return [self._matcher.match(frame, ts=ts) for ts, frame in frames]
        return [[] for _ in frames]

    # ---- 字幕检测 ----
    def detect_subtitle(
        self, frames: list[tuple[float, npt.NDArray[np.uint8]]]
    ) -> list[list[Detection]]:
        """长边≤960 且边为 32 倍数 → DBNet det → 多边形转最小外接矩形 → 合并。

        模型缺失/损坏（AI001/AI002）降级为经典底部文字带检测，路由与清理照常。
        """
        try:
            sess = self._registry.session("subtitle")
        except AppError as exc:
            logger.warning(
                "字幕模型不可用（%s），降级经典文字带检测", exc.code,
            )
            return self._detect_subtitle_classical(frames)
        if self._breaker_tripped("subtitle", sess):
            return self._detect_subtitle_classical(frames)
        per_frame: list[list[Detection]] = []
        try:
            for ts, frame in frames:
                h, w = frame.shape[:2]
                scale = min(960 / max(w, h), 1.0)
                nw = max(32, round(w * scale / 32) * 32)
                nh = max(32, round(h * scale / 32) * 32)
                resized = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_LINEAR)
                arr = resized.astype(np.float32).transpose(2, 0, 1) / 255.0
                # PP-OCR det 标准归一化 (x-0.5)/0.5：实测全局 mean/std 会让
                # 概率图碎裂（整行字幕断成多条薄片框）
                arr = (arr - 0.5) / 0.5
                outputs = self._run_session(sess, arr[None].astype(np.float32))
                prob = np.asarray(outputs[0])[0]    # (1,H,W) 或 (H,W)
                if prob.ndim == 3:                  # 挤掉通道维 → (H,W) 概率图
                    prob = prob[0]
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
        except Exception as exc:
            # D2 降级：模型契约不符/推理运行期失败 → 整批走经典文字带
            count = self._breaker_record("subtitle", sess)
            if count >= _BREAK_AFTER:
                logger.warning(
                    "字幕模型推理失败（%s）；连续 %d 次失败，本次运行内"
                    "直接走经典检测（重新下载/替换模型后自动恢复）",
                    exc, count,
                )
            else:
                logger.warning("字幕模型推理失败，整批降级经典检测：%s", exc)
            return self._detect_subtitle_classical(frames)
        self._breaker_reset("subtitle")
        return per_frame

    @staticmethod
    def _detect_subtitle_classical(
        frames: list[tuple[float, npt.NDArray[np.uint8]]],
    ) -> list[list[Detection]]:
        """无模型兜底：底部 45% 文字带内以「亮度/梯度掩膜 + 行投影」找字幕行。

        硬编码字幕通常是高对比、横长条的白字；行投影天然聚合字符笔画，
        对实心色块与细笔画文字都成立。
        """
        per_frame: list[list[Detection]] = []
        for ts, frame in frames:
            h, w = frame.shape[:2]
            band_top = int(h * 0.55)
            band = frame[band_top:, :]
            band_h = h - band_top
            gray = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY)
            grad = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3))
            bright = gray > max(
                170.0, float(gray.mean()) + 2.5 * float(gray.std()),
            )
            raw_mask = (
                (grad > max(40.0, float(gray.mean()) * 0.6)) | bright
            ).astype(np.uint8)
            raw_mask *= 255
            mask_u8 = np.asarray(
                cv2.morphologyEx(
                    raw_mask,
                    cv2.MORPH_CLOSE,
                    cv2.getStructuringElement(
                        cv2.MORPH_RECT, (max(15, w // 12), 3),
                    ),
                ),
            ).astype(np.uint8)
            row_active = (mask_u8 > 0).sum(axis=1)
            row_on = row_active > max(3, int(w * 0.04))
            min_h = max(4, int(h * 0.012))
            dets: list[Detection] = []
            i = 0
            while i < band_h:
                if not row_on[i]:
                    i += 1
                    continue
                j = i
                while j < band_h and row_on[j]:
                    j += 1
                xs = np.where(mask_u8[i:j, :].any(axis=0))[0]
                cw = int(xs[-1]) - int(xs[0]) + 1 if xs.size else 0
                ch = j - i
                if xs.size and ch <= band_h * 0.6 and cw >= w * 0.08 \
                        and ch >= min_h and cw > ch:
                    dets.append(Detection(
                        bbox=BBox(x=int(xs[0]) / w, y=(band_top + i) / h,
                                  w=cw / w, h=ch / h),
                        confidence=0.8,
                        label="subtitle",
                        ts=ts,
                    ))
                i = j
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
            return cv2.inpaint(frame, mask, 3, cv2.INPAINT_TELEA).astype(np.uint8)

        try:
            sess = self._registry.session("inpaint")
        except AppError as exc:
            # D2 降级：LaMa 不可用 → TELEA 全图修复（效果打折，UI 明示基础修复）
            logger.warning("修复模型不可用（%s），降级 INPAINT_TELEA", exc.code)
            return cv2.inpaint(frame, mask, 3, cv2.INPAINT_TELEA).astype(np.uint8)

        # 正方形上下文裁剪：以掩码中心从真实帧裁 side×side（side=掩码
        # 长边×1.4，留出泊松求解所需的周边真实背景），等比缩放到 512²
        # ——模型看到的全是真实内容，无拉伸也无镜像填充
        side = int(min(max(pw, ph) * 1.4, min(frame.shape[0], frame.shape[1])))
        side = max(side, max(pw, ph))
        cx = (x0 + x1 + 1) / 2
        cy = (y0 + y1 + 1) / 2
        sx0 = int(min(max(round(cx - side / 2), max(0, x1 - side)),
                      min(x0, frame.shape[1] - side)))
        sy0 = int(min(max(round(cy - side / 2), max(0, y1 - side)),
                      min(y0, frame.shape[0] - side)))
        patch = frame[sy0:sy0 + side, sx0:sx0 + side]
        pmask = mask[sy0:sy0 + side, sx0:sx0 + side]
        resized_img = cv2.resize(patch, (_INPAINT_TILE, _INPAINT_TILE))
        resized_mask = cv2.resize(pmask, (_INPAINT_TILE, _INPAINT_TILE),
                                  interpolation=cv2.INTER_NEAREST)
        img_in = np.ascontiguousarray(
            resized_img.astype(np.float32).transpose(2, 0, 1) / 255.0)[None]
        msk_in = (resized_mask > 0).astype(np.float32)[None, None]
        try:
            outputs = self._run_session(sess, feed={
                "image": img_in.astype(np.float32),
                "mask": msk_in,
            })
        except Exception as exc:    # 模型与契约不符等：不致命，降级 TELEA
            logger.warning("LaMa 推理失败，降级 INPAINT_TELEA：%s", exc)
            return cv2.inpaint(frame, mask, 3, cv2.INPAINT_TELEA).astype(
                np.uint8)
        # Carve/LaMa-ONNX 约定（实测）：输入 image∈[0,1] + mask∈{0,1}，
        # 输出 0..255 合成帧，掩码外像素与输入逐比特一致
        result = np.asarray(outputs[0])[0]
        result = result.transpose(1, 2, 0).clip(0, 255).astype(np.uint8)
        back = cv2.resize(result, (side, side))

        # 低频梯度校正撤销说明：曾尝试用掩码带边界颜色纵向插值替换
        # 填充的低频——但掩码带边缘可能切在文字/前景上，参考色被污
        # 染后反而把带子涂花；泊松融合本身已对齐边界色调，不再叠加
        pmask_u8 = (pmask > 0).astype(np.uint8) * 255
        touches_border = bool(pmask_u8[0].any() or pmask_u8[-1].any()
                              or pmask_u8[:, 0].any() or pmask_u8[:, -1].any())
        out = frame.copy()
        if touches_border:
            # 掩码贴正方形边（超大掩码）：泊松无求解余量 → 距离 alpha 混合
            dist = cv2.distanceTransform((pmask > 0).astype(np.uint8),
                                         cv2.DIST_L2, 3)
            alpha = np.clip(dist / 2.5, 0.0, 1.0)[..., None].astype(np.float32)
            blended = (back.astype(np.float32) * alpha
                       + patch.astype(np.float32) * (1.0 - alpha))
            out[sy0:sy0 + side, sx0:sx0 + side] = blended.astype(np.uint8)
            return out
        # 泊松融合：填充内容的色调与梯度自动贴合周边真实背景
        # （无痕关键——消除填充区与渐变天空之间的亮度差和色块边界）
        pmask_dil = cv2.dilate(pmask_u8,
                               cv2.getStructuringElement(
                                   cv2.MORPH_ELLIPSE, (15, 15)))
        # seamlessClone 把"掩码外接框中心"对齐到 center：必须传掩码框
        # 中心在全帧坐标的位置。掩码贴画面边时上下文贴片被钳位，掩码
        # 不居中于贴片，传贴片中心会把填充内容错放到别处（框保持原样）
        mys, mxs = np.where(pmask_dil > 0)
        center = (int(sx0 + (mxs.min() + mxs.max() + 1) / 2),
                  int(sy0 + (mys.min() + mys.max() + 1) / 2))
        try:
            return cast(npt.NDArray[np.uint8],
                        cv2.seamlessClone(back, out, pmask_dil, center,
                                          cv2.NORMAL_CLONE))
        except cv2.error:
            dist = cv2.distanceTransform((pmask > 0).astype(np.uint8),
                                         cv2.DIST_L2, 3)
            alpha = np.clip(dist / 2.5, 0.0, 1.0)[..., None].astype(np.float32)
            blended = (back.astype(np.float32) * alpha
                       + patch.astype(np.float32) * (1.0 - alpha))
            out[sy0:sy0 + side, sx0:sx0 + side] = blended.astype(np.uint8)
            return out

    # ---- 关键帧嵌入 ----
    def embed_frames(
        self, frames: list[tuple[float, npt.NDArray[np.uint8]]]
    ) -> npt.NDArray[np.float32]:
        """CLIP 预处理 → 批推理 batch=16 → L2 归一化 (n,512)。

        CLIP 缺失/损坏降级经典构图特征（颜色直方图+灰度网格+DCT 纹理），
        维度与归一化口径与 CLIP 一致，重复度分析可继续但判别力下降。
        """
        try:
            sess = self._registry.session("clip")
        except AppError as exc:
            logger.warning(
                "CLIP 不可用（%s），构图特征降级为经典特征", exc.code,
            )
            return self._classical_embed(frames)
        vectors: list[npt.NDArray[np.float32]] = []
        batch: list[npt.NDArray[np.float32]] = []

        def flush() -> None:
            if not batch:
                return
            arr = np.stack(batch).astype(np.float32)
            outs = self._run_session(sess, arr)
            vecs = np.asarray(outs[0]).astype(np.float32)
            vectors.extend(vecs)
            batch.clear()

        try:
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
        except Exception as exc:
            # D2 降级：CLIP 推理失败（契约不符/OOM）→ 经典构图特征
            logger.warning("CLIP 推理失败，降级经典构图特征：%s", exc)
            return self._classical_embed(frames)
        mat = np.stack(vectors).astype(np.float32) if vectors else np.zeros(
            (0, 512), dtype=np.float32
        )
        norms: npt.NDArray[np.float32] = np.linalg.norm(mat, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        result_arr: npt.NDArray[np.float32] = (mat / norms).astype(np.float32)
        return result_arr

    @staticmethod
    def _classical_embed(
        frames: list[tuple[float, npt.NDArray[np.uint8]]],
    ) -> npt.NDArray[np.float32]:
        """经典构图特征：HSV 直方图(64) + 8×8 灰度网格均值/方差(128)
        + DCT 低频(64) + 梯度能量(2)，补零到 512 维。确定性、免模型。"""
        vectors: list[npt.NDArray[np.float32]] = []
        for _ts, frame in frames:
            small = cv2.resize(frame, (64, 64), interpolation=cv2.INTER_AREA)
            hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
            hist = cv2.calcHist(
                [hsv], [0, 1], None, [8, 8], [0, 180, 0, 256],
            ).flatten()
            hist = hist / max(float(hist.sum()), 1.0)
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32)
            cells = gray.reshape(8, 8, 8, 8)
            grid_mean = cells.mean(axis=(1, 3)).flatten() / 255.0
            grid_std = cells.std(axis=(1, 3)).flatten() / 255.0
            dct = cv2.dct(gray).flatten()[:64]
            dct = dct / max(float(np.abs(dct).max()), 1e-6)
            gx = float(np.abs(cv2.Sobel(
                gray, cv2.CV_32F, 1, 0, ksize=3)).mean()) / 255.0
            gy = float(np.abs(cv2.Sobel(
                gray, cv2.CV_32F, 0, 1, ksize=3)).mean()) / 255.0
            vectors.append(np.concatenate(
                [hist, grid_mean, grid_std, dct, [gx, gy]],
            ).astype(np.float32))
        out = np.zeros((len(vectors), 512), dtype=np.float32)
        for i, vec in enumerate(vectors):
            out[i, :len(vec)] = vec
        norms: npt.NDArray[np.float32] = np.linalg.norm(
            out, axis=1, keepdims=True,
        )
        norms[norms == 0] = 1.0
        return (out / norms).astype(np.float32)







