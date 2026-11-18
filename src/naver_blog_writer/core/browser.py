"""브라우저 세션 관리.

- 비밀번호를 저장하지 않습니다. 영구 프로필(PROFILE_DIR)에 사용자가 직접 로그인한 쿠키만 남습니다.
- 도구를 호출할 때만 브라우저를 열고 끝나면 닫습니다. 그래서 MCP 서버가 켜질 때 느려지지 않고,
  로그인 창과 프로필을 두고 충돌하지도 않습니다.
- 한 번에 하나의 작업만 실행하도록 잠급니다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from playwright.async_api import BrowserContext, Page, async_playwright

from .. import config
from . import selectors as S

log = logging.getLogger(__name__)

LOGIN_URL = "https://nid.naver.com/nidlogin.login?mode=form&url=https%3A%2F%2Fblog.naver.com%2F"
AUTH_COOKIES = {"NID_AUT", "NID_SES"}

_lock = asyncio.Lock()


class NotLoggedInError(RuntimeError):
    pass


def publish_guard_js() -> str:
    """발행 버튼 클릭을 캡처 단계에서 막는 스크립트.

    자동화 브라우저 안에서는 사람이든 코드든 발행 버튼을 누를 수 없습니다.
    발행은 사용자가 평소 쓰는 브라우저에서 임시저장 글을 열어 직접 합니다.
    """
    sels = json.dumps(", ".join(S.PUBLISH_BLOCKLIST))
    return f"""
