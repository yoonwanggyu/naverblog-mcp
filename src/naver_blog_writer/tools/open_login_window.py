"""open_login_window: 사용자가 직접 로그인하는 창을 엽니다."""

from mcp.types import ToolAnnotations

from ..core.browser import start_background_login

ANNOTATIONS = ToolAnnotations(title="로그인 창 열기", readOnlyHint=False, openWorldHint=True)


async def open_login_window(timeout_sec: int = 300) -> dict:
    """화면에 브라우저를 띄워 사용자가 직접 네이버에 로그인하게 합니다. 비밀번호는 저장하지 않습니다.

    창만 띄우고 바로 돌아옵니다. 사용자에게 창에서 로그인하라고 안내하고,
    사용자가 다 했다고 하면 login_status로 확인합니다.
    """
    return start_background_login(timeout_sec=max(60, min(timeout_sec, 900)))
