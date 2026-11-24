"""find_place: 네이버 지도에서 장소를 찾아 주소와 지도 블록 정보를 돌려줍니다."""

import asyncio
import re

from mcp.types import ToolAnnotations

from ..core import media, place, writing_sources
from ..core.browser import browser_session

ANNOTATIONS = ToolAnnotations(title="네이버 지도에서 장소 찾기", readOnlyHint=True, openWorldHint=True)


async def find_place(query: str, folder: str = "") -> dict:
    """장소 이름(예: '젠스시')으로 네이버 지도를 검색해 정확한 위치를 찾습니다. 장소 리뷰 글은 write_post 전에 반드시 호출합니다.

    folder에 사진 폴더 경로를 주면 사진의 GPS 위치를 기준으로 검색해 가장 가까운 곳을 고릅니다(같은 이름의 지점 구분).
    브라우저 창이 화면에 보이며 검색 과정을 사용자가 볼 수 있습니다.
    결과의 place_block을 write_post의 place 블록으로 그대로 써서 도입 바로 다음에 두고, checklist_line(📍 주소)은 지도 아래 정보 줄에 씁니다.
    confident=false면 후보 목록을 사용자에게 보여 주고 어느 곳인지 확인받습니다.
    위치가 정해지면 place_id로 place_reviews를 호출해 같은 장소의 최신 리뷰 10편을 분석합니다.
    """
    q = query.strip()
    if not q:
        raise ValueError("검색할 장소 이름이 비어 있습니다.")
    gps = None
    if folder:
        f = media.check_allowed(folder)
        gps = await asyncio.to_thread(media.folder_gps, f)
    async with browser_session() as (_ctx, page):
        res = await place.search_places(page, q, lat=gps["lat"] if gps else None, lon=gps["lon"] if gps else None)
    best = res["best"]
    if best:
        dong = ""
        m = re.search(r"([가-힣0-9]+(동|읍|면|리|가))\b", best.get("address") or "")
        if m:
            dong = m.group(1)
        res["place_block"] = {
            "type": "place",
            "query": f"{best['name']} {dong}".strip(),
            "address": best.get("road_address") or best.get("address"),
            "place_id": best["place_id"],
        }
        res["checklist_line"] = f"📍 {best.get('road_address') or best.get('address')}"
        res["place_id"] = best["place_id"]
        res["next_step"] = f"place_reviews(place_id='{best['place_id']}', place_name='{best['name']}')로 같은 장소의 최신 리뷰를 분석하세요."
    if not res["confident"]:
        res["next"] = "위치를 확신할 수 없습니다. candidates를 사용자에게 보여 주고 어느 곳인지 확인받으세요."
    if not gps:
        res["note"] = "사진에 GPS 정보가 없어 지도 기본 위치(서울) 기준으로 검색했습니다."
    writing_sources.attach_places(res)
    return res
