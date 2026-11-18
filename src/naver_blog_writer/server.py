"""네이버 블로그 작성 MCP 서버 (stdio). 생성과 등록만 합니다. 로직은 tools/와 core/에 있습니다.

주의: stdio MCP에서 stdout은 프로토콜 전용 채널입니다. print()를 쓰지 말고 logging(stderr)만 쓰세요.
"""

from __future__ import annotations

import logging
import sys

from mcp.server.fastmcp import FastMCP

from . import config, prompts, tools

log = logging.getLogger("naver_blog_writer")

INSTRUCTIONS = """네이버 블로그 초안 작성 도구입니다. 결과물은 항상 임시저장이며, 발행은 사용자가 직접 합니다(코드에서 차단됨).
이 서버가 연결된 대화에서 사진, 장소, 날짜와 함께 들어오는 글 요청은 '블로그'라는 말이 없어도(…써줘, 정리해줘, 후기, 포스팅 등) 모두
네이버 블로그 초안 요청으로 보고, 항상 아래 순서와 스타일 가이드대로 작성합니다.
브라우저 작업(최근 글 읽기, 경향 분석, 장소 검색, 같은 장소 리뷰 읽기, 로그인, 글 입력)은 이 서버가 직접 화면에 보이는 크롬 창으로 합니다. 다른 브라우저 MCP를 쓰지 않습니다.

판단 기준 (스타일 가이드 0절)
- 평소에는 사용자 요청, 개인 고정 규칙(11절), 내 글 5개(10절), 같은 장소 리뷰, 최신 경향(부록 B), 기본 가이드를 모두 종합해서 씁니다.
- 서로 부딪쳐 둘 다 지킬 수 없을 때만 순서대로 따릅니다: 바꿀 수 없는 규칙(발행 금지, 하루 한도, 사실 지어내지 않기,
  다른 사람 글 문장 금지, 협찬 표기) > 이번 사용자 요청 > 11절 > 10절 > 같은 장소 리뷰 > 부록 B > 기본 가이드.
- 사용자 요청 때문에 생긴 lint 경고는 고치지 않고 보고에 "요청대로 유지"로 적습니다.

작업 순서
1) get_style_guide(topic=글 주제: 맛집, 카페, 데이트, 여행, 해외여행, 결혼준비, 취업준비): 매번 가장 먼저 호출합니다.
   내 블로그 최근 게시물 5개로 '내 스타일'(10절)을 갱신하고, 그 주제의 '최신 경향'(부록 B, 네이버 주제별 인기글)이 7일보다
   오래됐으면 다시 분석한 뒤 전체 가이드를 돌려줍니다.
2) list_folders(query=사용자가 말한 날짜와 장소 그대로). needs_confirmation=true면 confirm_question을 그대로 보여 주고
   '이게 맞습니까?'에 대한 답을 받은 뒤에만 scan_media(user_confirmed=true)로 진행합니다.
3) scan_media → view_images. 영상은 extract_video_frames.
4) 장소 글이면 find_place(query=장소명, folder=폴더 경로)로 위치를 찾고, place_block을 place 블록으로 씁니다. confident=false면 사용자에게 확인합니다.
5) 장소가 정해지면 장소마다 반드시 place_reviews(place_id=find_place의 place_id)로 같은 장소의 최신 블로그 리뷰 10편을 분석합니다.
   읽지 않은 장소가 있으면 write_post가 저장을 거부합니다. place 블록에는 place_block을 그대로(place_id 포함) 씁니다.
   다른 리뷰의 문장, 제목, 소제목은 쓰지 않고(write_post가 겹침 검사), less_covered를 내 사진과 메모로 채워 차별점을 만듭니다.
   price_agreed_recent처럼 최근 6개월 리뷰 2곳 이상이 같은 값만 사실로 쓸 수 있습니다.
6) 사실은 폴더 이름, 사용자 메모, 사진에 보이는 글자, find_place 결과, place_reviews의 일치 값, load_research 노트, 웹 검색 결과만 씁니다.
   모르는 값은 [[확인필요: 항목]].
   맛 평가, 외관 후기, 분위기 감상, 느낀 점, 추천 여부 같은 주관 평가는 쓰지 않고 fill 블록(topic, 추천 문구 suggestions 2~3개)을 넣습니다.
   '[이 부분에 직접 입력하시기 바랍니다]' 아래에 '(추천 문구)' 줄이 붙어 들어갑니다(사용자 메모에 있는 평가만 text로 그대로).
   사람이 쓴 후기처럼 씁니다: text는 한 줄 한 호흡(15~28자)으로 \\n을 넣고 생각이 바뀌는 곳에 빈 줄, 사진 사이 글은 1~5줄,
   소제목은 '메뉴', '내부' 같은 짧은 이름표, 지도와 정보 줄은 도입 바로 다음. 자세한 규칙은 스타일 가이드 3~6절입니다.
   음식 사진에는 본문을 붙이지 않고 fill(메뉴 이름과 맛, 추천 문구에 정식 메뉴명)만 둡니다. 퀴즈, 비유 소제목은 쓰지 않습니다.
   제목: [분류] 상호명 시간대 위치 내돈내산 후기 (feat. 확인된 특징) 이모지. 음식 이름은 제목에 쓰지 않습니다.
   글 끝에는 quote 블록(말풍선, 포스트잇, 프레임)으로 추천 상황과 댓글, 공감, 이웃추가 같은 참여 문구를 넣습니다.
7) write_post(dry_run=true)로 lint_warnings(말투 경고 포함)와 overlap_warnings(최근 글, 같은 장소 리뷰와 겹침)를 고친 뒤 write_post로 임시저장합니다.
   main_keyword와 hook_pattern(도입 유형)은 항상 채웁니다.
8) 검증은 1회입니다. 불일치나 저장 미확인(saved_confirmed=false)이면 반복하지 말고 결과를 보여 준 뒤 사용자에게 묻습니다.
로그인이 필요하다는 오류가 나면 open_login_window를 호출하고, 사용자가 창에서 직접 로그인하도록 안내한 뒤 login_status로 확인합니다."""


def build_server() -> FastMCP:
    mcp = FastMCP("naver-blog-writer", instructions=INSTRUCTIONS)
    tools.register_all(mcp)
    prompts.register_all(mcp)
    return mcp


def _setup_logging() -> None:
    config.ensure_dirs()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    fh = logging.FileHandler(config.LOG_PATH, encoding="utf-8")
    fh.setFormatter(fmt)
    root.handlers = [sh, fh]


def main() -> None:
    _setup_logging()
    log.info("naver-blog-writer 시작 (blog_id=%s, media_roots=%s, daily_limit=%s)",
             config.BLOG_ID, config.MEDIA_ROOTS, config.DAILY_DRAFT_LIMIT)
    build_server().run()


if __name__ == "__main__":
    main()
