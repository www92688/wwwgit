# 贡献指南

感谢参与「源重构」！本项目的两大核心扩展点均采用**插件式架构**，无需修改主程序即可增强能力。

## 开发环境

```bash
git clone <repo-url> && cd yuanchonggou
pip install -e ".[dev]"
pytest tests/unit -q          # 快速回归（无需 ffmpeg）
ruff check src tests && mypy  # 提交前必须全绿
```

提交规范：小步提交；UI 文案使用 `self.tr()`；注释与用户文案使用简体中文。

## 扩展点一：新增采集平台插件

平台 = `src/ych/core/m1_capture/plugins/` 下一个 `*_plugin.py` 文件，**零注册代码**——
PluginManager 启动时自动扫描发现。

最小骨架：

```python
# src/ych/core/m1_capture/plugins/myplatform_plugin.py
from __future__ import annotations

from ych.core.m1_capture.plugin_base import PlatformPlugin


class MyPlatformPlugin(PlatformPlugin):
    id = "myplatform"            # 全局唯一
    display_name = "我的平台"
    region = "cn"                # cn | global
    requires_api_key = False     # True 时设置页出现 Key 输入框（keyring 存储）
    enabled_by_default = False

    RATE_HOST = "api.myplatform.com"
    RATE_INTERVAL_S = 2.0        # 客户端限频；429 由 PLG003 兜底

    def check_available(self) -> tuple[bool, str]:
        # 必须 ≤5s 返回、任何情况不得抛异常
        return (True, "ok")

    def search(self, keyword, filters, max_count, token):
        # 复用 self.api_get() 走统一网络出口与错误映射
        resp = self.api_get("https://api.myplatform.com/search",
                            params={"q": keyword})
        self.raise_for_status(resp)          # 401/403→PLG002, 429→PLG003
        data = self.parse_json(resp, "MyPlatform")
        ...  # 映射为 list[VideoMeta]，清晰度选择用 self.pick_download_variant()
```

要求：
1. 一切网络请求经注入的 `HttpClient`（S4 唯一出口），禁止自建 session；
2. 搜索失败抛 `AppError`（PLG 域），单平台异常由协调器隔离不会影响其他平台；
3. 补充 `tests/fixtures/<platform>_search.json` 样例 + responses mock 四分支用例；
4. README 平台表格同步更新可用性状态。

## 扩展点二：新增去重手法

手法 = `src/ych/core/m3_dedup/techniques/` 下的 `DedupTechnique` 子类，
在 `registry.make_default_registry()` 中注册一行。

要点：
- `zorder` 决定执行顺序（现有：mirror10 < crop20 < color30 < speed40 < border50）；
- 参数通过 `param_schema` 声明，`validate_params` 自动完成缺省补全与 clamp；
- `apply(ctx, params)` 只向 `ctx.vf_filters` 追加 ffmpeg 滤镜或修改 speed_factor；
- 保证输出分辨率/编码符合全局规范（偶数对齐、yuv420p）。

## 发布流程

1. PR 合并到 main（CI 静态检查+单元测试必须绿）
2. 打 tag `vX.Y.Z` → Release 工作流自动构建 exe 与安装包
