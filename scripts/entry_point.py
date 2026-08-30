# 入口包装：PyInstaller Analysis 需要一个真实脚本文件
# （spec 中优先使用本文件作为入口）
import sys

from ych.app import main

if __name__ == "__main__":
    sys.exit(main())
