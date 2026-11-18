"""본문 포맷 정규화와 점검(lint).

규칙 (style/default.md 4-4절과 같음. 네이버 맛집, 여행 상위 블로그 48편을 분석한 기준)
- 한 줄 = 한 호흡. 에디터의 한 문단(줄)은 보통 15~28자다. 작성자가 나눈 줄(\\n)은 그대로 둔다.
- 너무 긴 줄(LINE_MAX자 초과)은 문장 끝, 쉼표, 연결 어미(~고, ~는데, ~서 등) 뒤에서 자동으로 나눈다.
  문장을 서로 합치지는 않는다.
- 빈 줄(\\n\\n)은 생각 묶음의 경계다. 묶음은 보통 1~4줄이고 묶음 사이에는 빈 줄이 하나 들어간다.
- 문장 앞 공백(들여쓰기)과 겹친 공백은 없앤다.
- 체크리스트와 정보 줄(✔, 📍, ⏰, •, -, 1.)은 쪼개지 않는다. 직접 입력 자리와 추천 문구 줄도 쪼개지 않는다.
- 협찬 표기처럼 통째로 들어가야 하는 문구는 keep=True로 넘겨 나누지 않는다.
- 줄 안의 **글자**는 에디터에서 굵게 들어간다(editor.type_line).
"""

from __future__ import annotations

import re
import unicodedata

_ZERO_WIDTH = re.compile(r"[​‌‍﻿]")
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⭐❤✅✔][️‍]*")
# 정보 줄과 목록 줄: 체크 표시, 이모지 머리표, 목록 기호, 번호
_LIST_LINE = re.compile(r"^(?:[✔✅☑•·\-–▶▪︎◾️●○]|[\U0001F4CC\U0001F4CD⏰\U0001F552\U0001F697\U0001F4DE\U0001F4B0\U0001F37D☕\U0001F68C\U0001F687]|\d{1,2}[.)])\s*")
# 문장 끝: 마침표, 물음표, 느낌표 (뒤에 닫는 따옴표나 괄호, 이모지가 올 수 있음) 다음 공백
_SENT_END = re.compile(r"(?<=[.!?…~])[\"'”’)\]]*\s+")
# 줄을 나누기 좋은 자리: 쉼표 뒤, 연결 어미 뒤의 공백
_SOFT_BREAK = re.compile(r"(?:,|고|는데|은데|지만|어서|아서|해서|라서|니까|면서|다가|러|보니|하니|더니|에서|으로|하고|이랑)\s")

LINE_MAX = 32      # 이보다 긴 줄은 자동으로 나눕니다(상위 블로그 한 줄 길이 p75 28자, p90 42자)
LINE_TARGET = 22   # 나눌 때 목표로 삼는 길이

SPONSOR_WORDS = ("협찬", "광고", "제공받", "원고료", "지원받", "대가")

# 맛 평가, 외관 후기, 느낀 점처럼 사람의 주관이 필요한 자리에 넣는 문구. 사용자가 발행 전에 직접 채웁니다.
PLACEHOLDER = "[이 부분에 직접 입력하시기 바랍니다]"
# 직접 입력 자리 아래에 붙는 추천 문구 줄의 머리말. 사용자가 고르거나 고쳐 쓰고, 이 머리말 줄은 발행 전에 지웁니다.
SUGGESTION_PREFIX = "(추천 문구)"
CHECK_MARK = re.compile(r"\[\[확인필요[^\]]*\]\]")
# 사용자 메모 없이 Claude가 쓰면 안 되는 주관 표현(경고만 하고 저장은 막지 않습니다).
SUBJECTIVE = re.compile(
    r"맛있|맛이 좋|맛있었|존맛|꿀맛|맛집 인정|최고|최애|인생|감동|만족|실망|아쉬웠|아쉽|별로|추천해요|추천합니다|강추|"
    r"재방문|또 올|또 가고|친절|불친절|분위기(가|도)? (좋|최고|예쁘|아늑)|예뻤|예쁘더|깔끔했|고급스럽|훌륭|완벽|행복했|좋았"
)
# 사진 촬영 시각을 그대로 옮긴 표현(예: 오후 2시 17분). 사람이 쓴 후기에는 거의 없습니다.
EXIF_TIME = re.compile(r"(오전|오후)?\s?\d{1,2}시\s?\d{1,2}분")
# 소제목으로 쓰면 정보 문서처럼 보이는 표현
FAQ_SUBTITLE = re.compile(r"자주\s?묻는\s?질문|FAQ|Q\s?&\s?A", re.I)
# 사람이 쓰지 않는 억지 장치: 퀴즈, 비유 소제목
GIMMICK = re.compile(r"뭐로\s?보이|무엇일까요|뭘까요|정답은|맞혀\s?보|퀴즈|퍼레이드|향연|대잔치|릴레이|쉼표|끝판왕|신세계")
# 음식 생김새를 사진 보고 해설하는 줄(음식 이름과 설명은 fill의 추천 문구로만 씁니다)
PHOTO_NARRATION = re.compile(r"접시|한\s?점|단면|올라가|올라와|놓여|담겨|색이|윤기|모양|빼곡|그을린|껍질|속이\s?꽉|토핑|소스가|소스 위")
# 제목 맨 앞 [분류]
TITLE_DOMAINS = ("맛집 추천", "카페 추천", "데이트 기록", "여행 기록", "일상", "웨딩 준비", "결혼 준비", "취준 기록")


