"""inspect_editor: 에디터 셀렉터 진단."""

from mcp.types import ToolAnnotations

from ..core import editor
from ..core.browser import browser_session

ANNOTATIONS = ToolAnnotations(title="에디터 진단", readOnlyHint=True, openWorldHint=True)


async def inspect_editor() -> dict:
    """글쓰기 화면을 열어 셀렉터가 맞는지 점검합니다. 아무것도 입력하거나 저장하지 않습니다.

    네이버 화면이 바뀌어 write_post가 실패할 때 사용하고, 결과를 보고 core/selectors.py를 고칩니다.
    """
    async with browser_session() as (_ctx, page):
        frame = await editor.open_editor(page)
        return await editor.inspect(frame)
