"""네이버 지도에서 장소 찾기.

화면에 보이는 브라우저로 네이버 지도 검색 페이지를 열고, 검색 결과 목록(iframe)에 담긴 장소 데이터
(이름, 업종, 주소, 좌표, 전화번호)를 읽습니다. 사진의 GPS가 있으면 그 위치를 지도 중심으로 검색하고
가장 가까운 장소를 고릅니다. 이 결과(주소, 업종)는 글에 쓸 수 있는 확인된 사실로 취급합니다.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
import unicodedata
from typing import Any
from urllib.parse import quote

from playwright.async_api import Frame, Page

from .. import config

log = logging.getLogger(__name__)

MAP_SEARCH_URL = "https://map.naver.com/p/search/{query}"
LIST_URL = "https://pcmap.place.naver.com/place/list?query={query}&x={lon}&y={lat}&display=20"
LIST_FRAME_HINT = "pcmap.place.naver.com/place/list"
DEFAULT_CENTER = (37.5666, 126.9784)  # GPS가 없을 때 지도 중심(서울시청)
CONFIDENT_METERS = 300                # 사진 위치에서 이 거리 안이면 같은 장소로 확신합니다


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def parse_apollo_state(state: dict[str, Any]) -> list[dict[str, Any]]:
    """검색 결과 페이지의 window.__APOLLO_STATE__에서 장소 목록을 뽑습니다."""
    out: list[dict[str, Any]] = []
    for key, v in (state or {}).items():
        if not isinstance(v, dict) or not v.get("name") or not (v.get("address") or v.get("roadAddress")):
            continue
        if not (key.startswith("PlaceListBusinessesItem:") or key.startswith("PlaceSummary:") or v.get("x")):
            continue
        try:
            x, y = float(v.get("x")), float(v.get("y"))
        except (TypeError, ValueError):
            x = y = None  # type: ignore[assignment]
        full = v.get("fullAddress") or v.get("commonAddress")
        out.append({
            "place_id": str(v.get("id") or key.split(":")[-1]),
            "name": unicodedata.normalize("NFC", v["name"]),
            "category": v.get("category"),
            "address": v.get("address"),
            "road_address": v.get("roadAddress"),
            "full_address": full,
            "phone": v.get("phone") or v.get("virtualPhone"),
            "lon": x,
            "lat": y,
        })
    seen: set[str] = set()
    uniq = []
    for p in out:
        if p["place_id"] not in seen:
            seen.add(p["place_id"])
            uniq.append(p)
    return uniq


async def _list_frame(page: Page, timeout_s: float) -> Frame | None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        for f in page.frames:
            if LIST_FRAME_HINT in f.url:
                return f
        await asyncio.sleep(0.4)
    return None


async def _read_state(frame: Frame | Page, timeout_s: float = 10) -> dict | None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            st = await frame.evaluate("() => window.__APOLLO_STATE__ || null")
            if st:
                return st
        except Exception:
            pass
        await asyncio.sleep(0.5)
    return None


async def search_places(page: Page, query: str, lat: float | None = None, lon: float | None = None,
                        limit: int = 5) -> dict[str, Any]:
    """네이버 지도에서 장소를 검색합니다. 사용자가 화면에서 검색 과정을 볼 수 있습니다."""
    c_lat, c_lon = (lat, lon) if lat is not None and lon is not None else DEFAULT_CENTER
    url = MAP_SEARCH_URL.format(query=quote(query)) + f"?c={c_lon},{c_lat},15,0,0,0,dh"
    log.info("장소 검색: %s", url)
    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    frame = await _list_frame(page, 15)
    state = await _read_state(frame) if frame else None
    source = "map"
    if not state:  # 지도 화면 구조가 바뀌었으면 검색 목록 페이지를 직접 엽니다.
        await page.goto(LIST_URL.format(query=quote(query), lon=c_lon, lat=c_lat), wait_until="domcontentloaded", timeout=45000)
        state = await _read_state(page)
        source = "list"
    if not config.HEADLESS:  # 화면으로 지켜보는 사용자가 검색 결과를 볼 수 있게 잠깐 둡니다.
        await page.wait_for_timeout(2000)
    places = parse_apollo_state(state or {})
    if lat is not None and lon is not None:
        for p in places:
            if p["lat"] is not None:
                p["distance_m"] = round(_distance_m(lat, lon, p["lat"], p["lon"]))
        places.sort(key=lambda p: p.get("distance_m", 10**9))
    for p in places:
        p["map_url"] = f"https://map.naver.com/p/entry/place/{p['place_id']}"
    best = places[0] if places else None
    confident = bool(best and best.get("distance_m") is not None and best["distance_m"] <= CONFIDENT_METERS)
    if best and lat is None and len(places) == 1:
        confident = True
    return {"query": query, "center": {"lat": c_lat, "lon": c_lon, "from_photo_gps": lat is not None},
            "source": source, "count": len(places), "best": best, "confident": confident, "candidates": places[:limit]}
