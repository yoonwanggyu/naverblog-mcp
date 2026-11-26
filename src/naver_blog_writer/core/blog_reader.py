"""내 블로그의 최근 게시물 읽기와 '내 스타일' 자동 생성.

글을 쓸 때마다(get_style_guide 호출 때마다) NAVER_BLOG_ID 블로그의 최근 게시물을 최대 5개 읽습니다.
- 목록: RSS(rss.blog.naver.com/<ID>.xml), 실패하면 글 목록 API(PostTitleListAsync)
- 본문: 화면에 보이는 브라우저로 각 글(PostView)을 열어 제목, 본문, 소제목(인용구), 사진 수, 지도, 태그, 블록 순서를 읽음
읽은 결과는 NBW_HOME/my_posts/latest.json에 저장하고, NBW_HOME/my_style.md('내 스타일')를 새로 씁니다.
이 5개 글은 (1) 문체를 따라 쓰는 기준이고 (2) 새 글이 겹치지 않는지 검사하는 비교 대상입니다.
이 모듈은 내 블로그만 읽습니다. 다른 사람 글은 place_reviews.py(같은 장소 리뷰)와 trends.py(주제별 경향)가 읽습니다.
"""

from __future__ import annotations

import json
import logging
import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import unquote_plus

from playwright.async_api import Page

from .. import config
from . import post_analysis

log = logging.getLogger(__name__)

RSS_URL = "https://rss.blog.naver.com/{blog_id}.xml"
TITLE_LIST_URL = ("https://blog.naver.com/PostTitleListAsync.naver?blogId={blog_id}&viewdate=&currentPage=1"
                  "&categoryNo=0&parentCategoryNo=&countPerPage={limit}")
POST_URL = "https://blog.naver.com/PostView.naver?blogId={blog_id}&logNo={log_no}"
BLOG_HOME_URL = "https://blog.naver.com/{blog_id}"
BODY_MAX_CHARS = 2500

_EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿⭐❤]")
_ZW = re.compile(r"[​‌‍﻿]")

_READ_POST_JS = """
() => {
  const q = s => document.querySelector(s);
  const main = q('.se-main-container') || q('#postViewArea') || q('.post-view');
  if (!main) return null;
  const kind = c => {
    const k = c.classList;
    if (k.contains('se-text')) return 'text';
    if (k.contains('se-image') || k.contains('se-imageStrip') || k.contains('se-imageGroup')) return 'image';
    if (k.contains('se-quotation')) return 'subtitle';
    if (k.contains('se-horizontalLine')) return 'divider';
    if (k.contains('se-placesMap') || k.contains('se-map')) return 'place';
    if (k.contains('se-video') || k.contains('se-oembed')) return 'video';
    if (k.contains('se-sectionTitle')) return 'subtitle';
    return 'other';
  };
  const comps = [...main.querySelectorAll(':scope .se-component')].filter(c => !c.parentElement.closest('.se-component'));
  const tags = [...document.querySelectorAll('.post_tag a, .wrap_tag a, .item_tag a, a.tag, [class*=tag_area] a')]
      .map(a => a.innerText.trim()).filter(t => t.startsWith('#'));
  return {
    title: ((q('.se-title-text') || q('.pcol1') || q('.se_title') || {}).innerText || document.title || '').trim(),
    date: ((q('.se_publishDate') || q('.date') || {}).innerText || '').trim(),
    body: main.innerText,
    layout: comps.map(kind),
    // 사진 블록마다 [배치, 장수]. 배치: single(개별), strip(나란히), collage(콜라주), slide(슬라이드). 사진이 아닌 블록은 null
    photo_blocks: comps.map(c => kind(c) !== 'image' ? null : [
      c.classList.contains('se-imageStrip') ? 'strip' : c.classList.contains('se-imageGroup')
        ? (c.classList.contains('se-l-slide') ? 'slide' : 'collage') : 'single',
      Math.max(c.querySelectorAll('img.se-image-resource, .se-imageStrip img, .se-imageGroup img').length, 1)]),
    subtitles: comps.filter(c => kind(c) === 'subtitle').map(c => c.innerText.trim()).filter(Boolean),
    images: main.querySelectorAll('.se-image img, .se-imageStrip img, .se-imageGroup img, img.se-image-resource, #postViewArea img').length,
    tags: [...new Set(tags)],
    lines: [...main.querySelectorAll('.se-text .se-text-paragraph')]
      .map(p => [p.innerText.replace(/[\u200b\ufeff]/g, '').trim(), (p.className.match(/align-(\\w+)/) || [, 'left'])[1]])
      .filter(x => x[0]),
  };
}
"""


