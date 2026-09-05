@echo off
rem 源重构 YuChongGou 源码启动脚本（pythonw 启动，无控制台黑框）
rem 注意：本文件必须保存为 ANSI/GBK 编码，cmd 才能解析中文路径
cd /d "D:\项目开发文件\具体项目\视频截取"
start "" ".venv\Scripts\pythonw.exe" -m ych.app
