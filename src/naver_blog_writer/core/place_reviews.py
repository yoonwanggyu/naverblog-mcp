"""같은 장소의 최신 블로그 리뷰 분석.

find_place로 찾은 장소(place_id)의 네이버 플레이스 '블로그 리뷰'를 최신순으로 읽습니다. 이 목록에는 그 장소에 지도를
첨부한 글만 모이므로 이름만 같은 다른 지점이 섞이지 않습니다. 블로그 글만(카페 글 제외) 최근 PLACE_REVIEWS_LIMIT편을 엽니다.

쓰임
1. 형식 비교: 같은 가게 리뷰들이 어떤 구성, 분량, 소제목으로 쓰였는지(수치)
2. 차별점: 다른 리뷰가 많이 다룬 내용과 덜 다룬 내용
3. 사실 후보: 가격, 영업시간, 주차, 예약 같은 정보 줄(60자 이하)과 날짜. 최근 리뷰 2곳 이상이 같은 값일 때만 글에 씁니다.
4. 겹침 검사: 본문 줄은 NBW_HOME/place_reviews/<place_id>.json에만 두고 write_post가 비교합니다. Claude에게는 보내지 않습니다.
   FRESH_HOURS(24시간)가 지나면 파일을 지웁니다(purge_expired). READ_OTHER_BLOGS=false면 읽지 않습니다.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import time
import unicodedata
from pathlib import Path
from typing import Any

from playwright.async_api import Page

from .. import config
from . import post_analysis

log = logging.getLogger(__name__)

REVIEWS_URL = "https://pcmap.place.naver.com/place/{place_id}/review/ugc?type=photoView&reviewSort=recent"
FRESH_HOURS = 24   # 이 시간 안에 분석한 장소 리뷰만 겹침 검사 대상에 넣습니다


def _dir() -> Path:
    d = config.HOME / "place_reviews"
    d.mkdir(parents=True, exist_ok=True)
    return d


def parse_date(s: str, today: dt.date | None = None) -> dt.date | None:
    """'2026.09.21.', '2일 전', '3시간 전', '어제' 같은 표기를 날짜로 바꿉니다."""
    today = today or dt.date.today()
    s = (s or "").strip()
    m = re.match(r"(\d{4})\.\s?(\d{1,2})\.\s?(\d{1,2})", s)
    if m:
        try:
            return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.match(r"(\d{1,2})\.\s?(\d{1,2})\.?$", s)   # 올해 날짜는 연도 없이 나오기도 합니다
    if m:
        try:
            d = dt.date(today.year, int(m.group(1)), int(m.group(2)))
            return d if d <= today else dt.date(today.year - 1, d.month, d.day)
        except ValueError:
            return None
    if re.search(r"방금|분 전|시간 전|오늘", s):
        return today
    if "어제" in s:
        return today - dt.timedelta(days=1)
    m = re.match(r"(\d+)\s?일 전", s)
    if m:
        return today - dt.timedelta(days=int(m.group(1)))
    m = re.match(r"(\d+)\s?주 전", s)
    if m:
        return today - dt.timedelta(weeks=int(m.group(1)))
    return None


def parse_review_state(state: dict[str, Any], today: dt.date | None = None) -> list[dict[str, Any]]:
    """플레이스 리뷰 페이지의 __APOLLO_STATE__에서 블로그 리뷰를 최신순으로 뽑습니다(카페 글 제외, 같은 글 하나만)."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for key, v in (state or {}).items():
        if not key.startswith("FsasReview:") or not isinstance(v, dict) or v.get("type") != "blog":
            continue
        ids = post_analysis.blog_post_ids(v.get("url") or "")
        if not ids:
            continue
        d = parse_date(v.get("date") or v.get("createdString") or "", today)
        out[ids] = {"blog_id": ids[0], "log_no": ids[1], "title": (v.get("title") or "").strip(),
                    "date": d.isoformat() if d else None, "url": f"https://blog.naver.com/{ids[0]}/{ids[1]}",
                    "own_money": v.get("buyWithMyMoneyType")}
    return sorted(out.values(), key=lambda r: r["date"] or "", reverse=True)


