# 运镜轨迹特征（详设 14.1.3）：Farneback 光流 → 相似变换 → pan/zoom 曲线
from __future__ import annotations

import cv2
import numpy as np
import numpy.typing as npt

_CURVE_LEN = 32
_GRID_STEP_PX = 16

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


class MotionAnalyzer:
    """相邻帧光流 → (32,3) 运镜曲线，逐列 clip 到 [-1,1]。"""

    def analyze(
        self,
        frames_img: list[npt.NDArray[np.uint8]],
    ) -> npt.NDArray[np.float32]:
        triplets: list[tuple[float, float, float]] = []
        for i in range(1, len(frames_img)):
            triplets.append(_pair_motion(frames_img[i - 1], frames_img[i]))
        curve = _resample(triplets, _CURVE_LEN)
        return np.clip(curve, -1.0, 1.0).astype(np.float32)
