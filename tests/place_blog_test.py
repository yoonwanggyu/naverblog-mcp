"""장소 데이터 해석, 내 블로그 최근 글 읽기('내 스타일'), 같은 장소 리뷰 분석, 주제별 최신 경향을 로컬 가짜 페이지로 검증합니다.
네이버에 접속하지 않습니다.

    BROWSER_CHANNEL=chrome uv run python tests/place_blog_test.py
"""

import asyncio
import functools
import http.server
import os
import sys
import tempfile
import threading
from pathlib import Path

tmp = Path(tempfile.mkdtemp(prefix="nbw_pb_"))
site = tmp / "site"
site.mkdir()
os.environ["NBW_NO_DOTENV"] = "1"  # 테스트는 레포의 .env를 읽지 않습니다
os.environ.update(NBW_HOME=str(tmp / "home"), NAVER_BLOG_ID="myblog", HEADLESS=os.environ.get("HEADLESS", "true"),
                  MEDIA_ROOTS=str(tmp))

# 가짜 RSS: 글 7개 -> 최근 5개만 읽어야 함
items = "".join(
    f"<item><title>테스트 글 {i}</title><link>https://blog.naver.com/myblog/22400000000{i}?fromRss=true</link>"
    f"<guid>https://blog.naver.com/myblog/22400000000{i}</guid><category>맛집</category><tag>맛집,데이트{i}</tag>"
    f"<pubDate>Fri, 1{i} Nov 2026 11:30:00 +0900</pubDate></item>"
    for i in range(7, 0, -1)
)
(site / "rss.xml").write_text(f'<?xml version="1.0" encoding="UTF-8"?><rss><channel>{items}</channel></rss>', encoding="utf-8")
(site / "empty.xml").write_text('<?xml version="1.0"?><rss><channel></channel></rss>', encoding="utf-8")
for i in range(1, 8):
    (site / f"post_22400000000{i}.html").write_text(f"""<!doctype html><html><head><meta charset="utf-8"></head><body>
<div class="se-title-text">테스트 글 {i}</div>
<div class="se-main-container">
 <div class="se-component se-text"><p class="se-text-paragraph se-text-paragraph-align-center">주말에 일산으로 데이트를 다녀왔어요</p><p class="se-text-paragraph se-text-paragraph-align-center">날씨가 맑았거든요 😊</p></div>
 <div class="se-component se-image"><img src="a.png"></div>
 <div class="se-component se-quotation"><p>{i}번 글 주차는 어떻게 하나요?</p></div>
 <div class="se-component se-text"><p>건물 지하 주차장을 이용했어요. 2시간 무료였어요.</p></div>
 <div class="se-component se-image"><img src="b.png"></div>
 <div class="se-component se-placesMap"><p>지도</p></div>
 <div class="se-component se-text"><p class="se-text-paragraph se-text-paragraph-align-left">다음 글에서 또 만나요.</p></div>
</div>
<div class="post_tag"><a>#맛집</a><a>#데이트{i}</a></div></body></html>""", encoding="utf-8")

# 같은 장소의 다른 사람 리뷰(플레이스 '블로그 리뷰' 목록과 글). 카페 글과 중복은 빠지고 최신순이어야 함
import datetime as _dt  # noqa: E402
import json as _json  # noqa: E402

REVIEW_STATE = {
    "FsasReview:blog_a": {"type": "blog", "url": "https://m.blog.naver.com/rv_old/501", "title": "일산 젠스시 오래된 후기", "date": "2025.01.05."},
    "FsasReview:blog_b": {"type": "blog", "url": "https://m.blog.naver.com/rv_new/502", "title": "[일산 맛집] 젠스시｜런치 오마카세", "createdString": "2일 전"},
    "FsasReview:blog_c": {"type": "blog", "url": "https://m.blog.naver.com/rv_mid/503", "title": "일산 젠스시 런치 후기", "date": "2026.09.21."},
    "FsasReview:blog_c2": {"type": "blog", "url": "https://m.blog.naver.com/rv_mid/503", "title": "일산 젠스시 런치 후기", "date": "2026.09.21."},
    "FsasReview:cafe_x": {"type": "cafe", "url": "https://m.cafe.naver.com/x/1", "title": "카페 글", "date": "2026.10.01."},
}
(site / "reviews.html").write_text("<!doctype html><html><head><meta charset='utf-8'></head><body><script>window.__APOLLO_STATE__ = " + _json.dumps(REVIEW_STATE, ensure_ascii=False)
                                    + ";</script></body></html>", encoding="utf-8")


