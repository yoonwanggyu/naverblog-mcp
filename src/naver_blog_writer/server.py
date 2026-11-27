"""네이버 블로그 작성 MCP 서버 (stdio). 생성과 등록만 합니다. 로직은 tools/와 core/에 있습니다.

주의: stdio MCP에서 stdout은 프로토콜 전용 채널입니다. print()를 쓰지 말고 logging(stderr)만 쓰세요.
"""

from __future__ import annotations

import logging
import sys

from mcp.server.fastmcp import FastMCP

from . import config, prompts, tools
from .core.writing_rules import COPY_RULES, GEMINI_HANDOFF, PRIORITIES

log = logging.getLogger("naver_blog_writer")

INSTRUCTIONS = f"""네이버 블로그 초안 작성 도구입니다. 결과물은 항상 임시저장이며, 발행은 사용자가 직접 합니다(코드에서 차단됨).
이 서버가 연결된 대화에서 사진, 장소, 날짜와 함께 들어오는 글 요청은 '블로그'라는 말이 없어도(…써줘, 정리해줘, 후기, 포스팅 등) 모두
네이버 블로그 초안 요청으로 보고, 항상 아래 순서와 스타일 가이드대로 작성합니다.
브라우저 작업(최근 글 읽기, 경향 분석, 장소 검색, 같은 장소 리뷰 읽기, 로그인, 글 입력)은 이 서버가 직접 화면에 보이는 크롬 창으로 합니다. 다른 브라우저 MCP를 쓰지 않습니다.

{PRIORITIES}

작업 순서
1) get_style_guide(topic=글 주제: 맛집, 카페, 데이트, 여행, 해외여행, 결혼준비, 취업준비): 매번 가장 먼저 호출합니다.
   내 블로그 최근 게시물 5개로 '내 스타일'(10절)을 갱신하고, 그 주제의 '최신 경향'(부록 B, 네이버 주제별 인기글)이 7일보다
    오래됐으면 다시 분석한 뒤 전체 가이드를 돌려줍니다. writing_ref는 이후의 스캔·미리보기·리서치·내 글 링크 호출에 guide_ref로 전달합니다.
2) list_folders(query=사용자가 말한 날짜와 장소 그대로). needs_confirmation=true면 confirm_question을 그대로 보여 주고
   '이게 맞습니까?'에 대한 답을 받은 뒤에만 scan_media(user_confirmed=true)로 진행합니다.
3) scan_media → view_images. 사진과 영상은 scan_media의 order(촬영 시각순, 두 사람이 올린 것을 섞은 순서)대로 글에 넣습니다.
   첫 대표 사진만 예외이고, 빼는 것은 되지만 순서는 바꾸지 않습니다. 영상은 video 블록으로 올립니다.
   사진 수를 스스로 줄이지 않습니다(서버와 네이버 모두 글당 장수 제한 없음). 흐리거나 거의 같은 사진, 개인정보 사진만 빼고 이유를 보고합니다.
   사진 배치(개별 image, gallery의 strip 나란히 2~3장·slide 슬라이드 4장 이상, 콜라주는 크기 조절이 안 돼 쓰지 않음)는 정해진 기본값 없이 작성자가 고릅니다.
   photo_groups(이름이 같고 번호만 다른 사진), post_kind(모음집 힌트), 내 글·같은 장소 리뷰·최신 경향의 photo_layout을 보고 가이드 4-6절 기준으로,
   dry_run의 layout_preview로 모바일 화면 순서를 확인해 가독성을 판단한 뒤 저장합니다. collection이면 가이드 5절 '모음집' 틀.
   핸드폰이 아닌 카메라(필름카메라, DSLR 등) 사진(camera_groups)은 본문 시간 흐름에 넣지 않고, 마무리 상자 바로 위에 종류별 구역으로 모읍니다: 소제목 '○○ 컷'(예: 필름카메라 컷, DSLR 컷) 아래 사진 묶음, 사진별 설명 없음.
   video_question이 있으면(영상 2개 이상) 그대로 보여 주고 사용자가 고른 영상만 user_selected=true로 넣습니다.
4) 장소 글이면 find_place(query=장소명, folder=폴더 경로)로 위치를 찾고, place_block을 place 블록으로 씁니다. confident=false면 사용자에게 확인합니다.
5) 장소가 정해지면 장소마다 반드시 place_reviews(place_id=find_place의 place_id)로 같은 장소의 최신 블로그 리뷰 10편을 분석합니다.
   읽지 않은 장소가 있으면 write_post가 저장을 거부합니다. place 블록에는 place_block을 그대로(place_id 포함) 씁니다.
6) {COPY_RULES}
   미확인 사실은 load_research나 웹 검색으로 확인합니다. 사용자가 내 다른 글 링크를 요청하면 my_post_links를 호출합니다.
7) {GEMINI_HANDOFF}
   어느 방식이든 write_post(dry_run=true)로 lint_warnings와 overlap_warnings를 점검·수정하고 최종본만 임시저장합니다.
   main_keyword와 hook_pattern(도입 유형)은 항상 채웁니다.
8) 실제 write_post는 작업 ID(job_id)를 즉시 반환하며 접수는 저장 성공이 아닙니다. draft_status(job_id, wait_seconds=10)로 확인하고
   running이면 같은 ID로 조회만 계속합니다. 접수 응답을 놓치면 draft_status()로 최근 작업의 제목과 상태부터 확인합니다.
   saved일 때만 임시저장을 보고합니다. 검증은 1회입니다. 검증 불일치, unconfirmed, failed, unknown이면 재전송하지 말고 결과를 알립니다.
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