def _clean(s: str) -> str:
    s = unicodedata.normalize("NFC", _ZW.sub("", s or "")).replace(" ", " ")
    s = re.sub(r"[ \t]+\n", "\n", s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


async def list_recent(page: Page, blog_id: str, limit: int) -> list[dict[str, Any]]:
    """최근 글 목록(최신순). RSS를 먼저 쓰고, 실패하면 글 목록 API를 씁니다.

    두 곳 모두 읽지 못하면 예외를 던집니다. '게시물 0개'와 '읽기 실패'를 구분해야
    네트워크 오류 때 이전에 읽어 둔 최근 글 기록(겹침 비교 대상)을 지우지 않습니다.
    """
    items: list[dict[str, Any]] = []
    reached = False
    errors: list[str] = []
    try:
        r = await page.context.request.get(RSS_URL.format(blog_id=blog_id), timeout=15000)
        if r.ok:
            root = ET.fromstring(await r.body())
            reached = True
            for it in root.findall("./channel/item")[:limit]:
                link = (it.findtext("guid") or it.findtext("link") or "").strip()
                m = re.search(r"(?:/|logNo=)(\d{6,})", link)
                if not m:
                    continue
                pub = it.findtext("pubDate") or ""
                try:
                    date = parsedate_to_datetime(pub).strftime("%Y-%m-%d")
                except (TypeError, ValueError):
                    date = pub
                items.append({"log_no": m.group(1), "title": (it.findtext("title") or "").strip(), "date": date,
                              "category": (it.findtext("category") or "").strip(),
                              "tags": [t for t in (it.findtext("tag") or "").split(",") if t.strip()]})
        else:
            errors.append(f"RSS 응답 {r.status}")
    except Exception as e:
        log.warning("RSS 읽기 실패: %s", e)
        errors.append(f"RSS: {str(e).splitlines()[0]}")
    if items:
        return items
    try:
        r = await page.context.request.get(TITLE_LIST_URL.format(blog_id=blog_id, limit=limit), timeout=15000)
        if r.ok:
            data = json.loads((await r.text()).replace("\\'", "'"))
            if data.get("resultCode") != "S":  # HTTP 200이어도 'E'(일시 장애)면 읽기 실패로 봅니다.
                raise RuntimeError(data.get("resultMessage") or f"resultCode={data.get('resultCode')}")
            reached = True
            for p in data.get("postList", [])[:limit]:
                items.append({"log_no": str(p["logNo"]), "title": unquote_plus(p.get("title", "")),
                              "date": p.get("addDate", ""), "category": "", "tags": []})
        else:
            errors.append(f"글 목록 API 응답 {r.status}")
    except Exception as e:
        log.warning("글 목록 API 읽기 실패: %s", e)
        errors.append(f"글 목록 API: {str(e).splitlines()[0]}")
    if not reached:
        raise RuntimeError("블로그 글 목록을 읽지 못했습니다 (" + "; ".join(errors) + ")")
    return items


async def read_post(page: Page, blog_id: str, item: dict[str, Any]) -> dict[str, Any]:
    url = POST_URL.format(blog_id=blog_id, log_no=item["log_no"])
    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    try:
        await page.wait_for_selector(".se-main-container, #postViewArea, .post-view", timeout=10000)
    except Exception:
        pass
    data = await page.evaluate(_READ_POST_JS) or {}
    body = _clean(data.get("body", ""))
    tags = data.get("tags") or [f"#{t.strip()}" for t in item.get("tags", [])]
    return {
        "log_no": item["log_no"],
        "url": f"https://blog.naver.com/{blog_id}/{item['log_no']}",
        "title": _clean(data.get("title") or item.get("title", "")),
        "date": item.get("date") or data.get("date", ""),
        "category": item.get("category", ""),
        "subtitles": [_clean(s) for s in data.get("subtitles", [])],
        "tags": [t.lstrip("#") for t in tags],
        "image_count": int(data.get("images") or 0),
        "layout": data.get("layout", []),
        **_photo_layout(data.get("layout", []), data.get("photo_blocks") or []),
        "body": body,
        "char_count": len(re.sub(r"\s", "", body)),
        "line_count": len(data.get("lines") or []),
        "avg_line_chars": round(sum(len(t) for t, _ in data["lines"]) / len(data["lines"]), 1) if data.get("lines") else None,
        "center_ratio": round(sum(a == "center" for _, a in data["lines"]) / len(data["lines"]), 2) if data.get("lines") else None,
    }


def _photo_layout(kinds: list[str], photo_blocks: list[Any]) -> dict[str, Any]:
    """post_analysis.layout_summary가 읽는 형태({photo_layout, max_single_run})로 만듭니다."""
    layout: dict[str, dict[str, int]] = {}
    run = best = 0
    for k, pb in zip(kinds, photo_blocks, strict=False):
        if pb:
            lay, n = pb
            slot = layout.setdefault(lay, {"blocks": 0, "photos": 0})
            slot["blocks"] += 1
            slot["photos"] += int(n)
            run = run + 1 if lay == "single" else 0
            best = max(best, run)
        elif k in ("text", "subtitle", "divider", "place"):
            run = 0
    return {"photo_layout": layout, "max_single_run": best} if photo_blocks else {}


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?…~])\s+|\n+", text)
    return [p.strip() for p in parts if len(p.strip()) >= 4]


