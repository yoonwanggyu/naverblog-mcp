"""recent_posts: 겹침 비교 대상인 최근 글(내 블로그 최근 게시물 + 임시저장 기록)을 돌려줍니다."""

from mcp.types import ToolAnnotations

from ..core import archive, blog_reader

ANNOTATIONS = ToolAnnotations(title="최근 글 기록", readOnlyHint=True, openWorldHint=False)


async def recent_posts(include_body: bool = False) -> dict:
    """새 글이 겹치면 안 되는 최근 글 목록입니다. 브라우저를 열지 않고 저장된 기록만 읽습니다.

    blog_posts: get_style_guide가 마지막으로 읽은 내 블로그 최근 게시물(최대 5개).
    drafts: 이 서버가 임시저장했지만 아직 블로그에 없는 글. 최신 정보가 필요하면 get_style_guide를 먼저 호출합니다.
    """
    cache = blog_reader.load_cache()
    keep = (lambda p: p) if include_body else (lambda p: {k: v for k, v in p.items() if k != "body"})
    blog = [keep(p) for p in cache.get("posts", [])]
    drafts = [keep(p) for p in archive.comparison_posts() if p["source"] == "임시저장"]
    return {
        "blog_posts_synced_at": cache.get("synced_at"),
        "blog_posts": blog,
        "drafts": drafts,
        "last_hook_pattern": drafts[0].get("hook_pattern") if drafts else None,
    }
