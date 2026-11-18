"""사진 폴더 찾기. 사용자가 말한 날짜와 장소명을 폴더 이름과 맞춰 봅니다.

- 날짜는 형식이 달라도 같은 날이면 일치로 봅니다(core/dates.py). 예: '11월 14일' = '26.11.14' = '11/14'.
- 장소명은 오타를 허용합니다. 한글을 자모로 풀어서 비교하므로 '제스시'와 '젠스시'가 비슷하다고 판단합니다.
- 정확히 일치하는 폴더가 하나뿐일 때만 바로 진행합니다. 그 밖에는 폴더에 저장된 날짜와 장소명을 보여 주고
  "이게 맞습니까?"라고 사용자에게 확인받아야 합니다. 확인 전에는 scan_media가 그 폴더를 읽지 않습니다.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import dates as D

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".heic", ".heif"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}

# 장소명이 아닌 말. 사용자가 문장째 넘겨도 이름 비교에서 뺍니다.
STOPWORDS = {
    "다녀온", "다녀왔던", "다녀왔어", "갔던", "갔다온", "방문한", "방문", "식당", "맛집", "음식점", "레스토랑", "카페",
    "사진", "사진들", "사진들로", "사진으로", "동영상", "영상", "블로그", "네이버", "글", "글로", "작성", "포스팅",
    "후기", "리뷰", "폴더", "날", "때", "거", "것", "곳", "관련", "대해", "대한", "대해서", "좀", "그", "저", "제가",
    "내가", "우리", "저희", "에", "에서", "랑", "하고", "그리고", "가서", "먹은", "먹었던", "써줘", "써", "작성해줘",
    "작성해", "정리해줘", "올려줘", "만들어줘", "부탁해", "해줘", "주세요", "점심", "저녁", "아침", "브런치",
}
_FILLER = re.compile(
    r"^(다녀|갔|가서|먹|마셨|방문|찍|올려|있었|했|해줘|해주|써|쓰|작성|정리|포스팅|만들)"
    r"|(줘|주세요|해요|했어|했던|었던|았던|합니다|할게|하자)$"
)
_JOSA = ("에서", "으로", "에게", "까지", "부터", "이랑", "하고", "에", "을", "를", "은", "는", "이", "가", "의", "로", "랑", "도", "와", "과")
_SPLIT = re.compile(r"[\s,./·\-_()\[\]{}'\"!?~:;]+")

TERM_MATCH = 0.7      # 이 점수 이상이면 이름 후보로 봅니다
TERM_IGNORE = 0.55    # 모든 폴더에서 이 점수 미만인 말은 이름이 아니라고 보고 뺍니다

_pending: dict[str, str] = {}   # 확인이 필요한 폴더 경로 -> 사용자에게 할 질문


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def _key(path: str | Path) -> str:
    return _nfc(str(Path(path).expanduser().resolve())).casefold()


def _compact(s: str) -> str:
    return re.sub(r"[\s_\-.()\[\]{}]+", "", _nfc(s).casefold())


def _jamo(s: str) -> str:
    return unicodedata.normalize("NFD", s)


# ---------------------------------------------------------------- 폴더 이름 해석

def parse_folder_name(name: str, today: date | None = None) -> dict[str, Any]:
    """폴더 이름에서 날짜와 나머지 이름(장소, 주제)을 분리합니다.

    예: '젠스시(26.11.14)' -> 2026-11-14, '젠스시' / '2026-11-15_성수데이트' -> 2026-11-15, '성수데이트'
        '1_카페어니언' -> 순서 1, '카페어니언' / '젠스시 11.14' -> (연도는 가장 최근의 11월 14일), '젠스시'
    """
    name = _nfc(name)
    hits = [h for h in D.find_dates(name, today=today) if h.month is not None]
    out: dict[str, Any] = {"date": None, "year_inferred": False, "date_text": None}
    rest = name
    if hits:
        h = hits[0]
        if h.year is None:
            d = D.resolve_year(h.month, h.day, today)  # type: ignore[arg-type]
            out["year_inferred"] = True
        else:
            d = date(h.year, h.month, h.day)  # type: ignore[arg-type]
        out["date"] = d.isoformat()
        out["date_text"] = h.text
        rest = D.remove_spans(name, [h])
    else:
        m = re.match(r"^(?P<order>\d{1,2})[ _.-]+(?P<rest>.+)$", name)  # 하루 안의 장소 순서: '1_카페'
        if m:
            out["order"] = int(m["order"])
            rest = m["rest"]
    rest = re.sub(r"\(\s*\)|\[\s*\]|\{\s*\}", " ", rest)
    rest = re.sub(r"[_]+", " ", rest)
    out["label"] = re.sub(r"\s{2,}", " ", rest).strip(" -.,·") or name
    return out


@lru_cache(maxsize=512)
def _first_photo_date_cached(folder: str, mtime: float) -> str | None:
    from PIL import Image

    try:
        files = sorted(p for p in Path(folder).iterdir() if p.suffix.lower() in IMAGE_EXT and not p.name.startswith("."))
    except OSError:
        return None
    for p in files[:3]:
        try:
            with Image.open(p) as im:
                exif = im.getexif()
                raw = exif.get_ifd(0x8769).get(36867) or exif.get(306)
                if raw:
                    return datetime.strptime(str(raw).strip(), "%Y:%m:%d %H:%M:%S").date().isoformat()
        except Exception:
            continue
    return None


def first_photo_date(folder: Path) -> str | None:
    """폴더 첫 사진의 EXIF 촬영일. 폴더 이름에 날짜가 없을 때 대신 씁니다(파일을 최대 3개만 엽니다)."""
    try:
        return _first_photo_date_cached(str(folder), folder.stat().st_mtime)
    except OSError:
        return None


def _count_media(folder: Path) -> tuple[int, int]:
    imgs = vids = 0
    try:
        for p in folder.iterdir():
            if p.is_file() and not p.name.startswith("."):
                ext = p.suffix.lower()
                imgs += ext in IMAGE_EXT
                vids += ext in VIDEO_EXT
    except OSError:
        pass
    return imgs, vids


def walk_folders(roots: list[Path], max_depth: int = 3, today: date | None = None) -> list[dict[str, Any]]:
    """MEDIA_ROOTS 아래에서 사진이나 영상이 든 폴더(또는 그런 하위 폴더를 가진 폴더)를 모읍니다."""
    found: list[dict[str, Any]] = []
    for root in roots:
        if not root.is_dir():
            continue
        stack: list[tuple[Path, int]] = [(root, 0)]
        while stack:
            folder, depth = stack.pop()
            try:
                children = sorted(p for p in folder.iterdir() if p.is_dir() and not p.name.startswith("."))
            except OSError:
                continue
            if depth < max_depth:
                stack.extend((c, depth + 1) for c in children)
            if folder == root:
                continue
            imgs, vids = _count_media(folder)
            if imgs + vids == 0 and not children:
                continue
            rel = folder.relative_to(root)
            info = parse_folder_name(folder.name, today)
            source = "folder_name" if info["date"] else None
            if info["date"] is None:  # 하위 폴더(예: 1_카페)는 상위 폴더의 날짜를 물려받습니다.
                for parent in rel.parents:
                    if str(parent) != ".":
                        pinfo = parse_folder_name(parent.name, today)
                        if pinfo["date"]:
                            info["date"], info["year_inferred"], source = pinfo["date"], pinfo["year_inferred"], "parent_folder"
                            break
            parent_labels = [parse_folder_name(p.name, today)["label"] for p in reversed(rel.parents) if str(p) != "."]
            found.append({
                "path": str(folder),
                "relative": _nfc(str(rel)),
                "date": info["date"],
                "date_source": source,
                "year_inferred": info["year_inferred"],
                "label": info["label"],
                "parent_labels": parent_labels,
                **({"order": info["order"]} if "order" in info else {}),
                "images": imgs,
                "videos": vids,
                "subfolders": [_nfc(c.name) for c in children],
            })
    found.sort(key=lambda r: (r["date"] or "0000", r["relative"]), reverse=True)
    return found


# ---------------------------------------------------------------- 사용자 입력 해석과 비교

def _strip_josa(w: str) -> str:
    for j in _JOSA:
        if w.endswith(j) and len(w) - len(j) >= 2:
            return w[: -len(j)]
    return w


def parse_query(query: str, today: date | None = None) -> dict[str, Any]:
    """사용자 입력에서 날짜와 이름 후보 단어를 뽑습니다."""
    q = _nfc(query)
    hits = D.find_dates(q, today=today, relative=True)
    rest = D.remove_spans(q, hits)
    terms: list[str] = []
    for w in _SPLIT.split(rest.casefold()):
        if not w or w in STOPWORDS:
            continue
        w = _strip_josa(w)
        if not w or w in STOPWORDS or _FILLER.search(w) or re.fullmatch(r"\d+(일|월|년)?", w):
            continue
        if w not in terms:
            terms.append(w)
    return {"dates": hits, "terms": terms}


def term_score(term: str, text: str) -> float:
    """단어가 이름에 얼마나 맞는지(0~1). 그대로 들어 있으면 1, 아니면 자모 단위로 가장 비슷한 부분과 비교."""
    t, h = _compact(term), _compact(text)
    if not t or not h:
        return 0.0
    if t in h:
        return 1.0
    tj, hj = _jamo(t), _jamo(h)
    n = len(tj)
    best = 0.0
    for w in range(max(1, n - 2), n + 3):
        for i in range(0, max(1, len(hj) - w + 1)):
            r = SequenceMatcher(None, tj, hj[i:i + w]).ratio()
            if r > best:
                best = r
    return round(best, 3)


def _fmt_date(iso: str | None) -> str:
    if not iso:
        return "날짜 정보 없음"
    d = date.fromisoformat(iso)
    return f"{d.year}년 {d.month}월 {d.day}일"


def search_folders(roots: list[Path], query: str = "", today: date | None = None) -> dict[str, Any]:
    """폴더를 찾고, 바로 진행해도 되는지(exact) 사용자 확인이 필요한지(needs_confirmation) 판단합니다."""
    today = today or date.today()
    folders = walk_folders(roots, today=today)
    if not query.strip():
        return {"match": "list", "needs_confirmation": False, "best": None, "candidates": [], "folders": folders,
                "parsed": {"dates": [], "terms": [], "ignored_terms": []}, "confirm_question": None}

    pq = parse_query(query, today)
    hits: list[D.DateHit] = pq["dates"]
    terms: list[str] = pq["terms"]

    # 장소명 점수: 폴더 이름(label)과 상위 폴더 이름 중 더 잘 맞는 쪽
    for f in folders:
        texts = [f["label"], *f["parent_labels"]]
        f["_terms"] = {t: max(term_score(t, x) for x in texts) for t in terms}
    ignored = [t for t in terms if max((f["_terms"][t] for f in folders), default=0) < TERM_IGNORE]
    kept = [t for t in terms if t not in ignored]

    for f in folders:
        name_score = sum(f["_terms"][t] for t in kept) / len(kept) if kept else None
        date_score, date_note = None, None
        if hits:
            target = f["date"]
            if target is None:
                target = first_photo_date(Path(f["path"]))
                if target:
                    f["date"], f["date_source"] = target, "photo_exif"
            if target:
                scored = [D.compare(h, date.fromisoformat(target)) + (h,) for h in hits]
                date_score, date_note, hit = max(scored, key=lambda x: x[0])
                f["_date_hit"] = hit
            else:
                date_score, date_note = 0.0, "폴더에 날짜 정보 없음"
        if name_score is not None and date_score is not None:
            score = 0.65 * name_score + 0.35 * date_score
        else:
            score = name_score if name_score is not None else (date_score or 0.0)
        f.update(name_score=None if name_score is None else round(name_score, 3),
                 date_score=date_score, date_note=date_note, score=round(score, 3))

    def is_exact(f: dict) -> bool:
        if not kept and not hits:
            return False
        return (f["name_score"] is None or f["name_score"] >= 0.999) and (f["date_score"] is None or f["date_score"] >= 0.999)

    exact = [f for f in folders if is_exact(f)]
    candidates = sorted(
        (f for f in folders if f["score"] >= 0.5 or (f["name_score"] or 0) >= TERM_MATCH or (f["date_score"] or 0) >= 0.5),
        key=lambda f: f["score"], reverse=True,
    )
    parsed = {"dates": [h.label() for h in hits], "terms": kept, "ignored_terms": ignored}
    for f in (exact + candidates[:5]) or folders[:5]:
        f.setdefault("photo_date", first_photo_date(Path(f["path"])))

    if len(exact) == 1 and not ignored:
        best = exact[0]
        _pending.pop(_key(best["path"]), None)
        result = {"match": "exact", "needs_confirmation": False, "best": best, "candidates": [best], "confirm_question": None}
    elif candidates:
        best = candidates[0]
        result = {"match": "needs_confirmation", "needs_confirmation": True, "best": best, "candidates": candidates[:5],
                  "confirm_question": _question(query, best, candidates[1:4], kept, ignored, multiple_exact=len(exact) > 1)}
    else:
        result = {"match": "none", "needs_confirmation": True, "best": None, "candidates": folders[:5],
                  "confirm_question": _question_none(query, folders[:5])}
    for f in result["candidates"]:
        if result["needs_confirmation"]:
            _pending[_key(f["path"])] = result["confirm_question"]
    result["parsed"] = parsed
    result["folders"] = []
    for f in folders:
        for k in ("_terms", "_date_hit"):
            f.pop(k, None)
    return result


def _describe(f: dict) -> str:
    year_note = " (연도는 폴더 이름에 없어 추정)" if f.get("year_inferred") else ""
    src = {"photo_exif": " (사진 촬영일 기준)", "parent_folder": " (상위 폴더 기준)"}.get(f.get("date_source") or "", "")
    return f"'{f['relative']}' · 날짜: {_fmt_date(f['date'])}{src}{year_note} · 장소: {f['label']} · 사진 {f['images']}장"


def _question(query: str, best: dict, others: list[dict], kept: list[str], ignored: list[str], multiple_exact: bool) -> str:
    diffs: list[str] = []
    hit = best.get("_date_hit")
    if hit is not None and (best.get("date_score") or 0) < 0.999:
        diffs.append(f"날짜: 말씀하신 {hit.label()} → 폴더에는 {_fmt_date(best['date'])}")
    for t in kept:
        if best["_terms"].get(t, 0) < 0.999:
            diffs.append(f"이름: 말씀하신 '{t}' → 폴더에는 '{best['label']}'")
    if ignored:
        diffs.append(f"폴더 이름에서 찾지 못한 말: {', '.join(ignored)}")
    head = ("같은 조건의 폴더가 여러 개 있습니다. 가장 먼저 찾은 폴더는 아래와 같습니다." if multiple_exact
            else f"말씀하신 '{query.strip()}'와 정확히 같은 폴더는 없지만, 가장 비슷한 폴더를 찾았습니다.")
    lines = [head, "", f"- 폴더 이름: {best['relative']}", f"- 폴더에 저장된 날짜: {_fmt_date(best['date'])}",
             f"- 장소(식당) 이름: {best['label']}", f"- 사진 {best['images']}장, 영상 {best['videos']}개"]
    if best.get("photo_date") and best["photo_date"] != best["date"]:
        lines.append(f"- 사진 촬영일: {_fmt_date(best['photo_date'])}")
    if diffs:
        lines += ["", "다른 점:"] + [f"- {d}" for d in diffs]
    if others:
        lines += ["", "다른 후보:"] + [f"- {_describe(o)}" for o in others]
    lines += ["", "이게 맞습니까?"]
    return "\n".join(lines)


def _question_none(query: str, recent: list[dict]) -> str:
    lines = [f"말씀하신 '{query.strip()}'와 비슷한 폴더를 찾지 못했습니다. 최근 폴더는 다음과 같습니다."]
    lines += [f"- {_describe(f)}" for f in recent] or ["- (사진이 든 폴더가 없습니다)"]
    lines += ["", "이 중에 맞는 폴더가 있습니까? 없으면 Google Drive에 사진이 다 올라갔는지 확인해 주세요."]
    return "\n".join(lines)


# ---------------------------------------------------------------- 확인 상태

def pending_question(path: str | Path) -> str | None:
    """사용자 확인이 필요한 폴더면 그 질문을, 아니면 None을 돌려줍니다."""
    return _pending.get(_key(path))


def mark_confirmed(path: str | Path) -> None:
    _pending.pop(_key(path), None)
