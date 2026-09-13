# 全应用唯一网络出口（详设第六章）：会话/代理/重试/断点续传/外网探测
from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, cast

import requests
from PySide6.QtCore import QObject, Signal
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import ych
from ych.common.cancellation import CancellationToken, ProgressFn
from ych.common.errors import (
    ERR_AI_REMOTE,
    ERR_DL_VERIFY_FAILED,
    ERR_NET_DNS_FAIL,
    ERR_NET_PROXY,
    ERR_NET_TIMEOUT,
    AppError,
)
from ych.common.schemas import ResumeState
from ych.services.s5_base.config_service import ConfigService
from ych.services.s5_base.log_service import LogService

logger = logging.getLogger("ych.s4")

# DNS 解析失败的特征串（跨 urllib3 版本出现在异常链文本中）
_DNS_MARKERS = (
    "getaddrinfo",
    "nameresolution",
    "name or service not known",
    "nodename nor servname",
    "temporary failure in name resolution",
    "no such host is known",
)

_CHUNK_SIZE = 256 * 1024          # 256KB（详设 6.3）
_PROGRESS_INTERVAL_S = 1.0        # 进度回调最小间隔
_PROGRESS_INTERVAL_BYTES = 1024 * 1024  # 或每 ≥1MB 回调一次


def _is_dns_error(exc: BaseException) -> bool:
    """沿异常链查找 DNS 解析失败特征。"""
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        text = str(cur).lower()
        if any(marker in text for marker in _DNS_MARKERS):
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def _body_snippet(resp: requests.Response, limit: int = 200) -> str:
    """HTTP 错误响应摘要：状态码 + 响应体前段（含服务端错误说明）。"""
    body = (resp.text or "").strip().replace("\n", " ")
    return f"远程服务返回 {resp.status_code}：{body[:limit]}"


