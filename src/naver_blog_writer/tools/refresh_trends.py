"""refresh_trends: 네이버 주제별 인기글로 '최신 경향'(스타일 가이드 부록 B)을 다시 만듭니다."""

from typing import Literal

from mcp.types import ToolAnnotations

from .. import config
from ..core import trends
from ..core.browser import browser_session

ANNOTATIONS = ToolAnnotations(title="주제별 최신 경향 갱신", readOnlyHint=True, openWorldHint=True)

Topic = Literal["맛집", "카페", "데이트", "여행", "해외여행", "결혼준비", "취업준비"]


async def refresh_trends(topic: Topic = "맛집", force: bool = False) -> dict:
    """네이버 블로그 주제별 인기글과 최신글(블로그당 1편, 최대 15편)의 형식 수치로 '최신 경향'을 만듭니다.

    get_style_guide가 7일마다 자동으로 갱신하므로 보통은 직접 부를 필요가 없습니다.
    사용자가 "경향 다시 분석해줘"처럼 요청할 때만 force=true로 부릅니다. 문장은 저장하지 않고 수치만 남깁니다.
    결과는 다음 get_style_guide부터 가이드의 '부록 B. 최신 경향'으로 붙습니다.
    """
    if not config.READ_OTHER_BLOGS:
        return {"refreshed": False, "note": "READ_OTHER_BLOGS=false라 다른 블로그를 읽지 않습니다. .env에서 켜야 합니다."}
    async with browser_session() as (_ctx, page):
        res = await trends.refresh(page, topic, force=force)
    data = trends.load(topic)
    if data:
        res["summary"] = trends.render(data)
    return res
