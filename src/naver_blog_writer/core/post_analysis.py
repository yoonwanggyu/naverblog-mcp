"""네이버 블로그 글 하나의 형식 분석과 여러 글의 집계.

같은 장소 리뷰(core/place_reviews.py)와 주제별 최신 경향(core/trends.py)이 함께 씁니다.
- 수집: PostView 페이지에서 블록(글, 사진, 인용구, 지도 등) 순서와 문단마다 글자, 정렬, 굵게를 읽습니다.
- 분석 결과는 수치와 형식뿐입니다(줄 길이, 정렬, 사진 수, 소제목, 지도 위치, 말투, 이모지, 제목 구조).
- 본문 문장은 결과에 넣지 않습니다. 같은 장소 리뷰의 가격, 영업시간 같은 정보 줄만 짧게(60자 이하) 남깁니다.
"""

from __future__ import annotations

import re
import statistics as st
import unicodedata
from collections import Counter
from typing import Any

from playwright.async_api import Page

POST_URL = "https://blog.naver.com/PostView.naver?blogId={blog_id}&logNo={log_no}"

COLLECT_JS = r"""
() => {
  const main = document.querySelector('.se-main-container');
  if (!main) return null;
  const comps = [...main.querySelectorAll('.se-component')].filter(c => !c.parentElement.closest('.se-component'));
  const kind = c => {
    const k = c.classList;
    if (k.contains('se-text')) return 'text';
    if (k.contains('se-image') || k.contains('se-imageStrip') || k.contains('se-imageGroup')) return 'image';
    if (k.contains('se-quotation') || k.contains('se-sectionTitle')) return 'subtitle';
    if (k.contains('se-horizontalLine')) return 'divider';
    if (k.contains('se-placesMap') || k.contains('se-map')) return 'place';
    if (k.contains('se-sticker')) return 'sticker';
    if (k.contains('se-video') || k.contains('se-oembed')) return 'video';
    return 'other';
  };
  const tags = [...document.querySelectorAll('.post_tag a, .wrap_tag a, .item_tag a, [class*=tag_area] a')]
    .map(a => a.innerText.trim()).filter(t => t.startsWith('#'));
  return {
    title: ((document.querySelector('.se-title-text') || {}).innerText || '').trim(),
    tags: [...new Set(tags)],
    blocks: comps.map(c => ({
      k: kind(c),
      style: (c.className.match(/se-l-(\w+)/) || [, ''])[1],
      imgs: c.querySelectorAll('img.se-image-resource, .se-imageStrip img, .se-imageGroup img').length,
      text: kind(c) === 'subtitle' ? ((c.querySelector('.se-quote, .se-section-title') || c).innerText || '').trim() : '',
      paras: kind(c) === 'text' ? [...c.querySelectorAll('.se-text-paragraph')].map(p => ({
        t: (p.innerText || '').replace(/[​﻿]/g, '').trim(),
        a: (p.className.match(/align-(\w+)/) || [, 'left'])[1],
        b: !!p.querySelector('b, strong, .se-bold'),
      })) : [],
    })),
  };
}
"""

_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⭐❤✅✔]")
# 정보 줄: 정보 낱말이 있고, 값의 형태(가격, 시각, 비율, '항목: 값', 요일 휴무 등)가 있는 줄만. 감상 문장은 담지 않습니다.
_INFO_WORD = re.compile(r"원|영업|브레이크|휴무|라스트\s?오더|주차|예약|웨이팅|캐치테이블|런치|디너|코스|운영|시간")
_OPINION = re.compile(r"정말|너무|진짜|완전|저렴|비싸|가성비|합리적|생각하면|다니|ㅎㅎ|ㅋㅋ|ㅠ|맛있|좋았|좋은|최고|추천|행복|만족")  # 감상이 섞인 줄
# 감상 줄: 방문자가 느낀 점(장점, 아쉬움, 팁). 같은 장소 리뷰에서 추천 문구의 재료로만 씁니다(본문에 옮기지 않음).
_FEELING = re.compile(r"맛있|맛나|좋았|좋아|좋더|추천|아쉬|부드럽|바삭|촉촉|쫄깃|깔끔|정갈|친절|넓|아늑|예쁘|예뻤|뷰|조용|최고|만족|편하|편했|"
                      r"따뜻|감성|힐링|재방문|또 가|또 오|가성비|푸짐|신선|고소|담백|진하|짰|짜다|짜서|짭짤|달달|달아|달았|달지|불편|좁|시끄|"
                      r"웨이팅|기다")
