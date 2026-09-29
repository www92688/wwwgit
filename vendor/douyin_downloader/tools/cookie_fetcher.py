import argparse
import asyncio
import contextlib
import json
import re
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import parse_qs, unquote, urlparse

import yaml

from utils.cookie_utils import parse_cookie_header, sanitize_cookies

DEFAULT_URL = "https://www.douyin.com/"
DEFAULT_OUTPUT = Path("config/cookies.json")
REQUIRED_KEYS = {"msToken", "ttwid", "odin_tt", "passport_csrf_token"}
SUGGESTED_KEYS = REQUIRED_KEYS | {"sid_guard", "sessionid", "sid_tt"}
DEFAULT_AUXILIARY_KEYS = {
    "_waftokenid",
    "s_v_web_id",
    "__ac_nonce",
    "__ac_signature",
    "UIFID",
    "UIFID_TEMP",
    "d_ticket",
    "x-web-secsdk-uid",
    "__security_server_data_status",
}
DEFAULT_AUXILIARY_PREFIXES = (
    "__security_mc_",
    "bd_ticket_guard_",
    "_bd_ticket_crypt_",
)
PRIMARY_WAIT_UNTIL = "networkidle"
FALLBACK_WAIT_UNTIL = "domcontentloaded"
PRIMARY_TIMEOUT_MS = 300_000
FALLBACK_TIMEOUT_MS = 300_000


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Launch a browser, guide manual login, then dump Douyin cookies.",
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_URL,
        help=f"Login page to open (default: {DEFAULT_URL})",
    )
    parser.add_argument(
        "--browser",
        choices=["chromium", "firefox", "webkit"],
        default="chromium",
        help="Playwright browser engine (default: chromium)",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browser headless (not recommended for manual login)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="JSON file to write collected cookies",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Optional config.yml to update with captured cookies",
    )
    parser.add_argument(
        "--include-all",
        action="store_true",
        help="Store every cookie from douyin.com instead of the recommended subset",
    )
    return parser.parse_args(argv)


async def _capture_self_user_url(page: Any) -> Optional[str]:
    """ych 内置补丁：登录后访问 /user/self，等待前端跳转到自己的主页并取地址。

    /user/self 的跳转由前端 JS 完成（服务端不重定向），故需在浏览器内轮询；
    任何失败都不影响登录主流程（调用方按「可选增强」处理）。
    """
    for path in ("/user/self", "/self"):
        try:
            await page.goto(
                f"https://www.douyin.com{path}",
                wait_until="domcontentloaded",
                timeout=15000,
            )
        except Exception:
            continue
        for _ in range(10):
            match = re.search(r"/user/(MS4wLjAB[0-9A-Za-z_-]+)", page.url or "")
            if match:
                return f"https://www.douyin.com/user/{match.group(1)}"
            await page.wait_for_timeout(500)
    return None


