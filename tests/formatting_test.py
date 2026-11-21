"""본문 정리(한 줄 한 호흡)와 말투 점검(lint) 테스트. 브라우저와 네이버 접속이 없습니다.

    uv run python tests/formatting_test.py
"""

import os
import sys

os.environ["NBW_NO_DOTENV"] = "1"

from naver_blog_writer.core import formatting as f  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def lint(blocks, title="성수 카페 어니언 주말 오전 웨이팅과 빵 메뉴 후기", tags=None):
    return f.lint(title, blocks, tags if tags is not None else ["a"] * 9, None)


def has(ws, key):
    return any(key in w for w in ws)


# 줄 나누기
p = f.to_paragraphs("성수에서 데이트를 했어요. 날씨가 정말 좋더라고요. 첫 코스는 카페였어요.")
check("긴 줄은 문장마다 나눔(합치지 않음)", p == [["성수에서 데이트를 했어요.", "날씨가 정말 좋더라고요.", "첫 코스는 카페였어요."]], str(p))
p = f.to_paragraphs("짧은 문장이에요. 둘째도 짧아요.")
check("32자 이하 줄은 그대로", p == [["짧은 문장이에요. 둘째도 짧아요."]], str(p))
p = f.to_paragraphs("주말에 남편이랑 백화점에 쇼핑하러 갔다가 점심 먹으러 12층 식당가에 있는 솥밥집에 들렀어요")
check("한 문장이 길면 연결 어미 뒤에서 나눔", len(p[0]) == 2 and p[0][0].endswith("갔다가") and all(len(x) <= 32 for x in p[0]), str(p))
p = f.to_paragraphs("첫 줄\n둘째 줄\n\n\n새 묶음")
check("작성자가 나눈 줄과 묶음 유지", p == [["첫 줄", "둘째 줄"], ["새 묶음"]], str(p))
p = f.to_paragraphs("✔ 위치: 경기 고양시 일산서구 킨텍스로 255 상가 234호 현대백화점 킨텍스점 맞은편")
check("정보 줄은 나누지 않음", len(p[0]) == 1, str(p))
p = f.to_paragraphs("2층은 창가 자리가 길게 이어져 있어요. " + f.PLACEHOLDER)
check("직접 입력 문구는 따로 묶음", p == [["2층은 창가 자리가 길게 이어져 있어요."], [f.PLACEHOLDER]], str(p))
r = f.render_fill(["겉은 바삭하고 속은 촉촉했어요", " ", "둘이 나눠 먹기 좋은 양이었어요"])
check("fill: 직접 입력 문구 + 추천 문구 줄", r.split("\n") == [f.PLACEHOLDER, "(추천 문구) 겉은 바삭하고 속은 촉촉했어요",
                                                     "(추천 문구) 둘이 나눠 먹기 좋은 양이었어요"], r)
check("굵게 표시는 화면 글자에서 빠짐", f.visible("**팡도르** / 7,500원") == "팡도르 / 7,500원")

# 말투와 구성 점검
img = [{"type": "image", "path": "x"}] * 4
fill = [{"type": "fill", "topic": "맛", "suggestions": ["겉은 바삭했어요", "양이 넉넉했어요"]}]
human = img + fill + [{"type": "text", "text": "안녕하세요 :)\n주말에 성수 다녀왔어요\n\n웨이팅이 있었는데\n생각보다 금방 들어갔어요!\n\n저희는 팡도르랑 라떼 주문했어요\n빵 고르는 재미가 있더라고요 ㅎㅎ"}]
w = lint(human)
check("사람다운 글은 말투 경고 없음", not any(k in " ".join(w) for k in ("마침표", "연달아", "촬영 시각", "40자", "주관 평가")), str(w))
dots = img + fill + [{"type": "text", "text": "\n".join(f"{i}번째 사실을 적은 줄입니다." for i in range(12))}]
check("마침표로 끝나는 줄이 많으면 경고", has(lint(dots), "마침표로 끝납니다"))
check("같은 어미 4번 연속이면 경고", has(lint(dots), "연달아"))
exif = img + fill + [{"type": "text", "text": "오후 2시 17분쯤 첫 접시가 나왔어요"}]
check("촬영 시각(분 단위) 경고", has(lint(exif), "촬영 시각"))
long_ = img + fill + [{"type": "text", "text": "**이 줄은 굵게 표시가 있어 자동으로 나누지 않는데 너무 길어서 사람 글 같지 않아요**"}]
check("40자 넘는 줄 경고", has(lint(long_), "40자가 넘는 줄"))
faq = img + fill + [{"type": "subtitle", "text": "자주 묻는 질문"}, {"type": "subtitle", "text": "주차는 되나요?"},
                    {"type": "subtitle", "text": "웨이팅은 긴가요?"}]