def _endings(sents: list[str]) -> Counter:
    c: Counter = Counter()
    for s in sents:
        s2 = re.sub(r"[\s.!?…~^ㅎㅋ\U0001F300-\U0001FAFF☀-➿]+$", "", s)
        if len(s2) >= 3 and re.search(r"[가-힣]$", s2):
            c[s2[-3:]] += 1
    return c


def style_stats(posts: list[dict[str, Any]]) -> dict[str, Any]:
    if not posts:
        return {}
    sents = [s for p in posts for s in _sentences(p["body"])]
    n = len(posts)
    emojis = sum(len(_EMOJI.findall(p["body"])) for p in posts)
    subs = [s for p in posts for s in p["subtitles"]]
    return {
        "posts": n,
        "avg_chars": round(sum(p["char_count"] for p in posts) / n),
        "avg_images": round(sum(p["image_count"] for p in posts) / n, 1),
        "photo_layout": post_analysis.layout_text(post_analysis.layout_summary(posts)),
        "avg_subtitles": round(len(subs) / n, 1),
        "question_subtitle_ratio": round(sum(s.rstrip().endswith("?") for s in subs) / len(subs), 2) if subs else None,
        "avg_sentence_chars": round(sum(len(s) for s in sents) / len(sents)) if sents else None,
        "top_endings": [e for e, _ in _endings(sents).most_common(8)],
        "emoji_per_post": round(emojis / n, 1),
        "avg_tags": round(sum(len(p["tags"]) for p in posts) / n, 1),
        "avg_line_chars": _avg([p.get("avg_line_chars") for p in posts]),
        "center_ratio": _avg([p.get("center_ratio") for p in posts], 2),
    }


def _avg(vals: list[Any], nd: int = 1) -> float | None:
    v = [x for x in vals if x is not None]
    return round(sum(v) / len(v), nd) if v else None


def _layout_str(layout: list[str]) -> str:
    names = {"text": "글", "image": "사진", "subtitle": "소제목", "divider": "구분선", "place": "지도", "video": "영상"}
    out: list[str] = []
    for k in layout:
        nm = names.get(k)
        if not nm:
            continue
        if out and out[-1].startswith(nm):
            cnt = out[-1][len(nm):].strip("×") or "1"
            out[-1] = f"{nm}×{int(cnt) + 1}"
        else:
            out.append(nm)
    return " → ".join(out)


