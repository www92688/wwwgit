# PACKAGE - 打包与分发

> 模块目标：.exe 安装包产出、CI 流水线、开源分发材料。
> 设计依据：`design/max_design.md` 第二章 2.1、第三章 runtime/、第十九章 SP-8；需求第五章
> 前置依赖：全部模块完成；SP-3/SP-8 结论（包拆分策略）
> 输出位置：仓库根 `scripts/build_exe.spec`、`.github/workflows/`

## 任务清单

### PyInstaller 打包
- [x] pyproject 增加 build 依赖 pyinstaller；`scripts/build_exe_spec`：src/ych 入口 + datas 打包 runtime/ffmpeg.exe/ffprobe.exe + runtime/models/*（按 SP-8 结论决定全量/基础两版）+ i18n/*.qm + templates/*.png
- [x] 基础包体积 ≤150MB 验证；模型按需下载流程接入（URL/sha 校验/断点续传走 S4.download_stream）
- [x] 干净 Win10 虚拟机验证：runtime 二进制定位、keyring 未登录新用户行为、冷启动 ≤5s（HDD 放宽 8s，README 标注）
- [x] Windows 长路径与中文路径冒烟

### Inno Setup 安装包
- [x] 安装脚本：开始菜单/桌面快捷方式、卸载保留用户数据(%APPDATA%)、安装包签名（可选）
- [x] 双击安装→启动→跑通一次最小功能冒烟

### CI（GitHub Actions）
- [x] windows-latest runner：unit+component 每次提交；integration job 单独触发（夜间/发版前）
- [x] 覆盖率门禁：services ≥80% / core ≥75% / 关键算法(similarity/postprocess/scene/motion/rhythm) ≥90%
- [x] Release 工作流：tag 触发构建 → 产物附到 GitHub Release

### 开源材料
- [x] LICENSE 文件（MIT 或 Apache 2.0，待用户确认）
- [x] README：安装说明 / 使用教程 / 常见问题 / 平台可用性说明（六平台"暂不可用"降级口径）
- [x] CONTRIBUTING 说明插件扩展点（新增平台 = 新增 *_plugin.py）与去重手法扩展点
- [x] 法律合规声明：仅供学习与个人创作，素材合规由用户自负，工具不存储不传播版权内容

## 完成标准
干净 Win10 环境双击安装即用；CI 全绿且覆盖率达标；Release 产物可下载。