async def list_reviews(page: Page, place_id: str) -> list[dict[str, Any]]:
    await page.goto(REVIEWS_URL.format(place_id=place_id), wait_until="domcontentloaded", timeout=30000)
    state = None
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        state = await page.evaluate("() => window.__APOLLO_STATE__ || null")
        if state and any(k.startswith("FsasReview:") for k in state):
            break
        await page.wait_for_timeout(500)
    if not config.HEADLESS:  # 화면으로 지켜보는 사용자가 어떤 리뷰 목록을 읽는지 볼 수 있게 잠깐 둡니다.
        await page.wait_for_timeout(1500)
    return parse_review_state(state or {})


_PRICE = re.compile(r"(\d{1,3}(?:,\d{3})+|\d{4,6})\s?원")


def _price_values(lines: list[str]) -> set[str]:
    return {m.group(1).replace(",", "") for ln in lines for m in _PRICE.finditer(ln)}


def summarize(place_name: str, place_id: str, reviews: list[dict[str, Any]]) -> dict[str, Any]:
    """Claude에게 돌려줄 요약. 본문 문장은 넣지 않고 수치, 제목, 소제목, 정보 줄만 담습니다."""
    stats = [r["analysis"] for r in reviews]
    agg = post_analysis.aggregate(stats)
    n = len(reviews)
    topics = agg.get("topics", {})
    less = [t for t in post_analysis.TOPICS if topics.get(t, 0) <= n // 3]
    # 같은 가격이 몇 개 리뷰에 나왔는지 (사실 후보)
    price_count: dict[str, list[str]] = {}
    for r in reviews:
        for p in _price_values(r["analysis"]["info_lines"]):
            price_count.setdefault(p, []).append(r["date"] or "날짜 모름")
    six_months_ago = (dt.date.today() - dt.timedelta(days=183)).isoformat()
    agreed = {f"{int(p):,}원": dates for p, dates in price_count.items()
              if len([d for d in dates if d >= six_months_ago and d != "날짜 모름"]) >= 2}
    return {
        "place": {"name": place_name, "place_id": place_id},
        "reviews_read": n,
        "rules": [
            "이 리뷰들의 문장, 제목, 소제목, 도입과 마무리를 그대로 쓰거나 바꿔 쓰지 않는다. write_post가 겹침을 검사한다.",
            "형식(분량, 사진 수, 소제목 수, 지도 위치)은 참고만 하고, 말투와 구성은 스타일 가이드와 내 스타일을 따른다.",
            "info_lines의 가격, 영업시간, 주차, 예약은 최근 6개월 안의 리뷰 2곳 이상이 같은 값일 때만 쓴다. 아니면 [[확인필요]].",
            "less_covered(다른 리뷰가 덜 다룬 내용)를 내 사진과 메모로 채울 수 있으면 그 부분을 더 자세히 써서 차별점을 만든다.",
        ],
        "format_summary": agg,
        "most_covered": [t for t, c in topics.items() if c >= max(2, n // 2)],
        "less_covered": less,
        "price_agreed_recent": agreed,
        "reviews": [{
            "date": r["date"], "title": r["title"], "url": r["url"],
            "chars": r["analysis"]["chars"], "images": r["analysis"]["images"], "lines": r["analysis"]["lines"],
            "avg_line": r["analysis"]["avg_line"], "center_ratio": r["analysis"]["center_ratio"],
            "subtitles": r["analysis"]["subtitles"][:8], "place_position": r["analysis"]["place_position"],
            "tone": r["analysis"]["tone"], "info_lines": r["analysis"]["info_lines"],
        } for r in reviews],
    }


def _path(place_id: str) -> Path:
    return _dir() / f"{re.sub(r'[^0-9A-Za-z_-]', '', place_id) or 'place'}.json"


def save_for_overlap(place_id: str, place_name: str, reviews: list[dict[str, Any]]) -> Path:
    """분석 기록. 겹침 검사용 제목, 소제목, 본문 줄을 로컬에 두고, write_post가 '같은 장소를 읽었는지' 확인하는 근거가 됩니다."""
    path = _path(place_id)
    data = {"place_id": place_id, "place_name": place_name, "analyzed_ts": time.time(), "error": None,
            "posts": [{"title": r["title"], "url": r["url"], "date": r["date"],
                       "subtitles": r["analysis"]["subtitles"], "body": "\n".join(r["analysis"]["body_lines"])} for r in reviews]}
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def record_failure(place_id: str, place_name: str, error: str) -> Path:
    """리뷰를 읽으려 했지만 실패한 기록. write_post는 이 기록이 있으면 비교 없이 저장하고 안내만 남깁니다."""
    path = _path(place_id)
    data = {"place_id": place_id, "place_name": place_name, "analyzed_ts": time.time(), "error": error[:300], "posts": []}
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def purge_expired() -> int:
    """FRESH_HOURS가 지난 기록(다른 사람 글의 본문 줄 포함)을 지웁니다. 지운 파일 수."""
    n = 0
    for p in _dir().glob("*.json"):
        try:
            ts = json.loads(p.read_text(encoding="utf-8")).get("analyzed_ts", 0)
        except (OSError, json.JSONDecodeError):
            ts = 0
        if time.time() - ts > FRESH_HOURS * 3600:
            p.unlink(missing_ok=True)
            n += 1
    return n


def _fresh_records() -> list[dict[str, Any]]:
    purge_expired()
    out = []
    for p in sorted(_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return out


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", s or "")).casefold()


def find_record(place_id: str | None = None, query: str | None = None) -> dict[str, Any] | None:
    """FRESH_HOURS 안에 이 장소를 읽은 기록. place_id가 있으면 그것으로, 없으면 장소 이름이 검색어에 들어 있는지로 찾습니다."""
    recs = _fresh_records()
    if place_id:
        for r in recs:
            if str(r.get("place_id")) == str(place_id):
                return r
        return None
    q = _norm(query or "")
    for r in recs:
        name = _norm(r.get("place_name") or "")
        if name and q and (name in q or q in name):
            return r
    return None


def load_recent_for_overlap() -> list[dict[str, Any]]:
    """FRESH_HOURS 안에 분석한 모든 장소의 리뷰(겹침 비교 대상). 데이트 코스처럼 장소가 여러 곳이면 모두 비교합니다."""
    return [post for r in _fresh_records() for post in r.get("posts", [])]


async def analyze_place(page: Page, place_id: str, place_name: str, limit: int | None = None) -> dict[str, Any]:
    limit = limit or config.PLACE_REVIEWS_LIMIT
    listed = await list_reviews(page, place_id)
    reviews: list[dict[str, Any]] = []
    errors: list[str] = []
    for item in listed:
        if len(reviews) >= limit:
            break
        try:
            a = await post_analysis.read_post(page, item["blog_id"], item["log_no"])
        except Exception as e:
            errors.append(f"{item['url']}: {str(e).splitlines()[0][:80]}")
            continue
        if a is None:
            errors.append(f"{item['url']}: 스마트에디터 본문이 없어 건너뜀")
            continue
        reviews.append({**item, "analysis": a})
    save_for_overlap(place_id, place_name, reviews)
    out = summarize(place_name, place_id, reviews)
    out["listed"] = len(listed)
    if errors:
        out["errors"] = errors[:5]
    if not reviews:
        out["note"] = "이 장소에 지도를 첨부한 블로그 리뷰가 없습니다. 스타일 가이드와 최신 경향만 따릅니다."
    elif len(reviews) < limit:
        out["note"] = f"블로그 리뷰가 {len(reviews)}편뿐이라 그만큼만 분석했습니다."
    return out