def clean_line(s: str) -> str:
    s = unicodedata.normalize("NFC", _ZERO_WIDTH.sub("", s))
    s = s.replace(" ", " ").replace("\t", " ")
    return re.sub(r" {2,}", " ", s).strip()


def visible(s: str) -> str:
    """화면에 보일 글자(굵게 표시 ** 제거)."""
    return re.sub(r"\*\*(.+?)\*\*", r"\1", s)


def split_sentences(line: str) -> list[str]:
    parts = [p.strip() for p in _SENT_END.split(line) if p and p.strip()]
    return parts or [line]


def _info_line(line: str) -> bool:
    """정보 줄, 직접 입력 자리, 추천 문구, 태그, 주소 줄. 말투 점검과 자동 나눔에서 뺍니다."""
    return (bool(_LIST_LINE.match(line)) or line.startswith(PLACEHOLDER) or line.startswith(SUGGESTION_PREFIX)
            or line.startswith("#") or "http" in line)


def _protected(line: str) -> bool:
    """자동으로 나누지 않을 줄. 굵게 표시(**)가 있는 줄은 표시가 갈라지지 않게 나누지 않습니다."""
    return _info_line(line) or "**" in line


def _break_long(line: str) -> list[str]:
    """LINE_MAX보다 긴 줄을 자연스러운 자리에서 나눕니다. 나눌 자리가 없으면 그대로 둡니다."""
    if len(line) <= LINE_MAX or _protected(line):
        return [line]
    sents = split_sentences(line)
    if len(sents) > 1:
        return [x for s in sents for x in _break_long(s)]
    best = None
    for m in _SOFT_BREAK.finditer(line):
        cut = m.end()
        left, right = line[:cut].strip(), line[cut:].strip()
        if len(left) < 8 or len(right) < 6:
            continue
        score = abs(len(left) - max(LINE_TARGET, len(line) / 2 if len(line) < 2 * LINE_MAX else LINE_TARGET))
        if best is None or score < best[0]:
            best = (score, left, right)
    if best is None:
        return [line]
    return [best[1]] + _break_long(best[2])