def build_my_style(posts: list[dict[str, Any]], synced_at: str, blog_id: str) -> str:
    st = style_stats(posts)
    lines = [
        f"# 내 스타일 (자동 생성: {blog_id} 블로그 최근 게시물 {len(posts)}개, {synced_at})",
        "",
        "> get_style_guide가 글을 쓸 때마다 최근 게시물을 다시 읽어 이 파일을 새로 씁니다. 직접 고쳐도 덮어씁니다.",
        "> 고정하고 싶은 개인 규칙은 STYLE_GUIDE_PATH 파일에 적으세요.",
        "",
    ]
    if not posts:
        lines += ["아직 읽을 수 있는 게시물이 없습니다. 기본 가이드(1~9절)만 따릅니다."]
        return "\n".join(lines) + "\n"
    lines += [
        "## 요약 (최근 글 평균)",
        f"- 본문 길이: 공백 제외 약 {st['avg_chars']}자",
        f"- 사진 배치: {st['photo_layout']}",
        f"- 사진: 글당 {st['avg_images']}장, 소제목: 글당 {st['avg_subtitles']}개"
        + (f" (질문형 비율 {int(st['question_subtitle_ratio'] * 100)}%)" if st.get("question_subtitle_ratio") is not None else ""),
        f"- 문장 길이: 평균 {st['avg_sentence_chars']}자, 이모지: 글당 {st['emoji_per_post']}개, 태그: 글당 {st['avg_tags']}개",
        f"- 자주 쓰는 문장 끝: {', '.join(st['top_endings']) or '(없음)'}",
        f"- 한 줄(에디터 문단) 평균 {st['avg_line_chars'] or '-'}자, 가운데 정렬 비율 "
        + (f"{int(st['center_ratio'] * 100)}%" if st.get("center_ratio") is not None else "-")
        + " (상위 블로그 기준: 한 줄 21자 안팎, 가운데 정렬 80%)",
        "",
        "## 게시물별 (최신순)",
    ]
    for i, p in enumerate(posts, 1):
        sents = _sentences(p["body"])
        body = p["body"] if len(p["body"]) <= BODY_MAX_CHARS else p["body"][:BODY_MAX_CHARS] + "\n…(이하 생략)"
        lines += [
            "",
            f"### {i}. {p['title']}",
            f"- 날짜: {p['date']}  카테고리: {p['category'] or '-'}  주소: {p['url']}",
            f"- 분량: 공백 제외 {p['char_count']}자, 사진 {p['image_count']}장"
            + (f", 한 줄 평균 {p['avg_line_chars']}자, 가운데 정렬 {int(p['center_ratio'] * 100)}%" if p.get("center_ratio") is not None else ""),
            f"- 구성: {_layout_str(p['layout']) or '-'}",
            f"- 소제목: {' / '.join(p['subtitles']) or '(없음)'}",
            f"- 태그: {' '.join('#' + t for t in p['tags']) or '(없음)'}",
            f"- 도입 문장: {sents[0] if sents else '-'}",
            f"- 마무리 문장: {sents[-1] if sents else '-'}",
            "",
            "본문:",
            "```",
            body,
            "```",
        ]
    return "\n".join(lines) + "\n"


async def sync_recent_posts(page: Page, blog_id: str, limit: int | None = None) -> dict[str, Any]:
    """최근 게시물을 읽어 캐시와 my_style.md를 갱신합니다."""
    limit = limit or config.RECENT_POSTS_LIMIT
    if not config.HEADLESS:  # 화면으로 지켜보는 사용자에게 어떤 블로그를 읽는지 먼저 보여 줍니다.
        try:
            await page.goto(BLOG_HOME_URL.format(blog_id=blog_id), wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(1500)
        except Exception as e:
            log.info("블로그 첫 화면 열기 실패(무시): %s", e)
    items = await list_recent(page, blog_id, limit)
    posts: list[dict[str, Any]] = []
    errors: list[str] = []
    for it in items[:limit]:
        try:
            posts.append(await read_post(page, blog_id, it))
        except Exception as e:
            errors.append(f"{it.get('title', it['log_no'])}: {e}")
    synced_at = time.strftime("%Y-%m-%d %H:%M:%S")
    data = {"blog_id": blog_id, "synced_at": synced_at, "synced_ts": time.time(), "posts": posts,
            "errors": errors, "stats": style_stats(posts)}
    save_cache(data)
    config.MY_STYLE_PATH.write_text(build_my_style(posts, synced_at, blog_id), encoding="utf-8")
    return data


def save_cache(data: dict[str, Any]) -> None:
    config.ensure_dirs()
    (config.MY_POSTS_DIR / "latest.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def load_cache() -> dict[str, Any]:
    try:
        return json.loads((config.MY_POSTS_DIR / "latest.json").read_text(encoding="utf-8"))
    except Exception:
        return {"posts": [], "synced_ts": 0, "synced_at": None}


def record_sync_failure(blog_id: str, error: str) -> dict[str, Any]:
    """읽기에 실패해도 시도 시각은 남깁니다(이전 캐시는 그대로 두고 비교 대상으로 계속 씁니다)."""
    data = load_cache()
    data.update(blog_id=blog_id, last_attempt_ts=time.time(), last_error=error)
    save_cache(data)
    return data


def sync_age_minutes() -> float | None:
    data = load_cache()
    ts = max(data.get("synced_ts") or 0, data.get("last_attempt_ts") or 0)
    return None if not ts else (time.time() - ts) / 60
