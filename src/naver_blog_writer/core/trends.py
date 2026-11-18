"""네이버 주제별 인기글로 '최신 경향'을 만듭니다(TREND_MAX_AGE_DAYS일마다 갱신).

- 목록: section.blog.naver.com의 주제별 인기글(DirectoryTopPostList)과 최신글(DirectoryPostList). 블로그 하나당 1편,
  쇼핑 글(isMarketPost)은 뺍니다. 최대 TREND_POSTS편을 엽니다.
- 결과: 형식 수치(post_analysis.aggregate)만 NBW_HOME/trends/<주제>.json에 저장합니다. 본문 문장과 글 주소는 저장하지 않습니다.
- READ_OTHER_BLOGS=false면 갱신하지 않고 가이드에도 붙이지 않습니다.
- 스타일 가이드에는 '부록 B. 최신 경향'으로 붙습니다(core/style.py). 우선순위는 내 스타일(10절)보다 낮고 기본 가이드보다 높습니다.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from playwright.async_api import Page

from .. import config
from . import post_analysis

log = logging.getLogger(__name__)

TOP_URL = "https://section.blog.naver.com/ajax/DirectoryTopPostList.naver?directorySeq={seq}"
RECENT_URL = "https://section.blog.naver.com/ajax/DirectoryPostList.naver?directorySeq={seq}&pageNo={page}"
REFERER = "https://section.blog.naver.com/ThemePost.naver"
THEME_PAGE_URL = "https://section.blog.naver.com/ThemePost.naver?directoryNo={seq}&activeDirectorySeq=3&currentPage=1"

# 글 주제 → 네이버 블로그 주제 번호
TOPICS: dict[str, tuple[int, str]] = {
    "맛집": (29, "맛집"), "카페": (29, "맛집"), "데이트": (29, "맛집"),
    "여행": (27, "국내여행"), "해외여행": (28, "세계여행"),
    "결혼준비": (15, "육아·결혼"), "취업준비": (34, "교육·학문"),
}


def topic_info(topic: str) -> tuple[int, str]:
    if topic not in TOPICS:
        raise ValueError(f"알 수 없는 주제입니다: {topic}. 쓸 수 있는 값: {', '.join(TOPICS)}")
    return TOPICS[topic]


def _dir() -> Path:
    d = config.HOME / "trends"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(seq: int) -> Path:
    return _dir() / f"directory_{seq}.json"


def load(topic: str) -> dict[str, Any] | None:
    seq, _ = topic_info(topic)
    p = _path(seq)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def age_days(data: dict[str, Any] | None) -> float | None:
    if not data or not data.get("updated_ts"):
        return None
    return (time.time() - data["updated_ts"]) / 86400


def _json(text: str) -> Any:
    return json.loads(re.sub(r"^\)\]\}',?\s*", "", text))


async def list_posts(page: Page, seq: int, limit: int) -> list[dict[str, Any]]:
    """인기글 먼저, 모자라면 최신글로 채웁니다. 블로그당 1편, 쇼핑 글 제외."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    sources = [(TOP_URL.format(seq=seq), "인기")] + [(RECENT_URL.format(seq=seq, page=p), "최신") for p in (1, 2)]
    for url, kind in sources:
        if len(out) >= limit:
            break
        try:
            r = await page.request.get(url, headers={"Referer": REFERER})
            data = _json(await r.text())
        except Exception as e:
            log.warning("주제별 글 목록 읽기 실패(%s): %s", url, e)
            continue
        res = data.get("result")
        items = res.get("postList", []) if isinstance(res, dict) else (res or [])
        for it in items:
            b = it.get("domainIdOrBlogId")
            if not b or b in seen or it.get("isMarketPost") or b == config.BLOG_ID:
                continue
            seen.add(b)
            out.append({"blog_id": b, "log_no": str(it.get("logNo")), "kind": kind})
            if len(out) >= limit:
                break
    return out