async def capture_cookies(args: argparse.Namespace) -> int:
    try:
        from playwright.async_api import async_playwright  # type: ignore
    except ImportError:  # pragma: no cover - defensive path
        print(
            "[ERROR] Playwright is not installed. Run `pip install playwright` first.",
            file=sys.stderr,
        )
        return 1

    async with async_playwright() as p:
        browser_factory = getattr(p, args.browser)
        # ych 内置补丁：登录浏览器强制直连。proxy=per-context/direct:// 仍会
        # 走系统代理解析（WPAD 自动检测或残留的死代理会注入
        # ERR_PROXY_CONNECTION_FAILED）；--no-proxy-server 彻底禁用一切
        # 代理解析（系统代理/PAC/自动检测）。抖音为国内站点，直连即可。
        browser = await browser_factory.launch(
            headless=args.headless, args=["--no-proxy-server"],
        )
        context = await browser.new_context()
        page = await context.new_page()
        observed_cookie_headers: List[str] = []
        observed_mstokens: List[str] = []

        def _on_request(request: Any) -> None:
            try:
                headers = request.headers or {}
                cookie_header = headers.get("cookie")
                if cookie_header:
                    observed_cookie_headers.append(cookie_header)
                url = request.url or ""
                query = parse_qs(urlparse(url).query)
                if "msToken" in query and query["msToken"]:
                    observed_mstokens.append((query["msToken"][0] or "").strip())
                token = extract_ms_token_from_text(url)
                if token:
                    observed_mstokens.append(token)
            except Exception:
                # 观察请求失败不应影响主流程
                return

        page.on("request", _on_request)

        print("[INFO] 已打开浏览器：请在其中完成抖音登录（扫码 / 手机号均可）。")
        print("[INFO] 登录成功后会自动保存 Cookie，无需按回车；回车仅作手动兜底。")

        confirmed = await wait_for_login_confirmation(context, page, args.url)
        if confirmed == "closed":
            with contextlib.suppress(Exception):
                await browser.close()
            return 1

        storage = await context.storage_state()
        cookies = {
            cookie["name"]: cookie["value"]
            for cookie in storage["cookies"]
            if cookie["domain"].endswith("douyin.com")
        }
        cookies = sanitize_cookies(cookies)

        ms_token = await try_extract_ms_token(
            page, cookies, observed_cookie_headers, observed_mstokens
        )
        if ms_token and not cookies.get("msToken"):
            cookies["msToken"] = ms_token
            print("[INFO] Extracted msToken from alternate sources.")

        # —— ych 内置补丁（见 VENDORED.md）：捕获登录用户主页地址，供宿主显示昵称 ——
        try:
            self_url = await _capture_self_user_url(page)
            if self_url:
                Path.cwd().joinpath("self_user.txt").write_text(
                    self_url, encoding="utf-8"
                )
                print(f"[INFO] Captured self profile url: {self_url}")
        except Exception as exc:
            print(f"[WARN] Self profile capture skipped: {exc}")

        await context.close()
        await browser.close()

    picked = cookies if args.include_all else filter_cookies(cookies)
    picked = sanitize_cookies(picked)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(picked, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[INFO] Saved {len(picked)} cookie(s) to {args.output.resolve()}")

    missing = REQUIRED_KEYS - picked.keys()
    if missing:
        print(f"[WARN] Missing required cookie keys: {', '.join(sorted(missing))}")

    if args.config:
        update_config(args.config, picked)

    return 0


async def fetch_cookies(
    *,
    output: Path,
    config: Optional[Path] = None,
    url: str = DEFAULT_URL,
    browser: str = "chromium",
    headless: bool = False,
    include_all: bool = False,
) -> int:
    """Parameterised entry to the manual-login cookie capture flow.

    Thin wrapper around :func:`capture_cookies` so callers (e.g. the CLI
    auto-relogin flow) don't have to fake an argparse.Namespace.
    """
    args = argparse.Namespace(
        output=output,
        config=config,
        url=url,
        browser=browser,
        headless=headless,
        include_all=include_all,
    )
    return await capture_cookies(args)


def is_timeout_error(exc: Exception) -> bool:
    return exc.__class__.__name__ == "TimeoutError" or "Timeout" in str(exc)


def is_target_closed_error(exc: Exception) -> bool:
    return (
        exc.__class__.__name__ == "TargetClosedError"
        or "Target page, context or browser has been closed" in str(exc)
    )


async def goto_with_fallback(page: Any, url: str) -> str:
    # 部分站点会持续发请求，networkidle 可能一直达不到，超时后降级等待策略。
    try:
        await page.goto(url, wait_until=PRIMARY_WAIT_UNTIL, timeout=PRIMARY_TIMEOUT_MS)
        return PRIMARY_WAIT_UNTIL
    except Exception as exc:
        if is_target_closed_error(exc):
            print(
                "[WARN] Browser/page was closed during initial navigation, "
                "continuing with current browser state."
            )
            return "target_closed"
        if not is_timeout_error(exc):
            raise
        print(
            f"[WARN] goto(wait_until={PRIMARY_WAIT_UNTIL}) timed out after {PRIMARY_TIMEOUT_MS}ms, "
            f"falling back to {FALLBACK_WAIT_UNTIL}."
        )
    try:
        await page.goto(url, wait_until=FALLBACK_WAIT_UNTIL, timeout=FALLBACK_TIMEOUT_MS)
        return FALLBACK_WAIT_UNTIL
    except Exception as exc:
        if is_target_closed_error(exc):
            print(
                "[WARN] Browser/page was closed during fallback navigation, "
                "continuing with current browser state."
            )
            return "target_closed"
        if is_timeout_error(exc):
            print(
                f"[WARN] goto(wait_until={FALLBACK_WAIT_UNTIL}) also timed out after {FALLBACK_TIMEOUT_MS}ms, "
                "continuing anyway."
            )
            return "timeout"
        raise


_LOGIN_COOKIE_NAMES = ("sessionid", "sid_tt")


async def wait_for_login_confirmation(
    context: Any, page: Any, url: str, input_func: Any = input,
) -> str:
    """等待登录完成：自动检测为主，控制台回车兜底（ych 内置补丁）。

    两条路径并行、谁先满足用谁：
    - 自动检测：轮询浏览器 cookies，出现 sessionid/sid_tt 即已登录，
      无需用户回控制台按回车（返回 "login"）；
    - 控制台回车：手动强制确认，检测失效时的兜底（返回 "enter"）。
    浏览器在登录前被用户关闭则返回 "closed"（不保存任何 Cookie）。

    回车监听用 daemon 线程而非 asyncio.to_thread：自动确认后进程要能
    立即退出，阻塞在 input() 的线程不能拖住解释器关停（executor 会 join）。
    """
    # 页面导航放到后台执行，避免在导航等待期间终端无法响应 Enter。
    nav_task = asyncio.create_task(goto_with_fallback(page, url))
    # 让 nav_task 至少进入第一个 await 点。
    await asyncio.sleep(0)

    loop = asyncio.get_running_loop()
    login_event: asyncio.Event = asyncio.Event()
    enter_event: asyncio.Event = asyncio.Event()

    async def _poll_login_cookie() -> None:
        while True:
            try:
                cookies = await context.cookies()
            except Exception:
                return   # 浏览器已被关闭 → 两个事件都不触发 → 视为 closed
            if any(
                cookie.get("name") in _LOGIN_COOKIE_NAMES
                and (cookie.get("value") or "")
                for cookie in cookies
            ):
                login_event.set()
                return
            await asyncio.sleep(1.5)

    def _read_enter() -> None:
        try:
            input_func()
        except BaseException:
            return   # 无控制台/输入流关闭：仅依赖自动检测
        loop.call_soon_threadsafe(enter_event.set)

    threading.Thread(target=_read_enter, daemon=True).start()
    poll_task = asyncio.create_task(_poll_login_cookie())
    enter_wait = asyncio.create_task(enter_event.wait())
    await asyncio.wait({poll_task, enter_wait}, return_when=asyncio.FIRST_COMPLETED)

    if login_event.is_set():
        result = "login"
        print("[INFO] 检测到登录成功，正在保存 Cookie…")
        # 给登录会话一点稳定时间（登录后的若干请求还在写入 Cookie）
        await page.wait_for_timeout(1500)
    elif enter_event.is_set():
        result = "enter"
        print("[INFO] 已手动确认，正在保存 Cookie…")
    else:
        result = "closed"
        print("[WARN] 浏览器在登录完成前被关闭，未保存任何 Cookie。")

    poll_task.cancel()
    enter_wait.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await poll_task
    with contextlib.suppress(asyncio.CancelledError):
        await enter_wait

    if not nav_task.done():
        nav_task.cancel()
    try:
        await nav_task
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        print(f"[WARN] Navigation task ended with error: {exc}")
    return result


async def try_extract_ms_token(
    page: Any,
    cookies: Dict[str, str],
    observed_cookie_headers: List[str],
    observed_mstokens: List[str],
) -> Optional[str]:
    existing = cookies.get("msToken")
    if existing:
        return existing

    for token in reversed(observed_mstokens):
        token = (token or "").strip()
        if token:
            return token

    for header in reversed(observed_cookie_headers):
        parsed = parse_cookie_header(header)
        token = (parsed.get("msToken") or "").strip()
        if token:
            return token
        extra = extract_ms_token_from_text(header)
        if extra:
            return extra

    try:
        doc_cookie = await page.evaluate("() => document.cookie || ''")
        parsed = parse_cookie_header(doc_cookie)
        token = (parsed.get("msToken") or "").strip()
        if token:
            return token
        extra = extract_ms_token_from_text(doc_cookie)
        if extra:
            return extra
    except Exception:
        pass

    js = """
() => {
  const values = [];
  const pushIf = (v) => {
    if (typeof v === 'string' && v.trim()) values.push(v.trim());
  };
  try {
    for (const key of Object.keys(localStorage || {})) {
      if (key.toLowerCase().includes('mstoken')) {
        pushIf(localStorage.getItem(key));
      }
    }
  } catch (e) {}
  try {
    for (const key of Object.keys(sessionStorage || {})) {
      if (key.toLowerCase().includes('mstoken')) {
        pushIf(sessionStorage.getItem(key));
      }
    }
  } catch (e) {}
  return values;
}
"""
    try:
        candidates = await page.evaluate(js)
        for candidate in candidates or []:
            if not isinstance(candidate, str):
                continue
            text = candidate.strip()
            if not text:
                continue
            parsed = parse_cookie_header(text)
            if parsed.get("msToken"):
                return parsed["msToken"]
            extra = extract_ms_token_from_text(text)
            if extra:
                return extra
            if len(text) <= 2048 and all(ch not in text for ch in [";", " ", "\n", "\r", "\t"]):
                return text
    except Exception:
        pass

    return None


def extract_ms_token_from_text(text: str) -> Optional[str]:
    if not text:
        return None

    patterns = [
        r"(?:^|[;,&\s\"'])msToken=([^;,&\s\"']+)",
        r'"msToken"\s*:\s*"([^"]+)"',
        r"'msToken'\s*:\s*'([^']+)'",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        token = (match.group(1) or "").strip()
        if token:
            return unquote(token)
    return None


def filter_cookies(cookies: Dict[str, str]) -> Dict[str, str]:
    cookies = sanitize_cookies(cookies)
    picked = {}
    for key, value in cookies.items():
        if key in SUGGESTED_KEYS or key in DEFAULT_AUXILIARY_KEYS:
            picked[key] = value
            continue
        if any(key.startswith(prefix) for prefix in DEFAULT_AUXILIARY_PREFIXES):
            picked[key] = value

    if not picked:
        return cookies
    return picked


def update_config(config_path: Path, cookies: Dict[str, str]) -> None:
    existing: Dict[str, object] = {}
    if config_path.exists():
        existing = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    existing["cookies"] = cookies

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        yaml.safe_dump(existing, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    print(f"[INFO] Updated config file: {config_path.resolve()}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    return asyncio.run(capture_cookies(args))


if __name__ == "__main__":
    raise SystemExit(main())
