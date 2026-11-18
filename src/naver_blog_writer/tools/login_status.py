"""login_status: 저장된 브라우저 프로필의 로그인 상태."""

from mcp.types import ToolAnnotations

from .. import config
from ..core.browser import background_login_state, browser_session, check_login

ANNOTATIONS = ToolAnnotations(title="로그인 상태 확인", readOnlyHint=True, openWorldHint=True)


async def login_status() -> dict:
    """저장된 브라우저 프로필이 네이버에 로그인되어 있는지 확인합니다.

    open_login_window로 연 창이 아직 열려 있으면 브라우저를 새로 열지 않고 진행 상태만 돌려줍니다.
    확인할 때도 브라우저 창이 화면에 보입니다(HEADLESS=false일 때).
    """
    state = background_login_state()
    if state and state.get("status") == "waiting":
        state["blog_id"] = config.BLOG_ID or "(NAVER_BLOG_ID 미설정)"
        return state
    async with browser_session() as (_ctx, page):
        res = await check_login(page)
    res["blog_id"] = config.BLOG_ID or "(NAVER_BLOG_ID 미설정)"
    return res
