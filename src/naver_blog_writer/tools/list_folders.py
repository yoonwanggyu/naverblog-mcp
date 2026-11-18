"""list_folders: 사진 루트에서 글 폴더를 찾습니다. 날짜 형식이 달라도, 이름에 오타가 있어도 찾습니다."""

import asyncio

from mcp.types import ToolAnnotations

from .. import config
from ..core import folders

ANNOTATIONS = ToolAnnotations(title="블로그 사진 폴더 찾기", readOnlyHint=True, openWorldHint=False)


async def list_folders(query: str = "", limit: int = 20) -> dict:
    """사진 작업은 항상 이 도구로 시작합니다. query에는 사용자가 말한 날짜와 장소를 그대로 넣습니다(예: '11월24일 제스시').

    날짜는 형식이 달라도 같은 날로 봅니다(11월 14일 = 26.11.14 = 11/14 = 2026-11-14). 장소명은 오타를 허용합니다.
    - match='exact'이고 needs_confirmation=false면 best.path로 바로 scan_media를 호출합니다.
    - needs_confirmation=true면 confirm_question을 그대로 사용자에게 보여 주고 답을 기다립니다. 추측해서 진행하지 않습니다.
      사용자가 맞다고 하면 scan_media(folder=best.path, user_confirmed=true)로 진행하고, 아니라고 하면 다시 묻습니다.
    """
    if not config.MEDIA_ROOTS:
        raise ValueError("MEDIA_ROOTS가 비어 있습니다. Claude Desktop 설정에 Google Drive의 블로그 사진 폴더를 지정하세요.")
    res = await asyncio.to_thread(folders.search_folders, config.MEDIA_ROOTS, query)
    limit = max(1, min(limit, 100))
    out = {
        "roots": [str(r) for r in config.MEDIA_ROOTS],
        "query": query,
        "parsed": res["parsed"],
        "match": res["match"],
        "needs_confirmation": res["needs_confirmation"],
        "confirm_question": res["confirm_question"],
        "best": res["best"],
        "candidates": res["candidates"][:limit],
    }
    if res["match"] == "list":
        out["folders"] = res["folders"][:limit]
        out["truncated"] = len(res["folders"]) > limit
    if res["needs_confirmation"]:
        out["next"] = "confirm_question을 사용자에게 그대로 보여 주고 '이게 맞습니까?'에 대한 답을 받은 뒤에만 진행하세요."
    return out
