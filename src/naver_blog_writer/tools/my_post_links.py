"""my_post_links: 글 끝에 넣을 내 블로그 다른 글 목록(제목, 주소, 카테고리)."""

from mcp.types import ToolAnnotations

from .. import config
from ..core import blog_reader, writing_sources
from ..core.browser import browser_session

ANNOTATIONS = ToolAnnotations(title="내 블로그 글 링크 목록", readOnlyHint=True, openWorldHint=True)


async def my_post_links(limit: int = 30, guide_ref: str | None = None) -> dict:
    """사용자가 '내 다른 글 링크도 넣어줘'처럼 요청했을 때만 호출합니다. 내 블로그의 최근 글 목록(제목, 주소, 카테고리, 날짜)을 돌려줍니다.

    본문은 읽지 않습니다. 이번 글과 주제(카테고리)가 다른 글을 1~2개 골라 write_post의 link 블록(url)으로 마무리 상자 뒤에 넣습니다.
    링크 바로 앞 text에는 "다른 주제도 궁금하시면 여기 남겨 둘게요"처럼 부드러운 한 줄을 씁니다(스타일 가이드 6절).
    """
    if not config.BLOG_ID:
        raise ValueError("NAVER_BLOG_ID가 비어 있습니다.")
    limit = max(1, min(limit, 50))
    async with browser_session() as (_ctx, page):
        items = await blog_reader.list_recent(page, config.BLOG_ID, limit)
    posts = [{"title": it["title"], "url": f"https://blog.naver.com/{config.BLOG_ID}/{it['log_no']}",
              "category": it.get("category") or "", "date": it.get("date") or ""} for it in items]
    cats: dict[str, int] = {}
    if guide_ref:
        writing_sources.collection(guide_ref).evidence.append({"kind": "own_link_options", "posts": writing_sources.without_locations(posts)})
    for p in posts:
        cats[p["category"] or "(카테고리 정보 없음)"] = cats.get(p["category"] or "(카테고리 정보 없음)", 0) + 1
    return {"blog_id": config.BLOG_ID, "count": len(posts), "categories": cats, "posts": posts}
