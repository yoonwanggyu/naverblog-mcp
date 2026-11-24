"""get_style_guide: 내 블로그 최근 게시물로 '내 스타일'을, 주제별 인기글로 '최신 경향'을 갱신하고 전체 스타일 가이드를 돌려줍니다."""

from mcp.types import ToolAnnotations

from .. import config
from ..core import blog_reader, style, trends, writing_sources
from ..core.browser import ProfileBusyError, browser_session
from .refresh_trends import Topic

ANNOTATIONS = ToolAnnotations(title="스타일 가이드 읽기 (내 글 5개, 최신 경향 반영)", readOnlyHint=True, openWorldHint=True)


async def get_style_guide(topic: Topic = "맛집", refresh: bool = True) -> dict:
    """블로그 글쓰기 요청이면 가장 먼저 호출합니다. '써줘', '정리해줘', '후기' 등 표현과 상관없이 글쓰기 요청이면 항상 호출합니다.

    topic: 쓸 글의 주제(맛집, 카페, 데이트, 여행, 해외여행, 결혼준비, 취업준비). 식당 후기는 '맛집'.
    1) 화면에 보이는 브라우저로 내 블로그(NAVER_BLOG_ID)의 최근 게시물을 최대 5개 읽어 '내 스타일'(10절)을 새로 만듭니다.
    2) 그 주제의 '최신 경향'(부록 B)이 7일보다 오래됐으면 네이버 주제별 인기글을 다시 분석합니다(최대 15편, 수치만).
       READ_OTHER_BLOGS=false면 이 단계는 건너뜁니다.
    3) 기본 가이드 + 최신 경향 + 내 스타일 + 개인 고정 규칙을 합친 전체 가이드를 돌려줍니다. 이 가이드대로만 씁니다.
    최근 게시물은 말투의 기준이자, 새 글이 겹치지 않는지 write_post가 검사하는 비교 대상입니다.
    """
    trends.topic_info(topic)
    sync: dict = {"refreshed": False}
    trend: dict = {"refreshed": False}
    if refresh and config.BLOG_ID:
        try:
            async with browser_session() as (_ctx, page):
                try:
                    data = await blog_reader.sync_recent_posts(page, config.BLOG_ID)
                    sync = {"refreshed": True, "posts_read": len(data["posts"]), "synced_at": data["synced_at"],
                            "titles": [p["title"] for p in data["posts"]], "errors": data["errors"]}
                    if not data["posts"]:
                        sync["note"] = "아직 읽을 수 있는 게시물이 없어 기본 가이드만 적용합니다."
                except Exception as e:
                    data = blog_reader.record_sync_failure(config.BLOG_ID, str(e))
                    sync = {"refreshed": False, "error": f"최근 게시물을 읽지 못했습니다: {e}",
                            "using_cache_from": data.get("synced_at"), "posts_in_cache": len(data.get("posts", []))}
                if not config.READ_OTHER_BLOGS:
                    trend = {"refreshed": False, "note": "READ_OTHER_BLOGS=false라 주제별 인기글 경향을 쓰지 않습니다."}
                else:
                    try:
                        trend = await trends.refresh(page, topic)
                    except Exception as e:  # 경향 갱신 실패는 글쓰기를 막지 않습니다(이전 결과가 있으면 그대로 씀)
                        old = trends.load(topic)
                        trend = {"refreshed": False, "error": f"최신 경향을 갱신하지 못했습니다: {e}",
                                 "using_cache_from": old.get("updated_at") if old else None}
        except ProfileBusyError:
            raise
        except Exception as e:  # 브라우저를 열지 못한 경우: 이전에 읽어 둔 내 글과 경향을 그대로 씁니다
            data = blog_reader.record_sync_failure(config.BLOG_ID, str(e))
            sync = {"refreshed": False, "error": f"브라우저를 열지 못해 최근 게시물을 읽지 못했습니다: {e}",
                    "using_cache_from": data.get("synced_at"), "posts_in_cache": len(data.get("posts", []))}
    elif not config.BLOG_ID:
        sync = {"refreshed": False, "error": "NAVER_BLOG_ID가 없어 최근 게시물을 읽지 않았습니다."}
    out = style.load_style_guide(topic)
    out["writing_ref"] = writing_sources.remember("guide", {"topic": topic, "guide": out["guide"], "read_other_blogs": config.READ_OTHER_BLOGS})
    out["draft_writer"] = config.DRAFT_WRITER
    out["recent_posts_sync"] = sync
    out["trend_sync"] = trend
    return out