def other_post(title, lines, price):
    paras = "".join(f'<p class="se-text-paragraph se-text-paragraph-align-center">{x}</p>' for x in lines)
    return f"""<!doctype html><html><head><meta charset="utf-8"></head><body><div class="se-title-text">{title}</div>
<div class="se-main-container">
 <div class="se-component se-image"><img class="se-image-resource" src="a.png"></div>
 <div class="se-component se-text">{paras}</div>
 <div class="se-component se-placesMap"><p>지도</p></div>
 <div class="se-component se-quotation se-l-quotation_line"><div class="se-quote">메뉴</div></div>
 <div class="se-component se-text"><p class="se-text-paragraph se-text-paragraph-align-center">{price}</p>
  <p class="se-text-paragraph se-text-paragraph-align-center">주차 : 건물 지하 2시간 무료</p>
  <p class="se-text-paragraph se-text-paragraph-align-center">저의 단골 가게 오늘 소개해 드릴게요!</p></div>
 <div class="se-component se-image"><img class="se-image-resource" src="b.png"></div>
</div></body></html>"""


for bid, no, title, price in (("rv_old", "501", "일산 젠스시 오래된 후기", "런치 39,000원"),
                              ("rv_new", "502", "[일산 맛집] 젠스시｜런치 오마카세", "런치 오마카세 : 45,000원"),
                              ("rv_mid", "503", "일산 젠스시 런치 후기", "런치는 45,000원")):
    (site / f"other_{bid}_{no}.html").write_text(other_post(title, ["카운터 자리에서 셰프님이 한 점씩 쥐어 주셨어요", "주말 점심에 다녀왔어요"], price),
                                               encoding="utf-8")
# 주제별 인기글과 최신글 목록 (같은 블로그 두 번, 쇼핑 글은 빠져야 함)
(site / "top.json").write_text(")]}',\n" + _json.dumps({"result": [
    {"domainIdOrBlogId": "rv_new", "logNo": 502}, {"domainIdOrBlogId": "rv_new", "logNo": 999},
    {"domainIdOrBlogId": "shop", "logNo": 777, "isMarketPost": True}]}), encoding="utf-8")
(site / "recent.json").write_text(_json.dumps({"result": {"postList": [{"domainIdOrBlogId": "rv_mid", "logNo": 503}]}}), encoding="utf-8")

handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(site))
handler.log_message = lambda *a: None
httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{httpd.server_address[1]}"

from naver_blog_writer import config  # noqa: E402
from naver_blog_writer.core import archive, blog_reader, place, place_reviews, post_analysis, trends  # noqa: E402
from naver_blog_writer.tools.get_style_guide import get_style_guide  # noqa: E402
from naver_blog_writer.tools.place_reviews import place_reviews as place_reviews_tool  # noqa: E402

blog_reader.RSS_URL = base + "/rss.xml?{blog_id}"
blog_reader.POST_URL = base + "/post_{log_no}.html?b={blog_id}"
blog_reader.TITLE_LIST_URL = base + "/none.json?{blog_id}&{limit}"
post_analysis.POST_URL = base + "/other_{blog_id}_{log_no}.html"
place_reviews.REVIEWS_URL = base + "/reviews.html?{place_id}"
trends.TOP_URL = base + "/top.json?{seq}"
trends.RECENT_URL = base + "/recent.json?{seq}&{page}"

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


# 1) 장소 데이터 해석 (네이버 지도 검색 결과 형태)
STATE = {
    "PlaceListBusinessesItem:1699893670": {"id": "1699893670", "name": "젠스시", "category": "일식당", "address": "대화동 2307-12",
                                           "roadAddress": "킨텍스로 255 상가234호", "x": "126.7504090", "y": "37.6689196", "phone": "031-923-5415"},
    "PlaceListBusinessesItem:37111858": {"id": "37111858", "name": "젠스시", "category": "초밥,롤", "address": "진영리 1606-9",
                                         "roadAddress": "김해대로357번길 5-1", "x": "128.7290024", "y": "35.3074239"},
    "Panorama:abc": {"id": "abc", "lat": 1},
}
parsed = place.parse_apollo_state(STATE)
check("장소 데이터 해석", len(parsed) == 2 and parsed[0]["road_address"] == "킨텍스로 255 상가234호", str([p["name"] for p in parsed]))
d = place._distance_m(37.668686, 126.750175, 37.6689196, 126.7504090)
check("사진 GPS와 거리 계산", 20 < d < 50, f"{d:.0f}m")


