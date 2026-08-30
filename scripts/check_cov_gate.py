"""关键算法覆盖率门禁校验器（CI 用，stdlib 实现）。

背景：pytest-cov 对「点号单模块」--cov 目标的解析会提前导入目标模块，
在 numpy+cv2 组合下触发双重初始化使收集崩溃；文件路径目标又静默零数据。
故 CI 改为包级 --cov 采集 + 本脚本对指定文件逐个卡阈值。

用法：
    python scripts/check_cov_gate.py <coverage_xml> <threshold> <file.py> [<file.py> ...]
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import PureWindowsPath


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    xml_path, threshold = argv[0], float(argv[1])
    wanted = [PureWindowsPath(w) for w in argv[2:]]

    root = ET.parse(xml_path).getroot()
    rates: dict[str, float] = {}
    for cls in root.iter("class"):
        raw = cls.get("filename", "")
        name = PureWindowsPath(raw.replace("/", "\\")).name
        rates[name] = max(rates.get(name, 0.0), float(cls.get("line-rate", 0)) * 100)

    failed: list[str] = []
    for w in wanted:
        target = w.name
        got = rates.get(target)
        if got is None:
            failed.append(f"{w}: 未在覆盖数据中找到")
        elif got < threshold:
            failed.append(f"{w}: {got:.1f}% < {threshold:g}%")

    for name in sorted(rates):
        print(f"  {name:<32} {rates[name]:6.1f}%")
    if failed:
        print("门禁未通过：")
        for f in failed:
            print(f"  ✗ {f}")
        return 1
    print(f"关键算法覆盖率门禁通过（≥{threshold:g}%）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