(() => {{
  if (window.__nbwGuard) return;
  const SEL = {sels};
  const block = (e) => {{
    const t = e.target && e.target.closest ? e.target.closest(SEL) : null;
    if (t) {{
      e.preventDefault(); e.stopImmediatePropagation();
      window.__nbwBlocked = (window.__nbwBlocked || 0) + 1;
      console.warn('[nbw] publish click blocked');
    }}
  }};
  for (const ev of ['pointerdown', 'mousedown', 'mouseup', 'click', 'auxclick']) {{
    window.addEventListener(ev, block, true);
  }}
  window.__nbwGuard = true;
}})();
"""


class ProfileBusyError(RuntimeError):
    pass


# macOS: 자동화 크롬은 백그라운드 프로세스(Claude Desktop의 하위 프로세스)가 띄우므로 Claude Desktop 창 뒤에 열립니다.
# 사용자가 작업을 지켜볼 수 있게 그 크롬 프로세스를 앞으로 가져옵니다(앱 활성화라 추가 권한이 필요 없습니다).
_ACTIVATE_JXA = """
ObjC.import('AppKit');
function run(argv) {
  const app = $.NSRunningApplication.runningApplicationWithProcessIdentifier(parseInt(argv[0]));
  if (!app || app.isNil()) return 'none';
  app.activateWithOptions($.NSApplicationActivateIgnoringOtherApps);
  return 'ok';
}
"""


def _browser_pid(profile_dir: str) -> int | None:
    """이 서버 전용 프로필로 실행된 크롬 메인 프로세스의 PID."""
    try:
        out = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return None
    for line in out.splitlines():
        pid, _, cmd = line.strip().partition(" ")
        if f"--user-data-dir={profile_dir}" in cmd and "--type=" not in cmd and "Helper" not in cmd:
            return int(pid)
    return None


def _bring_to_front_sync(profile_dir: str) -> str:
    pid = _browser_pid(profile_dir)
    if not pid:
        return "pid-not-found"
    try:
        r = subprocess.run(["osascript", "-l", "JavaScript", "-e", _ACTIVATE_JXA, str(pid)],
                           capture_output=True, text=True, timeout=5)
        return (r.stdout or r.stderr).strip()
    except Exception as e:
        return f"error: {e}"


async def bring_to_front() -> None:
    if sys.platform != "darwin":
        return
    res = await asyncio.to_thread(_bring_to_front_sync, str(config.PROFILE_DIR))
    log.info("브라우저 창 앞으로 가져오기: %s", res)


@asynccontextmanager
async def browser_session(headless: bool | None = None) -> AsyncIterator[tuple[BrowserContext, Page]]:
    if _lock.locked():
        raise ProfileBusyError("다른 블로그 작업이 진행 중입니다. 끝난 뒤 다시 시도하세요.")
    async with _lock:
        config.ensure_dirs()
        pw = await async_playwright().start()
        kwargs: dict = dict(
            user_data_dir=str(config.PROFILE_DIR),
            headless=config.HEADLESS if headless is None else headless,
            locale="ko-KR",
            timezone_id="Asia/Seoul",
            viewport={"width": 1400, "height": 1000},
            slow_mo=config.SLOW_MO_MS,
            args=["--disable-blink-features=AutomationControlled"],
        )
        if config.BROWSER_CHANNEL:
            kwargs["channel"] = config.BROWSER_CHANNEL
        try:
            ctx = await pw.chromium.launch_persistent_context(**kwargs)
        except Exception as e:
            await pw.stop()
            msg = str(e)
            if "ProcessSingleton" in msg or "lock" in msg.lower() or "already" in msg.lower():
                raise ProfileBusyError(
                    "브라우저 프로필이 사용 중입니다. 로그인 창(naver-blog-login)이 열려 있다면 닫고 다시 시도하세요."
                ) from e
            raise
        try:
            # 발행 차단 가드: 모든 페이지와 iframe에 로드 시점부터 설치됩니다.
            await ctx.add_init_script(publish_guard_js())
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            if not kwargs["headless"]:
                await bring_to_front()
            yield ctx, page
        finally:
            try:
                await ctx.close()
            finally:
                await pw.stop()


async def has_auth_cookie(ctx: BrowserContext) -> bool:
    cookies = await ctx.cookies(["https://nid.naver.com", "https://blog.naver.com"])
    names = {c["name"] for c in cookies if "naver.com" in c.get("domain", "")}
    return bool(AUTH_COOKIES & names)


async def check_login(page: Page) -> dict:
    """쿠키 확인 후 로그인이 꼭 필요한 글쓰기 주소에 들어가 봅니다.

    공개 블로그 주소(blog.naver.com/<ID>)는 로그인이 만료돼도 열리므로 확인에 쓰지 않습니다.
    글쓰기 주소는 만료된 경우 몇 초 뒤 로그인 페이지(nid.naver.com)로 넘어가므로 최대 10초 지켜봅니다.
    아무것도 입력하거나 저장하지 않습니다.
    """
    if not await has_auth_cookie(page.context):
        return {"logged_in": False, "reason": "네이버 로그인 쿠키가 없습니다."}
    target = (S.WRITE_URLS[0].format(blog_id=config.BLOG_ID) if config.BLOG_ID
              else "https://blog.naver.com/MyBlog.naver")
    await page.goto(target, wait_until="domcontentloaded", timeout=30000)
    for _ in range(20):
        if "nid.naver.com" in page.url:
            return {"logged_in": False, "reason": "로그인 페이지로 이동했습니다. 세션이 만료됐습니다."}
        await asyncio.sleep(0.5)
    return {"logged_in": True, "url": page.url}


async def interactive_login(timeout_sec: int = 300) -> dict:
    """화면이 보이는 브라우저를 열고 사용자가 직접 로그인할 때까지 기다립니다.

    아이디, 비밀번호, 2단계 인증, 캡차 모두 사람이 처리합니다.
    '로그인 상태 유지'를 체크하면 쿠키가 오래 유지됩니다.
    """
    async with browser_session(headless=False) as (ctx, page):
        if await has_auth_cookie(ctx):
            res = await check_login(page)
            if res["logged_in"]:
                return {"logged_in": True, "message": "이미 로그인되어 있습니다."}
        await page.goto(LOGIN_URL, wait_until="domcontentloaded")
        log.info("로그인 창을 열었습니다. %s초 안에 로그인하세요.", timeout_sec)
        waited = 0.0
        while waited < timeout_sec:
            try:
                if page.is_closed():
                    return {"logged_in": False, "message": "로그인 창이 닫혔습니다. 다시 open_login_window를 호출하세요."}
                if await has_auth_cookie(ctx) and "nid.naver.com" not in page.url:
                    await asyncio.sleep(2)  # 쿠키가 디스크에 기록될 시간
                    return {"logged_in": True, "message": "로그인 완료."}
            except Exception:  # 사용자가 브라우저 창 자체를 닫은 경우
                return {"logged_in": False, "message": "로그인 창이 닫혔습니다. 다시 open_login_window를 호출하세요."}
            await asyncio.sleep(1.5)
            waited += 1.5
        return {"logged_in": False, "message": f"{timeout_sec}초 안에 로그인이 완료되지 않았습니다."}


# ---------------------------------------------------------------- 백그라운드 로그인
# open_login_window가 로그인 완료까지 도구 호출을 붙잡고 있으면 Claude Desktop의 응답 시간 제한에 걸릴 수 있습니다.
# 그래서 창만 띄우고 바로 돌아오며, 기다리는 일은 서버 안의 백그라운드 작업이 합니다.

_login_task: asyncio.Task | None = None
_login_result: dict | None = None


def start_background_login(timeout_sec: int) -> dict:
    global _login_task, _login_result
    if _login_task and not _login_task.done():
        return {"status": "waiting", "message": "로그인 창이 이미 열려 있습니다. 창에서 로그인을 마쳐 주세요."}
    if _lock.locked():
        raise ProfileBusyError("다른 블로그 작업이 진행 중입니다. 끝난 뒤 다시 시도하세요.")
    _login_result = None

    async def run() -> None:
        global _login_result
        try:
            _login_result = await interactive_login(timeout_sec=timeout_sec)
        except Exception as e:  # pragma: no cover - 브라우저 실행 실패 등
            _login_result = {"logged_in": False, "message": f"로그인 창 실행 실패: {e}"}

    _login_task = asyncio.get_running_loop().create_task(run())
    return {"status": "opened", "message": f"화면에 네이버 로그인 창을 열었습니다. {timeout_sec}초 안에 직접 로그인하세요. "
            "'로그인 상태 유지'를 체크하면 오래 유지됩니다. 로그인을 마친 뒤 login_status로 확인합니다."}


def background_login_state() -> dict | None:
    """백그라운드 로그인 진행 상태. 진행한 적이 없으면 None."""
    if _login_task is None:
        return None
    if not _login_task.done():
        return {"logged_in": False, "status": "waiting", "message": "로그인 창이 열려 있습니다. 사용자가 로그인하는 중입니다."}
    return {"status": "finished", **(_login_result or {"logged_in": False, "message": "결과 없음"})}
