# 抖音采集插件：经内置 douyin-downloader 子进程采集博主主页作品
# 使用约束：仅限本机个人学习与素材管理；分发内置该功能的构建前请确认平台条款与法规
from __future__ import annotations

import logging
import shutil
import threading
import time
from collections.abc import Callable
from pathlib import Path

from ych.common.cancellation import (
    CancellationToken,
    ProgressFn,
    TaskCanceled,
)
from ych.common.errors import ERR_PLG_KEY_MISSING, AppError
from ych.common.schemas import ResumeState, SearchFilters, VideoMeta
from ych.core.m1_capture.douyin_risk_control import (
    DouyinRiskControl,
    budget_state,
    rejection_in_output,
)
from ych.core.m1_capture.plugin_base import PlatformPlugin, RateLimiterLike
from ych.core.m1_capture.plugins import _douyin_backend as backend
from ych.services.s4_net.http_client import HttpClient
from ych.services.s5_base.config_service import ConfigService

logger = logging.getLogger("ych.m1")

# 作品列表拉空重试：间歇性风控下首次采集常拉空，退避后重试通常恢复
_EMPTY_RESULT_RETRIES = 1
_EMPTY_RETRY_BACKOFF_S = 20.0


class DouyinPlugin(PlatformPlugin):
    """抖音来源：搜索=抓取博主作品元数据；下载=子进程按日期窗口取视频本体。

    与直链插件（Pexels 等）的差异：无公开 API，元数据与媒体都经内置
    CLI 获取，因此 search/download 都是阻塞子进程调用（协调器与
    QThreadPool worker 均为后台线程，允许阻塞）。
    """

    id = "douyin"
    display_name = "抖音"
    region = "cn"
    enabled_by_default = True

    RATE_HOST = ""   # 子进程内部自带限速与重试，不走 HttpClient 限频

    def __init__(
        self,
        http: HttpClient,
        config: ConfigService,
        limiter: RateLimiterLike | None = None,
    ) -> None:
        super().__init__(http, config, limiter)
        # 同一 CLI 共享 dy_downloader.db 增量库与 _run_config.yml：
        # 子进程必须串行化，避免 SQLite 锁冲突与配置互相覆盖
        self._cli_lock = threading.Lock()
        self._login_guard = threading.Lock()
        self._logging_in = False
        # 风控节流（方案一）：冷却/每日预算/最小搜索间隔，状态持久化
        self._risk = DouyinRiskControl(config)

    # ---- 依赖收敛 ----
    def _vendor_root(self) -> Path | None:
        override = str(self._config.get("douyin_vendor_dir"))
        return backend.resolve_vendor_root(override)

    def is_logged_in(self) -> bool:
        """登录状态以运行态配置文件中的真实 Cookie 为准（重启后依然成立）。"""
        root = self._vendor_root()
        return root is not None and backend.has_real_cookies(root)

    # ---- 登录（采集页「登录抖音」按钮入口）----
    def start_cookie_login(
        self, on_finished: Callable[[bool, str], None] | None = None
    ) -> None:
        """弹出浏览器完成抖音登录；结束后异步回调 (成功?, 原因)。

        cookie_fetcher 自动检测登录（浏览器 cookies 出现登录态即自动
        保存，无需回车）；控制台窗口用于展示日志与手动兜底确认。
        登录成功后同步标记位、抓取昵称（均经 config.changed 驱动 UI 刷新）。
        所有失败路径都必须回调，禁止静默吞掉。
        """
        root = self._vendor_root()
        if root is None:
            self._finish_login(on_finished, False,
                               "未找到内置下载器目录（vendor/douyin_downloader）")
            return
        try:
            # 先从模板引导运行态配置：否则 cookie_fetcher 落盘的会是
            # 只含 cookies 键的残缺配置，后续搜索找不到主页占位符
            backend.read_live_config(root)
        except AppError as exc:
            self._finish_login(on_finished, False, exc.message)
            return
        with self._login_guard:
            if self._logging_in:
                self._finish_login(
                    on_finished, False, "登录流程已在进行中，请查看已弹出的窗口",
                )
                return
            self._logging_in = True

        def _work() -> None:
            ok, reason = False, "登录未完成或 Cookie 未保存：请在浏览器完成登录后保持窗口开启片刻"
            try:
                proc = backend.spawn_cookie_login(root)
                exit_code = proc.wait()
                if exit_code != 0:
                    # 先看退出码：窗口闪退时即使文件里残留旧 Cookie 也不得判成功
                    reason = f"登录窗口异常退出（退出码 {exit_code}），请重试"
                elif backend.has_real_cookies(root):
                    ok, reason = True, ""
                    self._config.set("douyin_cookies_set", True)
            except Exception as exc:   # 契约：登录失败不得让线程崩溃
                logger.warning("抖音登录流程异常：%s", exc)
                reason = f"登录流程异常：{exc}"
            finally:
                with self._login_guard:
                    self._logging_in = False
            self._finish_login(on_finished, ok, reason)
            if ok:
                self.refresh_nickname()

        threading.Thread(target=_work, daemon=True, name="ych-douyin-login").start()

    @staticmethod
    def _finish_login(
        on_finished: Callable[[bool, str], None] | None,
        ok: bool,
        reason: str,
    ) -> None:
        """回调兜底：UI 回调异常只记日志，不影响登录线程。"""
        if on_finished is None:
            if not ok:
                logger.warning("抖音登录未完成：%s", reason)
            return
        try:
            on_finished(ok, reason)
        except Exception:
            logger.exception("抖音登录回调异常")

    # ---- 登录用户昵称（按钮旁展示用）----
    def refresh_nickname(self) -> str:
        """抓取登录用户的抖音昵称并写入配置；失败返回空串（UI 降级显示）。

        链路：self_user.txt（登录时浏览器捕获）→ 元数据抓取 1 条 →
        作品 JSON 的 author.nickname。该阻塞方法仅供后台线程调用。
        """
        root = self._vendor_root()
        if root is None or not backend.has_real_cookies(root):
            # 文件态为准：Cookie 缺失/被清理时回滚持久化的登录标记与昵称
            if bool(self._config.get("douyin_cookies_set")):
                self._config.set("douyin_cookies_set", False)
                self._config.set("douyin_nickname", "")
            return ""
        if not bool(self._config.get("douyin_cookies_set")):
            self._config.set("douyin_cookies_set", True)   # 文件态为准，同步标记
        sec_uid = backend.read_self_sec_uid(root)
        if not sec_uid:
            logger.info("未找到 self_user.txt，跳过昵称抓取")
            return ""
        home = f"https://www.douyin.com/user/{sec_uid}"
        try:
            with self._cli_lock:
                text = backend.build_run_config(
                    backend.read_live_config(root), home, 1,
                    use_database=False,
                )
                cfg_path = backend.write_run_config(root, text)
                run_dir = backend.new_harvest_dir(root)
                code, _tail = backend.run_cli(
                    root, ["-c", str(cfg_path), "-p", str(run_dir)],
                    None, backend._HARVEST_TIMEOUT_S,
                )
            if code != 0:
                return ""
            for data in backend.collect_harvest(run_dir, 1):
                author = data.get("author")
                if not isinstance(author, dict):
                    continue
                nickname = str(author.get("nickname") or "")
                if nickname:
                    self._config.set("douyin_nickname", nickname)
                    logger.info("抖音昵称获取成功：%s", nickname)
                    return nickname
        except Exception as exc:   # 昵称属增强信息：失败只降级，不报错
            logger.warning("抖音昵称获取失败：%s", exc)
        return ""

    # ---- 可用性 ----
    def check_available(self) -> tuple[bool, str]:
        """(可达?, 原因码)。≤5s 纯本地检查，任何情况不得抛异常。"""
        try:
            root = self._vendor_root()
            if root is None:
                return (False, "PLG010")   # 内置下载器缺失
            if not backend.has_real_cookies(root):
                return (False, ERR_PLG_KEY_MISSING)   # 未登录抖音
            return (True, "ok")
        except Exception:
            return (False, "PLG010")

    # ---- 搜索（博主作品列表）----
    def search(
        self,
        keyword: str,
        filters: SearchFilters,
        max_count: int,
        token: CancellationToken | None,
    ) -> list[VideoMeta]:
        home = backend.extract_home_url(keyword)
        if home is None:
            raise AppError(
                "PLG010",
                "抖音采集需粘贴博主主页链接（douyin.com/user/…），不支持关键词搜索",
            )
        if not self.is_logged_in():
            raise AppError(ERR_PLG_KEY_MISSING, "尚未登录抖音：请先点击「登录抖音」")
        root = self._vendor_root()
        assert root is not None
        max_posts = max_count if max_count > 0 else backend._DEFAULT_MAX_POSTS

        # ---- 风控节流（方案一）：冷却 / 最小间隔 / 每日预算 ----
        # 拒绝后的反复重试只会延长封锁；预算按"搜索次数"计，
        # 批量下载不入预算（单博主批量属正常使用，CLI 内部自带限速）。
        if (remain := self._risk.cooldown_remaining()) > 0:
            raise AppError(
                "PLG010",
                f"抖音风控冷却中（{self._risk.cooldown_hint()}）："
                "上次被平台安全校验拒绝，等待后自动恢复，期间请勿反复重试",
            )
        if (wait_s := self._risk.search_too_soon()) > 0:
            raise AppError(
                "PLG010",
                f"距离上次搜索不足 {int(wait_s) + 1} 秒，"
                "请稍候再试（密集采集容易触发平台风控）",
            )
        if self._risk.budget_exhausted():
            used, limit = budget_state(self._config)
            raise AppError(
                "PLG010",
                f"今日抖音搜索已达预算（{used}/{limit} 次），明天再试；"
                "也可先用免费素材站获取素材",
            )
        self._risk.note_search_start()

        # 拉空重试：平台间歇性风控/限流会让作品列表拉空（实测 CLI 退出码 0
        # 但滚动全程无产出），退避后重试一次即可恢复；成功或硬失败即止。
        def _harvest_meta(run_dir: Path | None) -> list[VideoMeta]:
            if run_dir is None:
                return []
            return [
                m for m in (
                    backend.meta_from_aweme(data, home)
                    for data in backend.collect_harvest(run_dir, max_posts)
                )
                if m is not None
            ]

        def _raise_rejected() -> AppError:
            """确定性拒绝：立即冷却并停止重试（重试只会延长封锁）。"""
            hint = self._risk.note_rejection()
            return AppError(
                "PLG010",
                f"作品列表被平台安全校验拒绝，已进入冷却（{hint}）。"
                "若弹出的浏览器窗口出现安全验证，请手动完成后再试；"
                "期间请勿反复重试，只会延长封锁",
            )

        code, metas, detail = 0, [], ""
        for attempt in range(1 + _EMPTY_RESULT_RETRIES):
            if token is not None:
                token.check()
            if attempt:
                logger.info(
                    "抖音作品列表拉空（第 %d 次），退避 %gs 后重试",
                    attempt, _EMPTY_RETRY_BACKOFF_S,
                )
                self._backoff_sleep(token)
            run_dir: Path | None = None
            tail: list[str] = []
            try:
                with self._cli_lock:
                    text = backend.build_run_config(
                        backend.read_live_config(root), home, max_posts,
                        use_database=False,   # 搜索只取元数据；不落增量库避免污染下载阶段
                    )
                    cfg_path = backend.write_run_config(root, text)
                    run_dir = backend.new_harvest_dir(root)
                    code, tail = backend.run_cli(
                        root, ["-c", str(cfg_path), "-p", str(run_dir)],
                        token, backend._HARVEST_TIMEOUT_S,
                    )
            except TaskCanceled:
                raise
            except AppError as exc:   # 超时等被终止：已落盘的作品仍可回收
                code, tail = 1, [str(exc.message or exc)]
            if code != 0:
                # 异常退出≠全无产出：子进程滚动期间会把已抓到的作品逐条
                # 落盘，超时/崩溃场景先回收，有产出就按部分成功返回
                salvaged = _harvest_meta(run_dir)
                if salvaged:
                    metas = salvaged
                    logger.warning(
                        "抖音下载器异常退出（%s），回收已抓取的 %d 条作品",
                        tail[-1] if tail else f"退出码 {code}", len(metas),
                    )
                    break
                if rejection_in_output(tail):
                    raise _raise_rejected()
                detail = tail[-1] if tail else f"退出码 {code}"
                break   # 硬失败不重试：重试解决的是"拉空"，不是崩溃
            if rejection_in_output(tail):
                raise _raise_rejected()
            metas = _harvest_meta(run_dir)
            if metas:
                break
            detail = tail[-1].strip() if tail and tail[-1].strip() else ""
        if not metas:
            if code != 0:
                raise AppError("PLG010", f"抖音作品列表获取失败：{detail}")
            raise AppError(
                "PLG010",
                "未获取到作品（已自动重试）：可能被平台风控限流，请稍后再试；"
                "若弹出了浏览器请在其中完成验证后再试",
            )
        self._risk.note_success()   # 采集成功：连续拒绝计数清零
        logger.info("抖音搜索 %s 命中 %d 条作品", home, len(metas))
        return metas

    def _backoff_sleep(self, token: CancellationToken | None) -> None:
        """退避等待：随取消信号打断，不无限阻塞。"""
        deadline = time.monotonic() + _EMPTY_RETRY_BACKOFF_S
        while time.monotonic() < deadline:
            if token is not None:
                token.check()
            time.sleep(0.5)

    # ---- 下载（单作品 → 本次任务临时文件 → 归档服务接管）----
    def download(
        self,
        meta: VideoMeta,
        dest_part: Path,
        on_progress: ProgressFn | None,
        resume: ResumeState | None,
        token: CancellationToken | None,
        on_state: Callable[[ResumeState], None] | None = None,
    ) -> ResumeState:
        home = str(meta.extra.get("home_url") or "")
        date = str(meta.extra.get("date") or "")
        if not home:
            raise AppError("PLG020", "作品元数据缺少主页链接（接口结构已变更？）")
        if not self.is_logged_in():
            raise AppError(ERR_PLG_KEY_MISSING, "尚未登录抖音：请先点击「登录抖音」")
        root = self._vendor_root()
        assert root is not None

        if on_progress is not None:
            on_progress(0.02)
        with self._cli_lock:
            if token is not None:
                token.check()
            final = self._existing_resume_file(resume)
            if final is None:
                final = self._run_download(root, meta, home, date, on_progress, token)

        target = Path(f"{dest_part}.mp4")
        if final.resolve() != target.resolve():
            shutil.copyfile(final, target)
        size = target.stat().st_size
        state = ResumeState(downloaded_bytes=size, total_bytes=size, temp_path=str(target))
        if on_state is not None:
            on_state(state)
        if on_progress is not None:
            on_progress(1.0)
        return state

    def _existing_resume_file(self, resume: ResumeState | None) -> Path | None:
        """崩溃恢复：上次任务已落盘的临时成品可直接复用。"""
        if resume is None or not resume.temp_path:
            return None
        path = Path(resume.temp_path)
        return path if path.is_file() and path.stat().st_size > 0 else None

    def _run_download(
        self,
        root: Path,
        meta: VideoMeta,
        home: str,
        date: str,
        on_progress: ProgressFn | None,
        token: CancellationToken | None,
    ) -> Path:
        """按作品日期开窗口跑 CLI（number=0 当日不限），再按 {id} 匹配文件。

        同日多作品会一次取回（后续同日任务命中内置增量库，秒级完成）。
        作品无日期时收窄为最新 1 条，防止 number=0 拉取整个主页。
        拉空重试与搜索一致：风控会让作品列表偶发拉空（CLI 退出码 0
        但无产出），退避后重试一次即可恢复。
        """
        # 冷却期快速失败：不发起 CLI（期间的批量下载任务会带明确原因失败，
        # 但不再撞平台——撞墙只会延长封锁）
        if (remain := self._risk.cooldown_remaining()) > 0:
            raise AppError(
                "PLG010",
                f"抖音风控冷却中（{self._risk.cooldown_hint()}），"
                "本次下载未发起；冷却结束后重新提交即可",
            )
        code, tail = 0, []
        for attempt in range(1 + _EMPTY_RESULT_RETRIES):
            if token is not None:
                token.check()
            if attempt:
                logger.info(
                    "抖音下载未找到作品（第 %d 次），退避 %gs 后重试",
                    attempt, _EMPTY_RETRY_BACKOFF_S,
                )
                self._backoff_sleep(token)
            text = backend.build_run_config(
                backend.read_live_config(root), home,
                0 if date else 1, start_date=date, end_date=date,
                download_media=True,   # 模板默认 video:false；不翻转则永远下载不出视频
            )
            cfg_path = backend.write_run_config(root, text)
            dl_dir = backend.downloads_dir(root)
            code, tail = backend.run_cli(
                root, ["-c", str(cfg_path), "-p", str(dl_dir)],
                token, backend._DOWNLOAD_TIMEOUT_S,
            )
            found = backend.match_media_file(dl_dir, meta.video_key)
            if found is not None:
                self._risk.note_success()   # 采集成功：连续拒绝计数清零
                if on_progress is not None:
                    on_progress(0.9)
                return found
            if rejection_in_output(tail):
                # 确定性拒绝：立即冷却并停止重试
                hint = self._risk.note_rejection()
                raise AppError(
                    "PLG010",
                    f"下载被平台安全校验拒绝，已进入冷却（{hint}）。"
                    "若弹出的浏览器窗口出现安全验证，请手动完成后再试",
                )
            if code != 0:
                break   # 硬失败不重试：重试解决的是"拉空"，不是崩溃
        detail = tail[-1] if tail else f"退出码 {code}"
        if code != 0:
            raise AppError("PLG010", f"抖音下载失败：{detail}")
        raise AppError(
            "PLG010",
            f"未找到作品 {meta.video_key} 的媒体文件（可能被风控拦截）：{detail}",
        )