_NOT_FEELING = re.compile(r"위치|출구|주소|\d+\s?층|도보|번지|로\s?\d+")   # 위치 설명 줄은 감상이 아님
IMPRESSION_WORDS = {
    "장점": {"맛": r"맛있|맛나|고소|담백|진하", "식감": r"부드럽|바삭|촉촉|쫄깃", "깔끔, 정갈": r"깔끔|정갈", "친절": r"친절",
             "넓음, 편안": r"넓|아늑|편하|편했", "뷰, 분위기": r"뷰|예쁘|예뻤|감성|분위기|조용", "가성비, 양": r"가성비|푸짐|양이|넉넉",
             "청결": r"깨끗|청결", "재방문 의사": r"재방문|또 가|또 오"},
    "아쉬움": {"웨이팅, 대기": r"웨이팅|기다", "짠맛, 단맛": r"짰|짜다|짜서|달았|달아", "좁음, 불편": r"좁|불편", "소음": r"시끄",
              "가격": r"비싸|비쌌", "주차": r"주차.{0,6}(어렵|힘들|불편)"},
}
_TIME_RANGE = re.compile(r"\d{1,2}\s?:\s?\d{2}\s?[~\-–]\s?\d{1,2}\s?:\s?\d{2}")   # 12:00~21:20 같은 시간대는 낱말 없이도 정보
_INFO_VALUE = re.compile(r"\d[\d,.]*\s?(원|만\s?원)|\d{1,2}\s?:\s?\d{2}|\d+\s?%|^[^\s:]{1,8}\s?:|[월화수목금토일](요일)?\s?(정기\s?)?휴무|"
                         r"주차[도는이가]?\s?(가능|불가|지원|무료)|예약\s?(필수|제)|\d+\s?시간\s?(무료|지원)")
TOPICS = {
    "위치, 찾아가기": r"위치|출구|도보|엘리베이터|건물|층",
    "주차": r"주차",
    "예약, 웨이팅": r"예약|웨이팅|캐치테이블|대기",
    "가격": r"\d[\d,]*\s?원",
    "영업시간": r"영업|브레이크|휴무|라스트\s?오더|\d{1,2}:\d{2}",
    "내부, 좌석": r"내부|좌석|자리|테이블|카운터|룸",
    "메뉴 구성": r"메뉴|코스|구성",
    "디저트, 음료": r"디저트|아이스크림|커피|음료|주류|사케|하이볼",
    "서비스": r"친절|설명|서비스|리필",
    "동행, 상황": r"남자친구|여자친구|남편|아내|와이프|신랑|오빠|친구|가족|부모님|기념일|생일|데이트|회식",
}


def _clean(s: str) -> str:
    return unicodedata.normalize("NFC", s or "").replace(" ", " ").strip()


def _tone(lines: list[str]) -> str:
    yo = sum(1 for ln in lines if re.search(r"요[.!?~ㅎㅋ)]*$", _EMOJI.sub("", ln).strip()))
    hamnida = sum(1 for ln in lines if re.search(r"니다[.!?~)]*$", _EMOJI.sub("", ln).strip()))
    da = sum(1 for ln in lines if re.search(r"[^니]다[.!?~)]*$", _EMOJI.sub("", ln).strip()))
    if yo >= max(da, hamnida):
        return "요"
    return "다" if da >= hamnida else "습니다"


