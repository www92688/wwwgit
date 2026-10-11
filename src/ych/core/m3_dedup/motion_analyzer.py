# 运镜轨迹特征（详设 14.1.3）：Farneback 光流 → 相似变换 → pan/zoom 曲线
from __future__ import annotations

from typing import cast

import cv2
import numpy as np
import numpy.typing as npt

_CURVE_LEN = 32
_GRID_STEP_PX = 16
# 光流是全部特征里最贵的一步（Farneback 每对帧数十毫秒级）；两条压缩手段
# 都不改变输出语义：pan/zoom 是归一化量，与分辨率无关；曲线最终重采样到
# 32 点，均匀抽样帧对只影响极短瞬间的采样密度
_MOTION_MAX_SIDE = 320    # 光流分析降采样上限（分析帧 640 → 光流 320）
_MAX_PAIRS = 96           # 帧对均匀抽样上限（300 帧 → 299 对 → ≤96 对）

_FB_PARAMS = dict(
    pyr_scale=0.5, levels=3, winsize=15,
    iterations=3, poly_n=5, poly_sigma=1.2, flags=0,
)


def _pair_motion(prev: npt.NDArray[np.uint8],
                 cur: npt.NDArray[np.uint8]) -> tuple[float, float, float]:
    """单对帧 → (pan_x/W, pan_y/H, zoom−1)；估计失败记 0。"""
    h, w = prev.shape[:2]
    flow = cv2.calcOpticalFlowFarneback(   # type: ignore[call-overload]
        cv2.cvtColor(prev, cv2.COLOR_BGR2GRAY),
        cv2.cvtColor(cur, cv2.COLOR_BGR2GRAY),
        None, _FB_PARAMS["pyr_scale"], _FB_PARAMS["levels"],
        _FB_PARAMS["winsize"], _FB_PARAMS["iterations"],
        _FB_PARAMS["poly_n"], _FB_PARAMS["poly_sigma"], _FB_PARAMS["flags"],
    )
    ys, xs = np.mgrid[_GRID_STEP_PX // 2:h:_GRID_STEP_PX,
                      _GRID_STEP_PX // 2:w:_GRID_STEP_PX]
    src = np.stack([xs.ravel(), ys.ravel()], axis=1).astype(np.float64)
    dst = src + flow[ys.ravel(), xs.ravel()].astype(np.float64)
    m, _inliers = cv2.estimateAffinePartial2D(
        src.reshape(-1, 1, 2), dst.reshape(-1, 1, 2),
        method=cv2.RANSAC, ransacReprojThreshold=2.0,
    )
    if m is None:
        return (0.0, 0.0, 0.0)
    pan_x = float(m[0, 2]) / w
    pan_y = float(m[1, 2]) / h
    s = float(np.sqrt(max(np.linalg.det(m[:2, :2]), 1e-12)))
    return (pan_x, pan_y, s - 1.0)


def _resample(series: list[tuple[float, float, float]],
              length: int) -> npt.NDArray[np.float32]:
    """线性插值重采样到固定长度 L。"""
    arr = np.asarray(series, dtype=np.float64)
    if arr.size == 0:
        return np.zeros((length, 3), dtype=np.float32)
    if len(arr) == 1:
        return np.repeat(arr, length, axis=0).astype(np.float32)
    src_x = np.linspace(0.0, 1.0, len(arr))
    dst_x = np.linspace(0.0, 1.0, length)
    out = np.stack([
        np.interp(dst_x, src_x, arr[:, c]) for c in range(3)
    ], axis=1)
    return out.astype(np.float32)


def _motion_small(img: npt.NDArray[np.uint8]) -> npt.NDArray[np.uint8]:
    """光流工作帧：长边 ≤ _MOTION_MAX_SIDE（小图原样返回）。"""
    h, w = img.shape[:2]
    side = max(h, w)
    if side <= _MOTION_MAX_SIDE or side == 0:
        return img
    if w >= h:
        nw = _MOTION_MAX_SIDE
        nh = max(2, round(h * nw / w / 2) * 2)
    else:
        nh = _MOTION_MAX_SIDE
        nw = max(2, round(w * nh / h / 2) * 2)
    return cast(
        npt.NDArray[np.uint8],
        cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA),
    )


def _pair_indices(n_frames: int) -> npt.NDArray[np.intp]:
    """参与光流的帧对左帧下标（均匀抽样，帧数少时全部保留）。"""
    n_pairs = n_frames - 1
    if n_pairs <= _MAX_PAIRS:
        return np.arange(n_pairs, dtype=np.intp)
    sel = np.linspace(0, n_pairs - 1, _MAX_PAIRS)
    return np.unique(sel.astype(np.intp))


class MotionAnalyzer:
    """相邻帧光流 → (32,3) 运镜曲线，逐列 clip 到 [-1,1]。"""

    def analyze(
        self,
        frames_img: list[npt.NDArray[np.uint8]],
    ) -> npt.NDArray[np.float32]:
        n = len(frames_img)
        if n < 2:
            triplets: list[tuple[float, float, float]] = []
        else:
            small = [_motion_small(f) for f in frames_img]
            triplets = [
                _pair_motion(small[i], small[i + 1])
                for i in _pair_indices(n)
            ]
        curve = _resample(triplets, _CURVE_LEN)
        return np.clip(curve, -1.0, 1.0).astype(np.float32)