def to_paragraphs(text: str, keep: bool = False) -> list[list[str]]:
    """텍스트를 묶음(빈 줄로 나뉨) 목록으로 바꿉니다. 각 묶음은 줄 목록이고, 줄 하나가 에디터의 한 문단입니다."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # 직접 입력 자리는 앞 문장과 섞이지 않게 줄 맨 앞에 둡니다(바로 아래 추천 문구 줄은 같은 묶음).
    text = re.sub(r"[ \t]*" + re.escape(PLACEHOLDER), "\n\n" + PLACEHOLDER, text)
    raw_groups = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    out: list[list[str]] = []
    for raw in raw_groups:
        lines = [clean_line(x) for x in raw.split("\n")]
        lines = [x for x in lines if x]
        if not lines:
            continue
        if keep:
            out.append(lines)
            continue
        out.append([part for ln in lines for part in _break_long(ln)])
    return out


def render_fill(suggestions: list[str] | None) -> str:
    """fill 블록(직접 입력 자리 + 추천 문구)을 본문 텍스트로 바꿉니다."""
    lines = [PLACEHOLDER]
    for s in suggestions or []:
        s = clean_line(s)
        if s:
            lines.append(f"{SUGGESTION_PREFIX} {s}")
    return "\n".join(lines)


def plain_len(text: str) -> int:
    return len(re.sub(r"\s", "", text))


def _ending(line: str) -> str:
    s = _EMOJI.sub("", visible(line)).strip()
    s = re.sub(r"[\s.!?~,…:;)\]^ㅎㅋㅠㅜ]+$", "", s)
    return s[-2:] if len(s) >= 2 else s


def lint(title: str, blocks: list[dict], tags: list[str] | None, sponsored: str | None) -> list[str]:
    """저장을 막지는 않는 경고 목록. dry_run 결과에 포함됩니다."""
    w: list[str] = []
    t = title.strip()
    if not 20 <= len(t) <= 60:
        w.append(f"제목 길이 {len(t)}자. 권장 30~55자.")
    t_body = re.sub(r"^\[(협찬|광고)\]\s*", "", t)
    m = re.match(r"^\[([^\]]+)\]", t_body)
    if not m:
        w.append("제목은 [분류]로 시작합니다: [맛집 추천] 상호명 시간대 위치 내돈내산 후기 (feat. 특징) 이모지 (스타일 가이드 4-6절).")
    elif m.group(1).strip() not in TITLE_DOMAINS:
        w.append(f"제목 분류 [{m.group(1)}] 대신 {', '.join('[' + d + ']' for d in TITLE_DOMAINS)} 중 하나를 씁니다.")
    if not _EMOJI.search(t[-3:]):
        w.append("제목 맨 끝에 이모지를 1개 붙입니다(예: 🍣 ☕ 🍷).")
    if re.search(r"[!?~@#$%^*=+<>{}\\]", t):
        w.append("제목에 ! ? ~ # 같은 기호가 있습니다. 쓸 수 있는 기호는 [ ] ｜ | , & ( ) . 정도입니다.")
    if len(_EMOJI.findall(t)) > 1:
        w.append("제목 이모지는 맨 끝에 1개까지만 씁니다.")
    if re.search(r"부터.{1,25}까지", t):
        w.append("제목에 '~부터 ~까지'처럼 음식을 늘어놓지 않습니다. 음식 이름은 제목에 쓰지 않습니다.")
    if sponsored and "내돈내산" in t:
        w.append("협찬 글 제목에 '내돈내산'을 쓸 수 없습니다(법적 표기 위반).")

    total = run = max_run = 0
    n_img = 0
    subtitles: list[str] = []
    lines: list[str] = []
    for b in blocks:
        typ = b.get("type")
        if typ in ("text", "fill"):
            txt = (b.get("text") or "") if typ == "text" else render_fill(b.get("suggestions"))
            n = plain_len(txt)
            total += n
            run += n
            max_run = max(max_run, run)
            if typ == "text":
                lines += [ln for g in to_paragraphs(txt) for ln in g]
        else:
            run = 0
            n_img += typ == "image"
            if typ == "subtitle":
                subtitles.append(b.get("text") or "")
    if max_run > 450:
        w.append(f"사진이나 소제목 없이 글이 {max_run}자 이어집니다. 사진 사이 글은 1~5줄(묶음 1~2개)이 보통입니다.")
    if total < 600:
        w.append(f"본문이 {total}자(공백 제외)로 짧습니다. 스타일 가이드 4-6절의 글 유형별 분량을 확인하세요.")
    if total > 3500:
        w.append(f"본문이 {total}자(공백 제외)로 깁니다. 3,000자 안쪽을 권장합니다.")
    if n_img < 3:
        w.append(f"사진이 {n_img}장입니다. 장소 후기는 10장 이상이 보통입니다.")
    if tags is not None and not 5 <= len(tags) <= 15:
        w.append(f"태그가 {len(tags)}개입니다. 8~12개를 권장합니다.")
    if sponsored is not None and not any(k in sponsored for k in SPONSOR_WORDS):
        w.append("협찬 문구에 '협찬', '광고', '제공받아' 같은 명확한 표현이 없습니다.")

    # 사람이 쓴 것처럼 보이게 하는 점검
    body = [ln for ln in lines if not _info_line(ln)]
    long_lines = [ln for ln in body if len(visible(ln)) > 40]
    if long_lines:
        w.append(f"40자가 넘는 줄 {len(long_lines)}개: {[x[:20] for x in long_lines[:3]]}. 한 줄 한 호흡(15~28자)으로 \\n을 넣어 나누세요.")
    if len(body) >= 10:
        dots = sum(1 for ln in body if visible(ln).rstrip().endswith("."))
        if dots / len(body) > 0.5:
            w.append(f"줄 {len(body)}개 중 {dots}개가 마침표로 끝납니다. 상위 블로그는 대부분 마침표 없이 끝내고 ! ~ ㅎㅎ :) 를 가끔 섞습니다.")
    streak, prev, worst = 1, None, (1, "")
    for ln in body:
        e = _ending(ln)
        streak = streak + 1 if e and e == prev else 1
        prev = e
        if streak > worst[0]:
            worst = (streak, e)
    if worst[0] >= 4:
        w.append(f"'~{worst[1]}'로 끝나는 줄이 {worst[0]}번 연달아 나옵니다. ~어요, ~더라고요, ~네요, ~죠, 명사로 끝내기처럼 섞으세요.")
    exif = [ln for ln in body if EXIF_TIME.search(ln)]
    if exif:
        w.append(f"촬영 시각을 분 단위로 쓴 줄 {len(exif)}개: {[x[:20] for x in exif[:2]]}. '점심쯤', '오픈 직후'처럼 쓰거나 빼세요.")
    gimmicks = [x for x in [t] + subtitles + body if GIMMICK.search(x)]
    if gimmicks:
        w.append(f"사람이 쓰지 않는 장치(퀴즈, 비유 소제목) {len(gimmicks)}곳: {[x[:20] for x in gimmicks[:3]]}. 빼거나 평범한 말로 바꾸세요.")
    narration = [x for x in body if PHOTO_NARRATION.search(x)]
    if narration:
        w.append(f"음식 생김새를 해설하는 줄 {len(narration)}개: {[x[:20] for x in narration[:3]]}. 음식 이름과 설명은 본문에 쓰지 말고 "
                 "fill 블록의 추천 문구로만 넣으세요(스타일 가이드 2-1절).")
    odd = [b.get("style") for b in blocks if b.get("type") == "quote" and b.get("style") not in (None, "quotation_bubble", "quotation_postit", "quotation_corner")]
    if odd:
        w.append(f"마무리 상자 모양 {odd}: 기본은 말풍선, 포스트잇, 프레임입니다. 사용자가 요청한 모양이면 그대로 둡니다.")
    quotes = [i for i, b in enumerate(blocks) if b.get("type") == "quote"]
    if not quotes or quotes[-1] < len(blocks) - 3:
        w.append("글 끝에 마무리 상자(quote, 말풍선, 포스트잇, 프레임 중 하나)가 없습니다. 추천 상황과 댓글, 공감, 이웃추가 같은 참여 문구를 2~4줄로 넣으세요.")
    if any(FAQ_SUBTITLE.search(s) for s in subtitles):
        w.append("'자주 묻는 질문' 소제목은 쓰지 않습니다. 필요한 정보는 본문 흐름과 정보 줄에 넣으세요.")
    q_subs = [s for s in subtitles if s.rstrip().endswith("?")]
    if len(q_subs) > 1:
        w.append(f"질문형 소제목이 {len(q_subs)}개입니다. 상위 블로그는 '메뉴', '내부', 메뉴 이름 같은 짧은 소제목을 씁니다. 질문형은 1개까지.")

    texts = [b.get("text") or "" for b in blocks if b.get("type") in ("text", "subtitle")]
    texts += [b.get("caption") or "" for b in blocks if b.get("type") == "image"]
    n_ph = sum(t.count(PLACEHOLDER) for t in texts) + sum(1 for b in blocks if b.get("type") == "fill")
    if n_ph == 0 and n_img >= 3:
        w.append("주관 평가 자리가 없습니다. 맛 평가, 외관 후기, 느낀 점은 직접 쓰지 말고 fill 블록(직접 입력 자리 + 추천 문구)으로 남기세요.")
    if any(b.get("type") == "fill" and not b.get("suggestions") for b in blocks):
        w.append("추천 문구가 없는 fill 블록이 있습니다. 상황에 맞는 추천 문구를 2~3개 넣으세요(스타일 가이드 2-2절).")
    subj = [x for t in texts for ln in t.split("\n") if not ln.strip().startswith((PLACEHOLDER, SUGGESTION_PREFIX))
            for x in split_sentences(ln) if SUBJECTIVE.search(x)]
    if subj:
        w.append(f"주관 평가로 보이는 문장 {len(subj)}개: {[x[:30] for x in subj[:3]]}. 사용자 메모에 있는 평가가 아니면 "
                 f"fill 블록의 추천 문구로 옮기세요.")
    return w


def count_placeholders(title: str, blocks: list[dict]) -> dict[str, int]:
    """사용자가 발행 전에 채워야 할 자리 수."""
    texts = [title] + [b.get("text") or b.get("caption") or "" for b in blocks]
    n_fill = sum(1 for b in blocks if b.get("type") == "fill")
    n_sugg = sum(len([s for s in b.get("suggestions") or [] if s.strip()]) for b in blocks if b.get("type") == "fill")
    return {"직접_입력": sum(t.count(PLACEHOLDER) for t in texts) + n_fill,
            "추천_문구_줄": n_sugg + sum(t.count(SUGGESTION_PREFIX) for t in texts),
            "확인필요": sum(len(CHECK_MARK.findall(t)) for t in texts)}