def title_features(title: str) -> dict[str, Any]:
    t = title or ""
    return {
        "length": len(t),
        "bracket": bool(re.search(r"[\[【]", t)),
        "bar": bool(re.search(r"[｜|]", t)),
        "quote": bool(re.search(r"[“”\"'‘’]", t)),
        "emoji_end": bool(_EMOJI.search(t[-3:])) if t else False,
        "후기": "후기" in t,
        "추천": "추천" in t,
        "내돈내산": "내돈내산" in t,
    }


def analyze(data: dict[str, Any]) -> dict[str, Any]:
    """COLLECT_JS 결과 하나를 수치로 바꿉니다. 본문은 lines(겹침 검사용)에만 담고 결과 요약에는 넣지 않습니다."""
    blocks = data.get("blocks") or []
    lines: list[str] = []
    aligns: Counter = Counter()
    bold = 0
    gaps: list[int] = []
    acc = 0
    seen_img = False
    cur = 0
    runs: list[int] = []
    place_pos = None
    subtitles: list[str] = []
    q_styles: Counter = Counter()
    images = image_blocks = 0
    for i, b in enumerate(blocks):
        k = b.get("k")
        if k == "text":
            n = 0
            for p in b.get("paras") or []:
                t = _clean(p.get("t"))
                if not t:
                    continue
                lines.append(t)
                aligns[p.get("a") or "left"] += 1
                bold += bool(p.get("b"))
                n += 1
            acc += n
            if n and cur:
                runs.append(cur)
                cur = 0
        elif k == "image":
            image_blocks += 1
            images += max(int(b.get("imgs") or 0), 1)
            cur += max(int(b.get("imgs") or 0), 1)
            if seen_img:
                gaps.append(acc)
            seen_img, acc = True, 0
        elif k == "subtitle":
            txt = _clean(b.get("text")).split("\n")[0]
            if txt:
                subtitles.append(txt)
            q_styles[b.get("style") or "default"] += 1
        elif k == "place" and place_pos is None:
            place_pos = round(i / max(len(blocks) - 1, 1), 2)
    if cur:
        runs.append(cur)
    body = "\n".join(lines)
    first = next((b.get("k") for b in blocks if not (b.get("k") == "text" and not any(_clean(p.get("t")) for p in b.get("paras") or []))), None)
    lens = [len(x) for x in lines]
    info = []
    feelings = []
    for ln in lines:
        is_info = ((_INFO_WORD.search(ln) and _INFO_VALUE.search(ln)) or _TIME_RANGE.search(ln)) and not _OPINION.search(ln)
        if len(ln) <= 60 and is_info and ln not in info:
            info.append(ln)
        elif 8 <= len(ln) <= 60 and _FEELING.search(ln) and not _NOT_FEELING.search(ln) and ln not in feelings:
            feelings.append(ln)
    return {
        "title": _clean(data.get("title")),
        "title_features": title_features(_clean(data.get("title"))),
        "chars": len(re.sub(r"\s", "", body)),
        "lines": len(lines),
        "avg_line": round(sum(lens) / len(lens), 1) if lens else None,
        "center_ratio": round(aligns["center"] / sum(aligns.values()), 2) if aligns else None,
        "images": images,
        "image_blocks": image_blocks,
        "lines_between_images": st.median(gaps) if gaps else None,
        "max_image_run": max(runs) if runs else 0,
        "first_block": first,
        "subtitles": subtitles,
        "quote_styles": dict(q_styles),
        "place_position": place_pos,
        "emoji": len(_EMOJI.findall(body)),
        "bold_lines": bold,
        "period_ratio": round(sum(1 for x in lines if x.rstrip().endswith(".")) / len(lines), 2) if lines else None,
        "tone": _tone(lines) if lines else None,
        "tags": len(data.get("tags") or []),
        "topics": [name for name, pat in TOPICS.items() if re.search(pat, body)],
        "info_lines": info[:8],
        "feeling_lines": feelings[:6],   # 감상 줄(60자 이하, 최대 6개): 추천 문구 재료
        "body_lines": lines,   # 겹침 검사용. Claude에게 돌려주는 요약에는 넣지 않습니다.
    }