def render(data: dict[str, Any]) -> str:
    """스타일 가이드에 붙일 '부록 B. 최신 경향'."""
    a = data["aggregate"]
    if not a.get("posts"):
        return ""
    tp = a["title_patterns"]
    pl = a["place"]
    return "\n".join([
        f"# 부록 B. 최신 경향 (자동 갱신: '{data['directory_name']}' 주제 인기글과 최신글 {a['posts']}편, {data['updated_at']})",
        "",
        f"> {config.TREND_MAX_AGE_DAYS}일마다 다시 분석합니다. 형식 수치만 담았고 다른 글의 문장은 담지 않았습니다.",
        "> 4-4~4-6절의 수치와 다르면 이 수치를 따른다. 단 10절 내 스타일과 2절 절대 규칙이 더 우선한다.",
        "",
        "| 항목 | 최신 경향 (중간값) |",
        "|---|---|",
        f"| 제목 | {a['title_length']}자. [ ] {tp['bracket']}, ｜ {tp['bar']}, 따옴표 {tp['quote']}, 끝 이모지 {tp['emoji_end']}, "
        f"'후기' {tp['후기']}, '추천' {tp['추천']} |",
        f"| 본문 | 공백 제외 {a['chars']}자, {a['lines']}줄, 한 줄 {a['avg_line']}자 |",
        f"| 정렬 | 가운데 정렬 {a['center_posts']} |",
        f"| 사진 | {a['images']}장, 사진 사이 {a['lines_between_images']}줄, 최대 연속 {a['max_image_run']}장, 사진으로 시작 {a['first_block_image']} |",
        f"| 소제목 | {a['subtitles']}개, 질문형 {a['question_subtitles']}, 모양 {a['quote_styles']} |",
        f"| 자주 쓰인 소제목 | {', '.join(a['common_subtitles']) or '-'} |",
        f"| 지도 위치 | 앞쪽 {pl['front']}, 중간 {pl['middle']}, 끝 {pl['back']}, 없음 {pl['none']} |",
        f"| 말투 | {a['tone']}, 마침표로 끝나는 줄 {a['period_ratio']} |",
        f"| 이모지, 굵게, 태그 | 이모지 {a['emoji']}개, 굵은 줄 {a['bold_lines']}개, 태그 {a['tags']}개 |",
        f"| 많이 다룬 내용 | {', '.join(f'{k} {v}' for k, v in a['topics'].items())} |",
        "",
    ])


async def refresh(page: Page, topic: str, force: bool = False) -> dict[str, Any]:
    """최신 경향이 TREND_MAX_AGE_DAYS일보다 오래됐거나 force면 다시 분석합니다. 실패해도 이전 결과를 지우지 않습니다."""
    seq, name = topic_info(topic)
    cached = load(topic)
    age = age_days(cached)
    if not force and age is not None and age < config.TREND_MAX_AGE_DAYS:
        return {"refreshed": False, "topic": topic, "directory": name, "updated_at": cached["updated_at"],
                "age_days": round(age, 1), "posts": cached["aggregate"].get("posts", 0)}
    if not config.HEADLESS:  # 사용자가 어떤 주제의 인기글을 읽는지 화면으로 볼 수 있게 주제 페이지를 먼저 엽니다.
        try:
            await page.goto(THEME_PAGE_URL.format(seq=seq), wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(1500)
        except Exception as e:
            log.info("주제 페이지 열기 실패(무시): %s", e)
    items = await list_posts(page, seq, config.TREND_POSTS)
    stats: list[dict[str, Any]] = []
    kinds: dict[str, int] = {}
    for it in items:
        try:
            a = await post_analysis.read_post(page, it["blog_id"], it["log_no"])
        except Exception as e:
            log.info("경향 글 읽기 실패 %s/%s: %s", it["blog_id"], it["log_no"], e)
            continue
        if a is None:
            continue
        a.pop("body_lines", None)       # 본문은 저장하지 않습니다
        a.pop("info_lines", None)
        stats.append(a)
        kinds[it["kind"]] = kinds.get(it["kind"], 0) + 1   # 글 주소(다른 블로거 ID)는 저장하지 않습니다
    if not stats:
        raise RuntimeError(f"'{name}' 주제 글을 하나도 읽지 못했습니다.")
    data = {"topic": topic, "directory_seq": seq, "directory_name": name, "updated_ts": time.time(),
            "updated_at": time.strftime("%Y-%m-%d %H:%M"), "aggregate": post_analysis.aggregate(stats), "source_kinds": kinds}
    _path(seq).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"refreshed": True, "topic": topic, "directory": name, "updated_at": data["updated_at"], "posts": len(stats)}
