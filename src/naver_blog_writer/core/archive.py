"""새 글이 이전 글과 겹치지 않는지 검사합니다.

비교 대상
1. 내 블로그의 최근 게시물(최대 5개): get_style_guide가 매번 블로그에서 읽어 둔 것(core/blog_reader.py)
2. 이 서버가 임시저장한 최근 글(최대 5개): 아직 발행 전이라 블로그에 없는 초안. NBW_HOME/archive/*.json
3. 같은 장소의 다른 사람 리뷰(최대 10개): place_reviews가 24시간 안에 분석한 것. 같은 가게라 키워드는 당연히 겹치므로
   메인 키워드와 도입 유형은 비교하지 않고 제목, 소제목, 도입과 마무리, 거의 같은 문장만 비교합니다.
가게 정보 줄(📍 주소, 📞 전화, ⏰ 영업시간처럼 이모지 머리표로 시작하는 줄)은 사실이라 문장 비교에서 뺍니다.
저장 항목: 제목, 메인 키워드, 소제목, 태그, 도입 유형, 본문 전문.
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
from difflib import SequenceMatcher

from .. import config
from . import blog_reader, place_reviews
from .formatting import fact_line, split_sentences

KEEP = 5
HOOK_PATTERNS = ("인사형", "상황형", "질문형", "요약형", "감성형")  # 도입 유형 (스타일 가이드 4-3절)


def _dir():
    d = config.HOME / "archive"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFC", s or "").casefold()
    return re.sub(r"[\s\W_]+", "", s)


def save(record: dict) -> None:
    """임시저장에 성공한 글을 기록하고, 최근 KEEP개만 남깁니다."""
    record = {"saved_at": time.strftime("%Y-%m-%d %H:%M:%S"), **record}
    path = _dir() / f"{time.time_ns()}.json"  # 같은 초에 여러 번 저장해도 겹치지 않고, 이름순 = 시간순
    path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    files = sorted(_dir().glob("*.json"))
    for old in files[:-KEEP]:
        old.unlink(missing_ok=True)


def recent() -> list[dict]:
    """최근 글 기록을 최신순으로 돌려줍니다."""
    out = []
    for p in sorted(_dir().glob("*.json"), reverse=True)[:KEEP]:
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return out


def _sim(a: str, b: str) -> float:
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _sentences(body: str) -> list[str]:
    """줄마다, 그리고 문장 부호마다 나눈 문장. 마침표 없이 줄만 바꿔 쓰는 블로그 글도 한 줄씩 비교합니다.
    가게 정보 줄(📍 주소, 📞 전화 등)은 같은 가게면 값이 같을 수밖에 없으므로 비교하지 않습니다."""
    return [s for line in (body or "").split("\n") if not fact_line(line)
            for s in split_sentences(line) if s.strip()]


def comparison_posts() -> list[dict]:
    """겹침 비교 대상: 내 블로그 최근 게시물 + 아직 블로그에 없는 임시저장 기록(제목이 같으면 하나만)."""
    blog = [{**p, "source": "블로그"} for p in blog_reader.load_cache().get("posts", [])]
    titles = {_norm(p.get("title", "")) for p in blog}
    drafts = [{**p, "source": "임시저장"} for p in recent() if _norm(p.get("title", "")) not in titles]
    others = ([{**p, "source": "같은 장소 리뷰"} for p in place_reviews.load_recent_for_overlap()]
              if config.READ_OTHER_BLOGS else [])
    return blog + drafts + others


def overlap_warnings(title: str, main_keyword: str | None, hook_pattern: str | None,
                     subtitles: list[str], body: str) -> list[str]:
    """최근 글과 겹치는 부분을 경고 목록으로 돌려줍니다. 저장을 막지는 않습니다."""
    past = comparison_posts()
    if not past:
        return []
    w: list[str] = []
    drafts = [p for p in past if p["source"] == "임시저장"]
    mine = [p for p in past if p["source"] != "같은 장소 리뷰"]
    if hook_pattern and drafts and hook_pattern == drafts[0].get("hook_pattern"):
        w.append(f"도입 유형 '{hook_pattern}'이 직전 글과 같습니다. 다른 유형으로 시작하세요.")
    if main_keyword:
        nk = _norm(main_keyword)
        same_kw = [p["title"] for p in mine if nk and (_norm(p.get("main_keyword", "")) == nk or nk in _norm(p.get("title", "")))]
        if same_kw:
            w.append(f"메인 키워드 '{main_keyword}'가 최근 글과 같습니다: {same_kw[:2]}. 한정어(지역, 상황, 시기)를 바꾸세요.")
    for p in past:
        r = _sim(title, p.get("title", ""))
        if r >= 0.6:
            w.append(f"제목이 {'다른 사람의 같은 장소 리뷰' if p['source'] == '같은 장소 리뷰' else '최근 글(' + p['source'] + ')'} "
                     f"'{p.get('title')}'과 {r:.0%} 비슷합니다.")
    past_subs = {_norm(s): p.get("title") for p in past for s in p.get("subtitles", [])}
    dup_subs = [s for s in subtitles if _norm(s) in past_subs]
    if dup_subs:
        w.append(f"소제목이 최근 글이나 같은 장소 리뷰와 똑같습니다: {dup_subs[:3]}. 표현을 바꾸세요.")
    new_sents = [s for s in _sentences(body) if len(_norm(s)) >= 12]
    if new_sents:
        first, last_s = new_sents[0], new_sents[-1]
        for p in past:
            ps = [s for s in _sentences(p.get("body") or "") if len(_norm(s)) >= 12]
            if not ps:
                continue
            if _sim(first, ps[0]) >= 0.7:
                w.append(f"도입 첫 문장이 '{p.get('title')}'의 도입과 비슷합니다.")
            if _sim(last_s, ps[-1]) >= 0.7:
                w.append(f"마무리 문장이 '{p.get('title')}'의 마무리와 비슷합니다.")
        def near(group: list[dict]) -> list[str]:
            norm = [_norm(s) for p in group for s in _sentences(p.get("body") or "")]
            return [s for s in new_sents if any(SequenceMatcher(None, _norm(s), q).ratio() >= 0.85 for q in norm if q)]

        others = [p for p in past if p["source"] == "같은 장소 리뷰"]
        copied = near(others) if others else []
        if copied:
            w.append(f"같은 장소의 다른 사람 리뷰와 거의 같은 문장이 {len(copied)}개 있습니다(예: '{copied[0][:30]}'). "
                     "다른 사람 글의 문장은 쓰지 않습니다. 내 경험으로 다시 쓰세요.")
        repeated = near(mine)
        if len(repeated) >= 3:
            w.append(f"최근 글과 거의 같은 문장이 {len(repeated)}개 있습니다(예: '{repeated[0][:30]}'). 유사문서 위험이 있으니 다시 쓰세요.")
    return w