def _median(vals: list[Any], nd: int = 1) -> float | None:
    v = [x for x in vals if x is not None]
    return round(st.median(v), nd) if v else None


def aggregate(stats: list[dict[str, Any]]) -> dict[str, Any]:
    """여러 글의 분석 결과를 중간값과 비율로 묶습니다."""
    n = len(stats)
    if not n:
        return {"posts": 0}
    sub_counter: Counter = Counter()
    for s in stats:
        sub_counter.update({re.sub(r"\s+", " ", x)[:20] for x in s["subtitles"]})
    q_styles: Counter = Counter()
    for s in stats:
        q_styles.update(s["quote_styles"])
    tf = [s["title_features"] for s in stats]
    pp = [s["place_position"] for s in stats]
    return {
        "posts": n,
        "title_length": _median([t["length"] for t in tf], 0),
        "title_patterns": {k: f"{sum(1 for t in tf if t[k])}/{n}" for k in ("bracket", "bar", "quote", "emoji_end", "후기", "추천", "내돈내산")},
        "chars": _median([s["chars"] for s in stats], 0),
        "lines": _median([s["lines"] for s in stats], 0),
        "avg_line": _median([s["avg_line"] for s in stats]),
        "center_posts": f"{sum(1 for s in stats if (s['center_ratio'] or 0) > 0.5)}/{n}",
        "images": _median([s["images"] for s in stats], 0),
        "lines_between_images": _median([s["lines_between_images"] for s in stats]),
        "max_image_run": _median([s["max_image_run"] for s in stats], 0),
        "first_block_image": f"{sum(1 for s in stats if s['first_block'] == 'image')}/{n}",
        "subtitles": _median([len(s["subtitles"]) for s in stats], 0),
        "question_subtitles": f"{sum(1 for s in stats for x in s['subtitles'] if x.rstrip().endswith('?'))}개",
        "common_subtitles": [k for k, c in sub_counter.most_common(12) if c >= 2],
        "quote_styles": dict(q_styles.most_common()),
        "place": {"front": sum(1 for p in pp if p is not None and p <= 0.25), "back": sum(1 for p in pp if p is not None and p >= 0.75),
                  "middle": sum(1 for p in pp if p is not None and 0.25 < p < 0.75), "none": sum(1 for p in pp if p is None)},
        "emoji": _median([s["emoji"] for s in stats], 0),
        "bold_lines": _median([s["bold_lines"] for s in stats], 0),
        "period_ratio": _median([s["period_ratio"] for s in stats], 2),
        "tone": dict(Counter(s["tone"] for s in stats if s["tone"]).most_common()),
        "tags": _median([s["tags"] for s in stats], 0),
        "topics": dict(Counter(t for s in stats for t in s["topics"]).most_common()),
    }


def blog_post_ids(url: str) -> tuple[str, str] | None:
    """블로그 글 주소에서 (블로그 ID, 글 번호)를 뽑습니다."""
    m = re.search(r"blog\.naver\.com/([A-Za-z0-9_-]+)/(\d+)", url or "") or \
        re.search(r"blogId=([A-Za-z0-9_-]+).*?logNo=(\d+)", url or "")
    return (m.group(1), m.group(2)) if m else None


async def read_post(page: Page, blog_id: str, log_no: str, url_tpl: str | None = None) -> dict[str, Any] | None:
    """글 하나를 열어 분석합니다. 본문 영역이 없으면(스마트에디터 ONE이 아닌 옛 글) None."""
    await page.goto((url_tpl or POST_URL).format(blog_id=blog_id, log_no=log_no), wait_until="domcontentloaded", timeout=30000)
    try:
        await page.wait_for_selector(".se-main-container", timeout=12000)
    except Exception:
        return None
    await page.wait_for_timeout(500)
    data = await page.evaluate(COLLECT_JS)
    return analyze(data) if data else None
