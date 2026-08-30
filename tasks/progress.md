# 源重构 - 总体进度

> 依据：`flag/flagone.md`（需求 v2.0）+ `design/max_design.md`（详设 v1.0）
> 用法：模块完成勾选对应条目；各模块子任务见 `tasks/<module-name>.md` 内部 check list
> 更新日期：2026-08-25

---

## 阶段与放行门槛

```text
阶段0 Spike 验证（P0 放行门槛）
  └→ 阶段1 公共件 + 服务层（S5/S4/S3）
       └→ 阶段2 引擎层（S1/S2）
            └→ 阶段3 核心业务（M5/M4，可提前并行搭框架）
                 └→ 阶段4 功能模块（M1 → M2 → M3）
                      └→ 阶段5 表示层 U
                           └→ 阶段6 打包发布
```

依赖规则：S5/S4/S3/S1←公共件（S2 另需 SP-2/SP-3 结论）；S4←S5；M5←S1+S3；M4←S3+S5(Config)；M1←S3+S4+M5+M4；M2←S1+S2+M4+M5；M3←S1+S2+M4+M5+M1(候选搜索复用采集插件与 HttpClient)；U 渐进接入全部服务。

---

## 模块进度总表

| # | 文件 | 模块 | 状态 |
|---|------|------|------|
| - | [x] `tasks/00-spike.md` | 技术可行性验证 SP-1~SP-8（P0=SP-1/2/3 放行门槛） | 已降级（替身冒烟，P0 放行 DEC-004） |
| 0 | [x] `tasks/01-common.md` | 工程骨架与公共件（schemas/errors/cancellation/fsutil/fixtures） | 已完成 |
| 1 | [x] `tasks/02-s5-base.md` | S5 基础服务（Config/Log/I18n） | 已完成 |
| 1 | [x] `tasks/03-s4-net.md` | S4 网络服务（HttpClient/限速/断点续传/probe_url） | 已完成 |
| 1 | [x] `tasks/04-s3-db.md` | S3 数据持久化（DDL v1 九表 + DAO） | 已完成 |
| 2 | [x] `tasks/05-s1-media.md` | S1 媒体引擎（FFmpegRunner/run_pipe/Probe/抽帧） | 已完成 |
| 2 | [x] `tasks/06-s2-ai.md` | S2 AI 推理（Provider/模型注册/postprocess/LaMa 修复） | 已完成 |
| 3 | [x] `tasks/07-m5-library.md` | M5 素材库管理（工作目录/三级归档/扫描索引） | 已完成 |
| 3 | [x] `tasks/08-m4-scheduler.md` | M4 任务调度中心（状态机/重试/失败记录/崩溃恢复） | 已完成 |
| 4 | [x] `tasks/09-m1-capture.md` | M1 素材采集（插件框架/Pexels/Pixabay/下载队列/外网检测） | 已完成 |
| 4 | [x] `tasks/10-m2-preprocess.md` | M2 视频预处理（五处理项/三条路径分派/帧级修复） | 已完成 |
| 4 | [x] `tasks/11-m3-dedup.md` | M3 智能去重（特征提取/相似度公式/五手法/策略/流水线） | 已完成 |
| 5 | [x] `tasks/12-ui.md` | U 表示层（U0~U6 页面/i18n/UI 冒烟测试） | 已完成 |
| 6 | [x] `tasks/13-package.md` | 打包分发（PyInstaller/Inno Setup/CI/开源材料） | 已完成（D4 剪裁） |

状态取值：未开始 / 进行中 / 已完成 / 已降级（仅 Spike）

---

## 关键验收锚点（来自需求）

- [x] 性能：预处理 ≤2×时长；去重 ≤3×时长；下载 ≥3 并行；启动 ≤5s；界面响应 ≤1s
- [x] 稳定性：原文件不破坏性修改；崩溃不丢已完成文件；失败列表可一键重新处理
- [x] 兼容：Win10+；输出 MP4(H.264+AAC)；导入 mp4/avi/mov/mkv/flv
- [x] 易用：中英文可切换默认中文；主要功能 ≤3 步；错误提示含解决建议
- [x] 可扩展：平台插件式架构；去重手法模块化

## 风险跟踪（需求第六章）

- [x] 平台反爬策略升级 → 插件式架构快速更新适配（骨架已降级设计）
- [x] 各平台接口变动 → 社区共同维护，及时更新适配
- [x] AI 去水印效果不完美 → 手动框选兜底 + 预览
- [x] 联网比对依赖外部服务 → 手动提供对比视频替代
- [x] 外网平台访问受限 → 启动检测提醒（ForeignNetChecker）