class HttpClient(QObject):
    """requests.Session 的统一封装；插件不得自建 session。"""

    # (code, message) 供全局错误提示
    net_error = Signal(str, str)

    def __init__(self, config: ConfigService) -> None:
        super().__init__()
        self._config = config
        self._session = self._build_session()
        # 运行时改代理设置即时生效（无需重启）
        config.changed.connect(self._on_config_changed)

    def _on_config_changed(self, key: str, _value: object) -> None:
        if key in ("proxy_enabled", "proxy_host", "proxy_port"):
            # 重建全新会话整体替换：requests.Session 非严格线程安全，
            # 在运行中的共享会话上热改 proxies 有竞态；旧会话由在途
            # 请求自然收尾后回收
            self._session = self._build_session()
            logger.info("网络会话已按新代理配置重建")

    def _apply_proxy(self, session: requests.Session | None = None) -> None:
        """代理配置 → session；__init__ 传入新会话，运行时改动复用现有会话。"""
        s = session if session is not None else self._session
        if self._config.get_typed("proxy_enabled", bool):
            host = str(self._config.get("proxy_host"))
            port_raw = self._config.get("proxy_port")
            port = port_raw if isinstance(port_raw, int) else 0
            if host and port:
                proxy = f"http://{host}:{port}"
                s.proxies = {"http": proxy, "https": proxy}
                logger.info("代理已更新：%s:%s", host, port)
                return
        s.proxies = {}
        logger.info("代理已停用")

    # ---- 会话构建 ----
    def _build_session(self) -> requests.Session:
        """UA/超时/请求级重试/代理，全部来自 S5 配置（详设 6.2）。"""
        session = requests.Session()
        max_retry = self._config.get_typed("max_retry", int)
        # 429 不在传输层重试：限频由 RateLimiter 前置规避 + 业务层 PLG003 退避
        # （详设 11.3），传输层快速重试只会加剧限频
        retry = Retry(
            total=max_retry,
            backoff_factor=1.5,
            status_forcelist=(500, 502, 503, 504),
            allowed_methods=("GET", "HEAD"),
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        session.headers["User-Agent"] = f"YuanChongGou/{ych.__version__}"
        self._apply_proxy(session)
        return session

    @property
    def session(self) -> requests.Session:
        return self._session

    # ---- 基础请求 ----
    def get_json(
        self,
        url: str,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        status_error_code: str = ERR_NET_TIMEOUT,
        timeout_s: float = 30.0,
    ) -> dict[str, object] | list[object]:
        """GET 并解析 JSON；网络错误映射 NET 域错误码后抛出。

        HTTP 4xx/5xx 抛 status_error_code（调用方按自己的错误域传入），
        消息带响应体摘要（远程服务的错误说明通常在响应体里）。
        """
        logger.debug("GET %s", LogService.sanitize(url))
        try:
            resp = self._session.get(
                url, params=params, headers=headers, timeout=(5, timeout_s)
            )
        except requests.exceptions.Timeout as exc:
            self.net_error.emit(ERR_NET_TIMEOUT, "连接超时")
            raise AppError(ERR_NET_TIMEOUT, "连接超时，请稍后重试", cause=exc) from exc
        except requests.exceptions.ProxyError as exc:
            self.net_error.emit(ERR_NET_PROXY, "代理不可用")
            raise AppError(
                ERR_NET_PROXY, "代理不可用，请检查代理设置", cause=exc
            ) from exc
        except requests.exceptions.ConnectionError as exc:
            code = ERR_NET_DNS_FAIL if _is_dns_error(exc) else ERR_NET_TIMEOUT
            msg = "域名解析失败" if code == ERR_NET_DNS_FAIL else "网络连接失败"
            self.net_error.emit(code, msg)
            raise AppError(code, msg, cause=exc) from exc
        except requests.exceptions.RequestException as exc:
            self.net_error.emit(ERR_NET_TIMEOUT, "请求失败")
            raise AppError(ERR_NET_TIMEOUT, "网络请求失败", cause=exc) from exc
        if resp.status_code >= 400:
            self.net_error.emit(status_error_code, f"HTTP {resp.status_code}")
            raise AppError(status_error_code, _body_snippet(resp))
        try:
            data = resp.json()
        except ValueError as exc:
            raise AppError(status_error_code, "响应不是有效 JSON", cause=exc) from exc
        if not isinstance(data, (dict, list)):
            raise AppError(status_error_code, "响应格式异常")
        return data

    def post_json(
        self,
        url: str,
        json_body: object,
        headers: dict[str, str] | None = None,
        timeout_s: float = 60.0,
    ) -> dict[str, object] | list[object]:
        """POST JSON 并解析 JSON 响应；HTTP 状态错误带上响应体摘要抛出。"""
        logger.debug("POST %s", LogService.sanitize(url))
        timeout = (5, timeout_s)
        try:
            resp = self._session.post(
                url, json=cast(Any, json_body), headers=headers, timeout=timeout
            )
        except requests.exceptions.Timeout as exc:
            self.net_error.emit(ERR_NET_TIMEOUT, "连接超时")
            raise AppError(ERR_NET_TIMEOUT, "连接超时，请稍后重试", cause=exc) from exc
        except requests.exceptions.ProxyError as exc:
            self.net_error.emit(ERR_NET_PROXY, "代理不可用")
            raise AppError(
                ERR_NET_PROXY, "代理不可用，请检查代理设置", cause=exc
            ) from exc
        except requests.exceptions.ConnectionError as exc:
            code = ERR_NET_DNS_FAIL if _is_dns_error(exc) else ERR_NET_TIMEOUT
            msg = "域名解析失败" if code == ERR_NET_DNS_FAIL else "网络连接失败"
            self.net_error.emit(code, msg)
            raise AppError(code, msg, cause=exc) from exc
        except requests.exceptions.RequestException as exc:
            self.net_error.emit(ERR_NET_TIMEOUT, "请求失败")
            raise AppError(ERR_NET_TIMEOUT, "网络请求失败", cause=exc) from exc
        if resp.status_code >= 400:
            # 4xx/5xx：远程服务的错误说明在响应体里（如鉴权失败/模型不存在）
            raise AppError(ERR_AI_REMOTE, _body_snippet(resp))
        try:
            data = resp.json()
        except ValueError as exc:
            raise AppError(ERR_AI_REMOTE, "响应不是有效 JSON", cause=exc) from exc
        if not isinstance(data, (dict, list)):
            raise AppError(ERR_AI_REMOTE, "响应格式异常")
        return data

    def head(self, url: str) -> requests.Response:
        """HEAD 请求（探测/取大小用）。"""
        return self._session.head(url, timeout=(5, 30), allow_redirects=True)

    # ---- 断点续传下载（详设 6.3 算法逐步落地）----
    def download_stream(
        self,
        url: str,
        dest: Path,
        resume: ResumeState | None,
        on_progress: ProgressFn | None,
        token: CancellationToken | None,
        on_state: Callable[[ResumeState], None] | None = None,
    ) -> ResumeState:
        """流式下载到 dest（.part 临时文件），支持断点续传。

        - resume 存在且临时文件在 → Range 续传；206 且 ETag 一致才追加；
        - 200/416/ETag 变化/无 Range 支持 → 丢弃重下（etag 置 ""）；
        - 取消/异常时保留 .part，ResumeState 持久化由调用方负责；
        - on_state 在每次进度落点回调当前 ResumeState（调用方借此持久化，
          支撑崩溃后从数据库恢复续传）。
        """
        temp = Path(resume.temp_path) if resume and resume.temp_path else (
            Path(str(dest) + ".part")
        )
        temp.parent.mkdir(parents=True, exist_ok=True)

        start_from = 0
        expect_etag: str | None = None
        if resume is not None and resume.downloaded_bytes > 0 and temp.exists():
            expect_etag = resume.etag
            start_from = resume.downloaded_bytes

        headers: dict[str, str] = {}
        if expect_etag is not None or start_from > 0:
            headers["Range"] = f"bytes={start_from}-"

        try:
            resp = self._session.get(
                url, headers=headers, stream=True, timeout=(5, 30)
            )
        except requests.exceptions.Timeout as exc:
            raise AppError(ERR_NET_TIMEOUT, "下载连接超时", cause=exc) from exc
        except requests.exceptions.ConnectionError as exc:
            code = ERR_NET_DNS_FAIL if _is_dns_error(exc) else ERR_NET_TIMEOUT
            raise AppError(code, "下载连接失败", cause=exc) from exc

        can_resume = (
            resp.status_code == 206
            and (expect_etag is None or resp.headers.get("ETag", "") == expect_etag)
        )
        if not can_resume:
            # 服务端忽略 Range（返回 200）/416/ETag 变化 → 整段重下，
            # 且按详设置 etag=""（不支持 Range 的标记）
            resp.close()
            headers.pop("Range", None)
            try:
                resp = self._session.get(
                    url, headers=headers, stream=True, timeout=(5, 30)
                )
            except requests.exceptions.RequestException as exc:
                raise AppError(ERR_NET_TIMEOUT, "下载连接失败", cause=exc) from exc
            start_from = 0
            etag = ""
        else:
            etag = resp.headers.get("ETag", "")

        # 4xx/5xx 的错误页响应体也会照常走完流式循环，必须在此拦下：
        # 否则 404 文本会被当作正常文件保存，最终报"文件损坏"误导排查
        if resp.status_code not in (200, 206):
            resp.close()
            raise AppError(
                ERR_DL_VERIFY_FAILED,
                f"下载源响应异常（HTTP {resp.status_code}）",
            )

        remaining = int(resp.headers.get("Content-Length", "0") or 0)
        total_bytes = start_from + remaining
        downloaded = start_from
        mode = "ab" if start_from > 0 else "wb"

        def _emit_state() -> None:
            if on_state is None:
                return
            on_state(ResumeState(
                downloaded_bytes=downloaded, etag=etag,
                total_bytes=total_bytes, temp_path=str(temp),
            ))

        last_cb_ts = 0.0
        last_cb_bytes = 0
        try:
            with open(temp, mode) as f:
                try:
                    for chunk in resp.iter_content(chunk_size=_CHUNK_SIZE):
                        if token is not None:
                            token.check()
                        if not chunk:
                            continue
                        f.write(chunk)
                        downloaded += len(chunk)
                        now = time.monotonic()
                        if now - last_cb_ts >= _PROGRESS_INTERVAL_S or (
                            downloaded - last_cb_bytes >= _PROGRESS_INTERVAL_BYTES
                        ):
                            last_cb_ts = now
                            last_cb_bytes = downloaded
                            if on_progress is not None:
                                on_progress(
                                    downloaded / total_bytes if total_bytes
                                    else 0.0
                                )
                            _emit_state()
                    f.flush()
                    os.fsync(f.fileno())
                except requests.exceptions.RequestException as exc:
                    # 传输中途断流（最常见的瞬态失败）必须映射 NET 域错误码，
                    # 否则任务以 UNKNOWN 终态失败且重试机制失效；.part 已保留可续传
                    raise AppError(
                        ERR_NET_TIMEOUT,
                        "下载传输中断，请重试（已保留断点）",
                        cause=exc,
                    ) from exc
        finally:
            resp.close()

        # 服务端提前断流时 iter_content 正常结束：长度已知但不足 → 拒绝落位
        if remaining and downloaded != total_bytes:
            raise AppError(
                ERR_DL_VERIFY_FAILED,
                f"下载不完整（{downloaded}/{total_bytes} 字节），请重试",
            )

        if on_progress is not None and total_bytes:
            on_progress(1.0)
        _emit_state()
        return ResumeState(
            downloaded_bytes=downloaded,
            etag=etag,
            total_bytes=total_bytes,
            temp_path=str(temp),
        )

    # ---- 外网探测（供 M1.5 ForeignNetChecker 使用）----
    def probe_url(
        self, url: str, timeout_s: float = 5.0
    ) -> Literal["ok", "dns_fail", "conn_fail"]:
        """三态探测：HEAD 失败退化 GET(range bytes=0-0)。

        任一 HTTP 响应（含 403/404）即视为可达；DNS 失败→dns_fail；
        其余连接/TLS/超时失败→conn_fail。
        """
        timeout = (timeout_s, timeout_s)
        try:
            self._session.head(url, timeout=timeout, allow_redirects=True)
            return "ok"
        except requests.exceptions.ConnectionError as exc:
            if _is_dns_error(exc):
                return "dns_fail"
            # HEAD 可能被拒绝 → 退化 GET 单字节范围再试一次
            try:
                resp = self._session.get(
                    url, headers={"Range": "bytes=0-0"},
                    stream=True, timeout=timeout,
                )
                resp.close()
                return "ok"
            except requests.exceptions.ConnectionError as exc2:
                return "dns_fail" if _is_dns_error(exc2) else "conn_fail"
            except requests.exceptions.RequestException:
                return "conn_fail"
        except requests.exceptions.RequestException:
            return "conn_fail"

    def probe_latency(self, url: str, timeout_s: float = 4.0) -> int | None:
        """单站延迟测量：可达返回毫秒数（含 HEAD 被拒退化 GET），不可达 None。

        仅限后台线程调用（阻塞网络 IO，禁止在 GUI 线程使用）。
        """
        timeout = (timeout_s, timeout_s)
        start = time.monotonic()
        try:
            resp = self._session.head(url, timeout=timeout, allow_redirects=True)
            resp.close()
            return int((time.monotonic() - start) * 1000)
        except requests.exceptions.ConnectionError:
            try:
                resp = self._session.get(
                    url, headers={"Range": "bytes=0-0"},
                    stream=True, timeout=timeout,
                )
                resp.close()
                return int((time.monotonic() - start) * 1000)
            except requests.exceptions.RequestException:
                return None
        except requests.exceptions.RequestException:
            return None






