"""view_images: 사진을 축소해 Claude에게 보여 줍니다."""

import asyncio

from mcp.server.fastmcp import Image
from mcp.types import ToolAnnotations

from .. import config
from ..core import media

ANNOTATIONS = ToolAnnotations(title="사진 보기", readOnlyHint=True, openWorldHint=False)


async def view_images(paths: list[str], max_side: int = 1024) -> list:
    """사진을 축소해서 Claude가 직접 볼 수 있게 돌려줍니다. 한 번에 최대 10장입니다.

    흐리거나 비슷한 사진을 고르고, 사진 속 간판이나 메뉴판 글자를 확인할 때 씁니다.
    응답이 너무 커지지 않게 전체 크기가 예산을 넘으면 사진을 자동으로 더 작게 줄입니다.
    """
    if len(paths) > 10:
        raise ValueError("한 번에 최대 10장까지 볼 수 있습니다. 나눠서 호출하세요.")
    max_side = max(256, min(max_side, 1568))
    checked = [media.check_allowed(raw) for raw in paths]
    ok = [p for p in checked if p.suffix.lower() in media.IMAGE_EXT]
    datas, side = await asyncio.to_thread(media.previews_within_budget, ok, max_side, config.PREVIEW_BUDGET_BYTES)
    previews = dict(zip(ok, datas, strict=True))
    out: list = []
    if side < max_side:
        out.append(f"(응답 크기 제한 때문에 긴 변 {side}px로 줄였습니다. 글자를 자세히 봐야 하면 장수를 줄여 다시 호출하세요.)")
    for i, p in enumerate(checked, 1):
        if p not in previews:
            out.append(f"[{i}] 이미지가 아닙니다: {p}")
            continue
        out.append(f"[{i}] {p}")
        out.append(Image(data=previews[p], format="jpeg"))
    return out
