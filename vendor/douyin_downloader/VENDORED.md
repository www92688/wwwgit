# VENDORED：douyin-downloader（内置抖音下载器）

- 来源：https://github.com/jiji262/douyin-downloader （MIT License，见本目录 LICENSE）
- 搬入时间：2026-09-25；上游同步方式：手动
- 本目录为独立子进程组件：ych 通过 `python run.py -c <config>` 调用，**不做进程内 import**

## 相对上游的裁剪与修改

- 删除：`img/`（README 配图）、`tests/`、`docs/`、用户运行产物（`config.yml`、`dy_downloader.db`、`Downloaded/`）
- 保留：全部 Python 源码与 `config.example.yml`（其余上游逻辑零改动，便于后续手动同步）
- 新增：`config_plugin.template.yml`（ych 插件专用配置模板，含 `__DOUYIN_HOME_URL__` 占位符；
  首次运行引导为运行态 `config_plugin.yml`，登录后**含 Cookie，不入库**）
- 本地补丁（同步上游时需注意，均在 `tools/cookie_fetcher.py`，失败不影响主流程）：
  1. 登录浏览器强制直连（`launch(args=["--no-proxy-server"])`），彻底禁用
     系统代理/PAC/WPAD 解析——`proxy=per-context`/`direct://` 仍会被系统
     "自动检测设置"或残留死代理注入 ERR_PROXY_CONNECTION_FAILED
  2. 登录确认后访问 `/user/self` 捕获登录者主页地址，写入 `self_user.txt`（ych 用于显示昵称）
  3. 登录等待改为自动检测为主：轮询 cookies 出现 sessionid/sid_tt 即自动
     保存，无需控制台回车；回车保留为手动兜底（daemon 线程，自动确认后
     进程可立即退出）
- 本地补丁（在 `core/api_client.py`，2026-09-29）：
  4. 作品列表浏览器回补改用 `launch_persistent_context("browser_profile/")`
     ——固定浏览器指纹，cookie/localStorage 跨次保留，让抖音看到稳定的
     "回头客"浏览器而非每次全新的自动化环境，降低安全校验拦截率；回补
     窗口中出现的验证由用户手动完成后，通过状态也会随指纹保留。
     **删除 `browser_profile/` 即可重置指纹。** 同步上游时注意该函数
     （`fetch_user_post_ids_via_browser` 附近）的 `browser=None` 兜底收尾。

## 运行产物（均已加入 .gitignore，严禁入库）

- `config_plugin.yml` 登录后**包含 Cookie**；`config/cookies.json` 为完整 Cookie 导出；
  `self_user.txt` 为登录者主页地址；`_run_config.yml` 为按次生成的运行配置
- `dy_downloader.db` 增量去重库；`_harvest/` 元数据暂存；`_downloads/` 下载暂存

## 使用约束

本组件通过逆向网页接口下载抖音内容，**仅限本机个人学习与素材管理使用**；
对外分发内置本目录的构建产物前，请再次确认平台条款与当地法规。