async def main():
    # 2) 최근 글 5개 읽기 + 내 스타일 생성 (브라우저로 각 글을 엶)
    res = await get_style_guide()
    sync = res["recent_posts_sync"]
    tr = res["trend_sync"]
    check("최신 경향 첫 분석(블로그당 1편, 쇼핑 글 제외)", tr.get("refreshed") is True and tr.get("posts") == 2, str(tr))
    check("가이드에 부록 B 최신 경향이 붙음", "# 부록 B. 최신 경향 (자동 갱신" in res["guide"] and "가운데 정렬 2/2" in res["guide"])
    tdata = trends.load("맛집")
    check("최신 경향에는 본문과 글 주소를 저장하지 않음", "카운터 자리에서" not in _json.dumps(tdata, ensure_ascii=False)
          and "rv_new" not in _json.dumps(tdata, ensure_ascii=False) and tdata.get("source_kinds") == {"인기": 1, "최신": 1},
          str(tdata.get("source_kinds")))
    check("최근 게시물 5개만 읽기(7개 중)", sync.get("posts_read") == 5 and sync["titles"][0] == "테스트 글 7", str(sync)[:120])
    cache = blog_reader.load_cache()
    p0 = cache["posts"][0]
    check("본문, 소제목, 사진, 태그, 구성 읽기", p0["image_count"] == 2 and p0["subtitles"] == ["7번 글 주차는 어떻게 하나요?"]
          and "데이트7" in p0["tags"] and p0["layout"][:3] == ["text", "image", "subtitle"], str({k: p0[k] for k in ("image_count", "tags", "layout")}))
    check("한 줄 길이와 가운데 정렬 비율 읽기", p0.get("line_count") == 3 and p0.get("center_ratio") == 0.67,
          str({k: p0.get(k) for k in ("line_count", "avg_line_chars", "center_ratio")}))
    guide = res["guide"]
    check("가이드에 10절 내 스타일이 붙음", "10절. 내 스타일" in guide and "테스트 글 7" in guide and "주말에 일산으로" in guide)
    check("my_style.md 파일 갱신", config.MY_STYLE_PATH.is_file() and "자주 쓰는 문장 끝" in config.MY_STYLE_PATH.read_text(encoding="utf-8"))
    check("겹침 비교 대상 = 블로그 최근 글", sum(1 for p in archive.comparison_posts() if p["source"] == "블로그") == 5)
    w = archive.overlap_warnings("새 글 제목입니다", None, None, ["7번 글 주차는 어떻게 하나요?"], "완전히 새로운 문장입니다.")
    check("블로그 글과 같은 소제목 경고", any("소제목" in x for x in w), str(w)[:80])
    check("읽은 시각 기록(write_post 허용 조건)", (blog_reader.sync_age_minutes() or 99) < 1)

    # 3) 읽기 실패(네트워크 오류) 시 이전에 읽은 5개를 지우지 않고 유지
    good_rss, good_api = blog_reader.RSS_URL, blog_reader.TITLE_LIST_URL
    blog_reader.RSS_URL = "http://127.0.0.1:9/rss.xml?{blog_id}"
    blog_reader.TITLE_LIST_URL = "http://127.0.0.1:9/x?{blog_id}&{limit}"
    res = await get_style_guide()
    sync = res["recent_posts_sync"]
    check("읽기 실패 시 이전 5개 유지", sync.get("refreshed") is False and sync.get("posts_in_cache") == 5
          and len(blog_reader.load_cache()["posts"]) == 5 and "10절. 내 스타일" in res["guide"], str(sync)[:120])
    blog_reader.RSS_URL, blog_reader.TITLE_LIST_URL = good_rss, good_api

    check("최신 경향은 7일 안에는 다시 읽지 않음", res["trend_sync"].get("refreshed") is False and res["trend_sync"].get("posts") == 2,
          str(res["trend_sync"]))

    # 4) 같은 장소의 다른 사람 리뷰 분석
    today = _dt.date(2026, 11, 18)
    check("리뷰 날짜 해석", place_reviews.parse_date("2026.09.21.", today) == _dt.date(2026, 9, 21)
          and place_reviews.parse_date("2일 전", today) == _dt.date(2026, 11, 16) and place_reviews.parse_date("어제", today) == _dt.date(2026, 11, 17))
    r = await place_reviews_tool("1699893670", "젠스시")
    order = [x["url"].split("/")[-2] for x in r["reviews"]]
    check("블로그 리뷰만 최신순(카페 글, 중복 제외)", order == ["rv_new", "rv_mid", "rv_old"], str(order))
    check("형식 요약(사진, 정렬, 지도 위치, 소제목)", r["format_summary"]["images"] == 2 and r["format_summary"]["center_posts"] == "3/3"
          and r["format_summary"]["place"]["middle"] == 3 and r["format_summary"]["common_subtitles"] == ["메뉴"], str({k: r["format_summary"].get(k) for k in ("images", "center_posts", "place", "common_subtitles")}))
    check("최근 6개월 2곳 이상 같은 가격만 사실 후보", list(r["price_agreed_recent"]) == ["45,000원"], str(r["price_agreed_recent"]))
    info = r["reviews"][0]["info_lines"]
    check("정보 줄만 남기고 감상 문장은 뺌", "주차 : 건물 지하 2시간 무료" in info and not any("소개해 드릴게요" in x for x in info), str(info))
    check("돌려주는 결과에 본문 문장 없음", "카운터 자리에서" not in _json.dumps(r, ensure_ascii=False))
    w = archive.overlap_warnings("일산 젠스시 런치 오마카세 다녀온 날", "일산 젠스시", None, ["메뉴"],
                                 "주말에 다녀왔어요. 카운터 자리에서 셰프님이 한 점씩 쥐어 주셨어요. 끝.")
    check("같은 장소 리뷰와 거의 같은 문장 경고", any("다른 사람 리뷰와 거의 같은 문장" in x for x in w), str(w)[:120])
    check("같은 장소 리뷰와 같은 소제목 경고", any("소제목" in x for x in w))
    check("같은 장소 리뷰와는 메인 키워드를 비교하지 않음", not any("메인 키워드" in x for x in w))

    # 5) 24시간이 지난 같은 장소 리뷰 기록은 파일째 지움
    import time as _time
    rec_file = config.HOME / "place_reviews" / "1699893670.json"
    d = _json.loads(rec_file.read_text(encoding="utf-8"))
    d["analyzed_ts"] = _time.time() - 25 * 3600
    rec_file.write_text(_json.dumps(d, ensure_ascii=False), encoding="utf-8")
    check("24시간 지난 리뷰 기록은 파일째 삭제", place_reviews.load_recent_for_overlap() == [] and not rec_file.exists())

    # 6) 다른 블로그 읽기를 끄면(READ_OTHER_BLOGS=false) 읽지 않고, 경향도 가이드에 붙지 않음
    config.READ_OTHER_BLOGS = False
    off = await place_reviews_tool("1699893670", "젠스시")
    res_off = await get_style_guide()
    check("끄면 같은 장소 리뷰를 읽지 않음", off.get("skipped") is True and not rec_file.exists(), str(off)[:60])
    check("끄면 경향을 붙이지 않음", "# 부록 B. 최신 경향 (자동 갱신" not in res_off["guide"] and "READ_OTHER_BLOGS" in res_off["trend_sync"].get("note", ""))
    config.READ_OTHER_BLOGS = True

    # 7) 게시물이 하나도 없는 블로그
    blog_reader.RSS_URL = base + "/empty.xml?{blog_id}"
    res = await get_style_guide()
    check("게시물이 없으면 기본 가이드만", res["recent_posts_sync"].get("posts_read") == 0
          and "아직 읽을 수 있는 게시물이 없습니다" in res["guide"], str(res["recent_posts_sync"])[:100])



asyncio.run(main())
sys.exit(0 if all(results) else 1)
