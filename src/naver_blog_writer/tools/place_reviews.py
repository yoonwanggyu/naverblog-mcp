"""place_reviews: 같은 장소의 최신 블로그 리뷰를 읽어 형식, 차별점, 사실 후보를 정리합니다."""

from mcp.types import ToolAnnotations

from .. import config
from ..core import place_reviews as core
from ..core.browser import ProfileBusyError, browser_session

ANNOTATIONS = ToolAnnotations(title="같은 장소 최신 리뷰 분석", readOnlyHint=True, openWorldHint=True)


async def place_reviews(place_id: str, place_name: str = "") -> dict | str:
    """장소 글이면 find_place 다음에 반드시 호출합니다(장소마다). 호출하지 않으면 write_post가 저장을 거부합니다. place_id는 find_place 결과의 place_id(best.place_id)입니다.

    그 장소에 지도를 첨부한 네이버 블로그 리뷰를 최신순으로 최대 10편 열어(화면에 보이는 브라우저) 분량, 사진 수, 소제목,
    지도 위치, 말투를 정리하고, 다른 리뷰가 많이 다룬 내용과 덜 다룬 내용(less_covered), 가격과 영업시간 같은 정보 줄을 돌려줍니다.
    - 다른 리뷰의 문장, 제목, 소제목은 그대로 쓰지 않습니다. write_post가 이 리뷰들과 겹치는지 검사합니다(24시간 동안).
    - 정보 줄의 값은 최근 6개월 리뷰 2곳 이상이 같을 때만 쓰고(price_agreed_recent 참고), 아니면 [[확인필요]]로 둡니다.
    """
    if not config.READ_OTHER_BLOGS:
        return {"skipped": True, "note": "READ_OTHER_BLOGS=false라 같은 장소 리뷰를 읽지 않습니다. write_post는 리뷰 비교 없이 진행합니다."}
    pid = (place_id or "").strip()
    if not pid.isdigit():
        raise ValueError("place_id는 find_place 결과의 숫자 ID입니다(예: '1699893670').")
    try:
        async with browser_session() as (_ctx, page):
            return await core.analyze_place(page, pid, place_name.strip())
    except ProfileBusyError:
        raise
    except Exception as e:
        core.record_failure(pid, place_name.strip(), str(e))
        return (f"FAILED: 같은 장소 리뷰를 읽지 못했습니다: {e}\n"
                "실패 기록을 남겼으므로 write_post는 이 장소를 리뷰 비교 없이 저장합니다. 사용자에게 이 사실을 알리세요.")
