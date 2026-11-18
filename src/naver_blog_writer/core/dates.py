"""날짜 표현 해석. 폴더 이름과 사용자 입력을 같은 규칙으로 읽습니다.

지원 형식 (공백, 괄호, 앞뒤 글자와 상관없이 문자열 어디에 있어도 찾습니다)
- 연월일: 2026-11-14, 2026.11.14, 2026/11/14, 2026_11_14, 2026. 11. 14., 20261114
- 두 자리 연도: 26.11.14, 26-11-14, 26/11/14, 261114
- 한국어: 2026년 11월 14일, 26년 11월 14일, 11월 14일, 11월14일, 11월 14
- 월일: 11.14, 11/14, 11-14, 11_14, 1114 (4자리는 월 01~12, 일 01~31일 때만)
- 영어: Nov 14, November 14 2026, 14 Nov 2026
- 일만: 14일 (월은 모름)
- 상대 날짜(사용자 입력만): 오늘, 어제, 그제, 그저께, 엊그제
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

_MONTHS_EN = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_EN = r"(?P<mon>jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_SEP = r"\s*[.\-/_]\s*"
# 숫자 뒤에 단위가 오면 날짜가 아닙니다(예: 9.5천원, 3.5km, 10-20분).
_NOT_UNIT = r"(?!\s*(?:천|만|원|%|배|시간|시|분|초|개|명))(?!\s*(?:kg|km|cm|mm|ml|g|m|l)(?![A-Za-z]))"

# 앞에 있을수록 우선합니다. 한 번 잡힌 글자 범위는 다음 패턴이 다시 쓰지 않습니다.
_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?<!\d)(?P<y>\d{4}|\d{2})\s*년\s*(?P<m>\d{1,2})\s*월\s*(?P<d>\d{1,2})\s*일?"),
    re.compile(r"(?<!\d)(?P<y>\d{4})" + _SEP + r"(?P<m>\d{1,2})" + _SEP + r"(?P<d>\d{1,2})\.?(?!\d)"),
    re.compile(r"(?<!\d)(?P<y>\d{2})" + _SEP + r"(?P<m>\d{1,2})" + _SEP + r"(?P<d>\d{1,2})\.?(?!\d)"),
    re.compile(r"(?<!\d)(?P<y>(?:19|20)\d{2})(?P<m>\d{2})(?P<d>\d{2})(?!\d)"),
    re.compile(r"(?<!\d)(?P<y>\d{2})(?P<m>\d{2})(?P<d>\d{2})(?!\d)"),
    re.compile(r"(?<!\d)(?P<m>\d{1,2})\s*월\s*(?P<d>\d{1,2})\s*일?"),
    re.compile(r"(?<![A-Za-z])" + _EN + r"\s*(?P<d>\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(?P<y>\d{4}))?(?!\d)", re.I),
    re.compile(r"(?<!\d)(?P<d>\d{1,2})(?:st|nd|rd|th)?\s+" + _EN + r"(?:,?\s*(?P<y>\d{4}))?", re.I),
    re.compile(r"(?<![\d.\-/_])(?P<m>\d{1,2})" + _SEP + r"(?P<d>\d{1,2})(?![\d.\-/_]?\d)" + _NOT_UNIT),
    re.compile(r"(?<![\dA-Za-z_])(?P<m>0[1-9]|1[0-2])(?P<d>0[1-9]|[12]\d|3[01])(?!\d)" + _NOT_UNIT),
    re.compile(r"(?<!\d)(?P<d>\d{1,2})\s*일(?!\s*(?:간|째|동안|정도))"),
]

_RELATIVE = {"오늘": 0, "어제": 1, "그제": 2, "그저께": 2, "엊그제": 2}


@dataclass(frozen=True)
class DateHit:
    """찾은 날짜 하나. year나 month가 None이면 입력에 그 정보가 없었다는 뜻입니다."""

    year: int | None
    month: int | None
    day: int
    start: int
    end: int
    text: str

    def label(self) -> str:
        if self.month is None:
            return f"{self.day}일"
        if self.year is None:
            return f"{self.month}월 {self.day}일"
        return f"{self.year}년 {self.month}월 {self.day}일"

    def iso(self) -> str | None:
        if self.year is None or self.month is None:
            return None
        return date(self.year, self.month, self.day).isoformat()


def _valid(y: int | None, m: int | None, d: int) -> bool:
    if m is not None and not 1 <= m <= 12:
        return False
    if not 1 <= d <= 31:
        return False
    if m is None:
        return True
    try:
        date(y if y is not None else 2024, m, d)  # 2024는 윤년이라 2월 29일도 통과
    except ValueError:
        return False
    return True


def find_dates(text: str, today: date | None = None, relative: bool = False) -> list[DateHit]:
    """문자열에서 날짜를 모두 찾아 등장 순서대로 돌려줍니다."""
    hits: list[DateHit] = []
    taken: list[tuple[int, int]] = []
    for pat in _PATTERNS:
        for m in pat.finditer(text):
            s, e = m.span()
            if any(s < te and ts < e for ts, te in taken):
                continue
            gd = m.groupdict()
            y = gd.get("y")
            year = None if y is None else int(y) + (2000 if len(y) == 2 else 0)
            if gd.get("mon"):
                month: int | None = _MONTHS_EN[gd["mon"].lower()[:3]]
            elif gd.get("m"):
                month = int(gd["m"])
            else:
                month = None
            day = int(gd["d"])
            if not _valid(year, month, day):
                continue
            taken.append((s, e))
            hits.append(DateHit(year, month, day, s, e, m.group(0)))
    if relative:
        today = today or date.today()
        for word, back in _RELATIVE.items():
            for m in re.finditer(word, text):
                s, e = m.span()
                if any(s < te and ts < e for ts, te in taken):
                    continue
                d = today - timedelta(days=back)
                taken.append((s, e))
                hits.append(DateHit(d.year, d.month, d.day, s, e, word))
    hits.sort(key=lambda h: h.start)
    return hits


def remove_spans(text: str, hits: list[DateHit]) -> str:
    """찾은 날짜 부분을 공백으로 바꾼 문자열을 돌려줍니다(나머지 글자로 이름을 찾기 위해)."""
    out = text
    for h in sorted(hits, key=lambda h: h.start, reverse=True):
        out = out[: h.start] + " " + out[h.end:]
    return out


def resolve_year(month: int, day: int, today: date | None = None) -> date:
    """연도 없는 월일을 오늘 기준 가장 최근의 지난 날짜로 정합니다(다녀온 날은 과거이므로)."""
    today = today or date.today()
    for y in (today.year, today.year - 1, today.year - 2):
        try:
            d = date(y, month, day)
        except ValueError:
            continue
        if d <= today + timedelta(days=1):
            return d
    return date(today.year - 1, month, min(day, 28))


def compare(query: DateHit, target: date) -> tuple[float, str]:
    """사용자가 말한 날짜와 폴더 날짜를 비교합니다. (점수 0~1, 설명)"""
    if query.month is None:
        return (0.6, "일치(일만 비교)") if query.day == target.day else (0.0, "다름")
    same_md = query.month == target.month and query.day == target.day
    if same_md and (query.year is None or query.year == target.year):
        return 1.0, "일치"
    if same_md:
        return 0.7, "연도만 다름"
    if query.month == target.month:
        a, b = f"{query.day:02d}", f"{target.day:02d}"
        if sum(x != y for x, y in zip(a, b, strict=True)) == 1:  # 14와 24처럼 숫자 하나만 다른 경우(오타 가능성)
            return 0.5, "일의 숫자 하나가 다름"
        if abs(query.day - target.day) <= 2:
            return 0.4, "1~2일 차이"
        return 0.2, "같은 달, 다른 날"
    return 0.0, "다름"
