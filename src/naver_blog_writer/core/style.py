"""스타일 가이드 조립.

순서와 우선순위 (뒤로 갈수록 우선, 단 기본 가이드 2절 '절대 규칙'은 항상 지킴)
1. 기본 가이드: style/default.md
2. 최신 경향(자동, 부록 B): NBW_HOME/trends/. 네이버 주제별 인기글의 형식 수치, TREND_MAX_AGE_DAYS일마다 갱신
3. 내 스타일(자동, 10절): NBW_HOME/my_style.md. get_style_guide가 매번 내 블로그 최근 게시물 5개를 읽어 새로 씀
4. 개인 고정 규칙(선택, 11절): STYLE_GUIDE_PATH 파일. 사용자가 직접 관리
"""

from __future__ import annotations

import re

from .. import config
from . import trends

MY_STYLE_HEADER = """

---

# 10절. 내 스타일 (최근 게시물에서 자동 추출, 1~9절보다 우선)

- 문체(어미, 말투, 문장 길이), 문단 길이, 사진과 글의 배치 리듬, 소제목 표현 방식, 이모지 사용량, 분량은 아래 게시물을 따른다.
- 단, 2절 절대 규칙(사실만 쓰기, 주관 평가는 fill 블록의 '[이 부분에 직접 입력하시기 바랍니다]'와 추천 문구, 협찬 표기)은 내 스타일보다 우선한다.
- 내 글이 아직 적거나 한 줄이 길고 왼쪽 정렬이면, 4-4절(한 줄 한 호흡, 가운데 정렬)을 따른다. 어미와 말투는 내 글을 따른다.
- 아래 게시물의 제목, 소제목, 도입 문장, 마무리 문장, 문장을 그대로 다시 쓰지 않는다. write_post가 이 게시물들과 겹치는지 검사한다.

"""


def personal_rules(text: str) -> str:
    """개인 고정 규칙 파일에서 주석(<!-- -->)을 뺀 내용. 제목 줄만 남으면 빈 문자열(11절을 붙이지 않음)."""
    body = re.sub(r"<!--.*?-->", "", text, flags=re.S).strip()
    has_rule = any(ln.strip() and not ln.lstrip().startswith("#") for ln in body.splitlines())
    return body if has_rule else ""


def load_style_guide(topic: str | None = None) -> dict:
    parts: list[str] = []
    sources: list[str] = []
    if config.DEFAULT_STYLE_GUIDE.is_file():
        parts.append(config.DEFAULT_STYLE_GUIDE.read_text(encoding="utf-8"))
        sources.append(str(config.DEFAULT_STYLE_GUIDE))
    trend = trends.load(topic) if topic and config.READ_OTHER_BLOGS else None
    if trend and trend.get("aggregate", {}).get("posts"):
        parts.append("\n\n---\n\n" + trends.render(trend))
        sources.append(f"최신 경향: {trend['directory_name']} ({trend['updated_at']})")
    if config.MY_STYLE_PATH.is_file():
        parts.append(MY_STYLE_HEADER + config.MY_STYLE_PATH.read_text(encoding="utf-8"))
        sources.append(str(config.MY_STYLE_PATH))
    if config.STYLE_GUIDE_PATH:
        if config.STYLE_GUIDE_PATH.is_file():
            rules = personal_rules(config.STYLE_GUIDE_PATH.read_text(encoding="utf-8"))
            if rules:
                parts.append("\n\n---\n\n# 11절. 개인 고정 규칙 (위 규칙과 충돌하면 이쪽을 따른다)\n\n" + rules)
                sources.append(str(config.STYLE_GUIDE_PATH))
            else:
                sources.append(f"(비어 있음) {config.STYLE_GUIDE_PATH}")
        else:
            sources.append(f"(없음) {config.STYLE_GUIDE_PATH}")
    if not parts:
        raise FileNotFoundError(f"스타일 가이드가 없습니다: {config.DEFAULT_STYLE_GUIDE}")
    return {"sources": sources, "guide": "".join(parts)}
