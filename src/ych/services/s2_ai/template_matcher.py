# TemplateMatcher 兜底（详设 9.2/9.3）：已知平台角标模板多尺度匹配
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import numpy.typing as npt

from ych.common.schemas import BBox
from ych.services.s2_ai.provider import Detection

logger = logging.getLogger("ych.s2")

_MATCH_THRESHOLD = 0.8
_SCALES = (0.5, 0.75, 1.0, 1.25, 1.5)


class TemplateMatcher:
    """templates/*.png 多尺度 matchTemplate(TM_CCOEFF_NORMED) 兜底检测。"""

    def __init__(self, templates_dir: Path) -> None:
        self._dir = templates_dir
        self._templates: list[tuple[str, Any]] = []
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        if self._dir.exists():
            for p in sorted(self._dir.glob("*.png")):
                img = cv2.imread(str(p), cv2.IMREAD_COLOR)
                if img is None:
                    continue
                # 零方差纯色模板会使 TM_CCOEFF_NORMED 未定义，跳过并告警
                if float(img.std()) < 1e-3:
                    logger.warning("跳过零方差模板：%s", p.name)
                    continue
                self._templates.append((p.stem, img))
        self._loaded = True

    def available(self) -> bool:
        """模板库是否可用（供 LocalProvider 决定是否补充检测）。"""
        self._ensure_loaded()
        return bool(self._templates)

    def match(
        self,
        frame: npt.NDArray[np.uint8],
        ts: float = 0.0,
        threshold: float = _MATCH_THRESHOLD,
    ) -> list[Detection]:
        """在单帧中匹配全部模板，返回命中 Detection（归一化坐标）。"""
        self._ensure_loaded()
        if not self._templates:
            return []
        h, w = frame.shape[:2]
        results: list[Detection] = []
        for _name, tmpl in self._templates:
            th, tw = tmpl.shape[:2]
            best_val = -1.0
            best_loc: tuple[int, int] = (0, 0)
            best_size: tuple[int, int] = (tw, th)
            for scale in _SCALES:
                nw = max(4, int(tw * scale))
                nh = max(4, int(th * scale))
                if nw >= w or nh >= h:
                    continue
                scaled = cv2.resize(tmpl, (nw, nh), interpolation=cv2.INTER_AREA)
                res = cv2.matchTemplate(frame, scaled, cv2.TM_CCOEFF_NORMED)
                _, max_val, _, max_loc = cv2.minMaxLoc(res)
                if max_val > best_val:
                    best_val = float(max_val)
                    best_loc = (int(max_loc[0]), int(max_loc[1]))
                    best_size = (nw, nh)
            if best_val >= threshold:
                results.append(Detection(
                    bbox=BBox(
                        x=best_loc[0] / w,
                        y=best_loc[1] / h,
                        w=best_size[0] / w,
                        h=best_size[1] / h,
                    ),
                    confidence=float(best_val),
                    label="watermark",
                    ts=ts,
                ))
        return results






