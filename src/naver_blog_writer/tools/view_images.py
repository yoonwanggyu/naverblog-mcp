"""view_images: 사진을 축소해 Claude에게 보여 줍니다."""

import asyncio

from mcp.server.fastmcp import Image
from mcp.types import ToolAnnotations

from .. import config
from ..core import media, writing_sources

ANNOTATIONS = ToolAnnotations(title="사진 보기", readOnlyHint=True, openWorldHint=False)


async def view_images(paths: list[str], guide_ref: str, max_side: int = 1024) -> list:
    """사진을 축소해서 Claude가 직접 볼 수 있게 돌려줍니다. 한 번에 최대 10장입니다.

    guide_ref는 이번 글의 get_style_guide.writing_ref입니다. 보여 준 JPEG를 그대로 이번 원고 자료에 연결합니다(같은 사진을 다시 보면 같은 V번호, 한 장으로 셈).
    응답이 너무 커지지 않게 전체 크기가 예산을 넘으면 사진을 자동으로 더 작게 줄입니다.
    """
    if len(paths) > 10:
        raise ValueError("한 번에 최대 10장까지 볼 수 있습니다. 나눠서 호출하세요.")
    max_side = max(256, min(max_side, 1568))
    checked = [media.check_allowed(raw) for raw in paths]
    ok = [p for p in checked if p.suffix.lower() in media.IMAGE_EXT]
    datas, side = await asyncio.to_thread(media.previews_within_budget, ok, max_side, config.PREVIEW_BUDGET_BYTES)
    labels = writing_sources.collection(guide_ref).previews(list(zip(ok, datas, strict=True)), "photo")
    previews = iter(zip(datas, labels, strict=True))
    out: list = []
    if side < max_side:
        out.append(f"(응답 크기 제한 때문에 긴 변 {side}px로 줄였습니다. 글자를 자세히 봐야 하면 장수를 줄여 다시 호출하세요.)")
    for i, p in enumerate(checked, 1):
        if p.suffix.lower() not in media.IMAGE_EXT:
            out.append(f"[{i}] 이미지가 아닙니다: {p}")
            continue
        jpeg, label = next(previews)
        out.append(f"[{i}] {p} ({label})")
        out.append(Image(data=jpeg, format="jpeg"))
    return out