w = lint(faq)
check("자주 묻는 질문 소제목 경고", has(w, "자주 묻는 질문"))
check("질문형 소제목 2개 이상 경고", has(w, "질문형 소제목"))
check("fill 없이 사진 3장 이상이면 경고", has(lint(img + [{"type": "text", "text": "사실만 있어요"}]), "주관 평가 자리가 없습니다"))
check("추천 문구 없는 fill 경고", has(lint(img + [{"type": "fill", "topic": "맛"}]), "추천 문구가 없는 fill"))
subj = img + fill + [{"type": "text", "text": "국물이 정말 맛있었어요"}]
check("주관 표현 경고(추천 문구 줄은 제외)", has(lint(subj), "주관 평가로 보이는 문장") and not has(lint(human), "주관 평가로 보이는"))
check("제목 기호 경고(! ? ~ #)", has(lint(human, title="성수 카페 어니언 진짜 최고!! 주말 웨이팅 후기 정리"), "기호"))
check("제목 대괄호와 ｜는 허용", not has(lint(human, title="[성수 카페] 어니언｜주말 오전 웨이팅과 빵 메뉴 후기 🥐"), "기호"))
check("태그 수 경고(5~15 밖)", has(lint(human, tags=["a"] * 3), "태그가 3개"))
GOOD_TITLE = "[맛집 추천] 젠스시 주말 런치 일산 킨텍스 내돈내산 후기 (feat. 캐치테이블 예약 꿀팁) 🍣"
closing = [{"type": "quote", "style": "quotation_bubble", "text": "주말 런치 찾는 분이라면 참고해 보세요\n궁금한 점은 댓글로 남겨 주세요 :)"}]
w = lint(human + closing, title=GOOD_TITLE)
check("요청 형식 제목과 마무리 상자는 경고 없음", not any(k in " ".join(w) for k in ("[분류]", "이모지", "부터", "마무리 상자", "기호")), str(w))
check("제목 [분류] 없으면 경고", has(lint(human + closing, title="일산 젠스시 주말 런치 오마카세 후기 정리 🍣"), "[분류]"))
check("제목 분류가 목록 밖이면 경고", has(lint(human + closing, title="[일산 킨텍스] 젠스시 주말 런치 내돈내산 후기 🍣"), "중 하나를 씁니다"))
check("제목 끝 이모지 없으면 경고", has(lint(human + closing, title="[맛집 추천] 젠스시 주말 런치 일산 킨텍스 내돈내산 후기"), "이모지를 1개"))
check("제목에 '~부터 ~까지' 음식 나열 경고", has(lint(human + closing, title="[맛집 추천] 젠스시 주말 런치｜전복부터 구운 가지, 굵은 롤까지 🍣"), "부터 ~까지"))
check("협찬 글 제목의 내돈내산 경고", any("내돈내산" in x for x in f.lint(GOOD_TITLE, human + closing, ["a"] * 9, "이 글은 식사를 제공받아 작성했습니다.")))
check("마무리 상자 없으면 경고", has(lint(human, title=GOOD_TITLE), "마무리 상자"))
quiz = human + [{"type": "subtitle", "text": "초밥 퍼레이드"}, {"type": "text", "text": "여러분은 뭐로 보이세요?\n정답은 가지예요"}] + closing
check("퀴즈와 비유 소제목 경고", has(lint(quiz, title=GOOD_TITLE), "사람이 쓰지 않는 장치"))
narr = human + [{"type": "text", "text": "흰 접시는 그대로 두고\n단면을 보면 계란이 빼곡해요"}] + closing
check("음식 생김새 해설 줄 경고", has(lint(narr, title=GOOD_TITLE), "음식 생김새를 해설"))
LINK = {"type": "link", "url": "https://blog.naver.com/me/224000000001"}
lead = {"type": "text", "text": "맛집 말고 다른 이야기도 궁금하시다면\n여기 남겨 둘게요"}
w = lint(human + closing + [lead, LINK], title=GOOD_TITLE)
check("마무리 상자 뒤 안내 한 줄 + 내 글 링크는 경고 없음", not any(k in " ".join(w) for k in ("마무리 상자", "링크")), str(w))
check("내 글 링크 3개 이상 경고", has(lint(human + closing + [lead, LINK, LINK, LINK], title=GOOD_TITLE), "내 글 링크가 3개"))
check("링크 앞 안내 한 줄 없으면 경고", has(lint(human + closing + [LINK], title=GOOD_TITLE), "안내 한 줄"))
plain = img + fill + [{"type": "text", "text": "\n".join(f"{i}번째 이야기를 적은 줄이에요" for i in range(16))}] + closing
check("긴 글에 이모지가 적으면 경고", has(lint(plain, title=GOOD_TITLE), "이모지가"))
rich = img + fill + [{"type": "text", "text": "\n".join(f"{i}번째 이야기를 적은 줄이에요 😊" for i in range(16))}] + closing
check("이모지를 충분히 쓰면 이모지 경고 없음", not has(lint(rich, title=GOOD_TITLE), "이모지가"))
check("한 줄에 이모지 3개 이상 경고", has(lint(img + fill + [{"type": "text", "text": "너무 좋았어요 😊😋✨"}] + closing, title=GOOD_TITLE), "몰린 줄"))
check("변형 선택자 기호(❤️) 경고", has(lint(img + fill + [{"type": "text", "text": "오늘도 행복 ❤️"}] + closing, title=GOOD_TITLE), "흑백 글자"))
c = f.count_placeholders("제목", human + [{"type": "text", "text": "가격 [[확인필요: 가격]]"}])
check("채울 자리 개수(직접 입력, 추천 문구 줄, 확인필요)", c == {"직접_입력": 1, "추천_문구_줄": 2, "확인필요": 1}, str(c))

# 개인 고정 규칙 파일(11절): 주석은 빼고, 제목만 있으면 붙지 않음
from pathlib import Path  # noqa: E402

from naver_blog_writer.core.style import personal_rules  # noqa: E402

tpl = (Path(__file__).parent.parent / "style" / "style_guide.md").read_text(encoding="utf-8")
check("빈 틀(style_guide.md)은 11절로 붙지 않음", personal_rules(tpl) == "")
check("규칙을 적으면 주석은 빼고 규칙만 붙음", personal_rules(tpl + "\n- 남자친구는 '오빠'라고 부름\n") == "## 규칙\n\n- 남자친구는 '오빠'라고 부름")

failed = [n for n, ok, _ in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} 통과")
sys.exit(1 if failed else 0)
