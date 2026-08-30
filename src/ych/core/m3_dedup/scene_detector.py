# 镜头切分（详设 14.1.2）：HSV 直方差 + 自适应阈值 + 邻域极大值 + 最短镜头
from __future__ import annotations

import cv2
import numpy as np
import numpy.typing as npt

_BINS = 32
_MIN_SHOT_S = 0.8          # 距上一边界最短镜头时长
_LOCAL_RADIUS = 3          # 邻域极大值半径
_K_SIGMA = 2.5


def frame_hist_diff(img_a: npt.NDArray[np.uint8],
                    img_b: npt.NDArray[np.uint8]) -> float:
    """HSV 三通道各 32-bin 直方图 L1 差 / 像素数（纯函数，可独立测试）。"""
    hist = []
    for img in (img_a, img_b):
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        chans = [
            cv2.calcHist([hsv], [c], None, [_BINS], rng)
            for c, rng in ((0, (0, 180)), (1, (0, 256)), (2, (0, 256)))
        ]
        hist.append(np.concatenate(chans).ravel())
    n_pixels = float(img_a.shape[0] * img_a.shape[1])
    return float(np.abs(hist[0] - hist[1]).sum() / max(n_pixels, 1.0))


class SceneDetector:
    """相邻抽样帧直方差 → 边界时刻列表（升序）。"""

    def split(
        self,
        frames_ts: list[float],
        frames_img: list[npt.NDArray[np.uint8]],
        duration_s: float,
    ) -> list[float]:
        """返回边界 [t1<…<tm]；镜头区间=(t_{k-1},t_k]，t0=0、末点=duration_s。"""
        n = len(frames_img)
        if n < 2:
            return []
        diffs = np.array([
            frame_hist_diff(frames_img[i - 1], frames_img[i])
            for i in range(1, n)
        ], dtype=np.float64)                       # diffs[i] 对应 ts[i+1]
        threshold = float(diffs.mean() + _K_SIGMA * diffs.std())

        boundaries: list[float] = []
        for i, d in enumerate(diffs):
            if d <= threshold:
                continue
            lo = max(0, i - _LOCAL_RADIUS)
            hi = min(len(diffs), i + _LOCAL_RADIUS + 1)
            if d < diffs[lo:hi].max():
                continue
            t = frames_ts[i + 1]
            if boundaries and t - boundaries[-1] < _MIN_SHOT_S:
                continue
            boundaries.append(round(t, 3))
        return boundaries

    @staticmethod
    def shots_of(boundaries: list[float], duration_s: float) -> list[tuple[float, float]]:
        """边界 → [(start,end)] 镜头区间列表。"""
        edges = [0.0, *boundaries, float(duration_s)]
        return [(edges[k], edges[k + 1]) for k in range(len(edges) - 1)
                if edges[k + 1] > edges[k]]
