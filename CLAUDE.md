# naverblog-mcp

Google Drive for desktop이 동기화한 로컬 사진 폴더에서 Claude가 글 폴더를 찾아 사진과 영상을 보고 글을 쓴 뒤, 네이버 블로그에 **임시저장**하는 로컬 MCP 서버.
Claude Desktop에 stdio 서버로 등록해서 쓴다. 사용자는 경로가 아니라 "11월 15일 성수 데이트 글 써줘"처럼 말한다.

## 목적과 방향성
- 블로그 주제는 "커플 데이트의 모든 것"(맛집, 카페, 여행, 결혼 준비, 취업 준비)이다. 주제별 고정 입력 필드를 만들지 않는다.
- 제품 목표: 블로그 방문자 수를 늘리고 광고 수익(체험단, 협찬, 애드포스트)을 얻는 것. 사람이 직접 쓴 후기처럼 읽히는 글이 우선이다.
  글 형식의 기준은 네이버 맛집, 여행 상위 블로그 분석(`style/default.md` 부록 A)이다: 한 줄 한 호흡, 가운데 정렬, 짧은 이름표 소제목(질문형은 1개까지, '자주 묻는 질문' 없음), 지도와 정보 줄은 글 앞쪽, 제목은 `[분류] 상호명 시간대 위치 내돈내산 후기 (feat. 특징) 이모지`(음식 이름 없음), 끝은 마무리 상자.
- 품질 기준 두 가지: 도구를 정확한 순서로 호출할 것. 스타일 가이드(`style/default.md`)대로 글과 사진을 배치할 것.
- 판단 기준은 `default.md` 0절: 평소에는 모든 자료(요청, 11절 개인 규칙, 10절 내 글 5개, 같은 장소 리뷰, 부록 B 경향, 기본 가이드)를 종합하고, 부딪칠 때만 바꿀 수 없는 규칙 > 요청 > 11절 > 10절 > 같은 장소 리뷰 > 부록 B > 기본 가이드 순. 이 순서를 바꾸면 `default.md` 0절, `server.py` INSTRUCTIONS, `prompts/write.py`를 함께 고친다.
- 배포 목표: GitHub에서 clone한 다른 사람의 macOS나 Windows에서도 README 절차만으로 동작해야 한다.
- 역할 분리: 서버는 이미지를 인식하지 않는다. 메타데이터를 읽고, 축소 이미지를 Claude에게 보여 주고, 지정된 파일을 업로드할 뿐이다.
- 사용자는 화면에 뜬 크롬 창으로 최근 글 읽기, 경향 분석, 장소 검색, 같은 장소 리뷰 읽기, 로그인, 글 입력을 지켜본다. 브라우저는 이 서버가 Playwright로 직접 띄운다(별도 Playwright MCP를 쓰지 않는다).
- 사진, 장소, 날짜와 함께 들어오는 글 요청은 '블로그'라는 말이 없어도 네이버 블로그 초안 요청이며 항상 스타일 가이드대로 쓴다.

## 사진 폴더 규칙
- 루트는 `MEDIA_ROOTS`(예: `~/Library/CloudStorage/GoogleDrive-<이메일>/내 드라이브/블로그사진`).
- 글 1편 = 폴더 1개. 이름에 장소명과 날짜가 있으면 형식은 자유다(`젠스시(26.11.14)`, `2026-11-15_성수데이트`, `놀숲 11.17` 등). 날짜가 없으면 첫 사진의 EXIF 촬영일을 쓴다.
- 하루 여러 장소는 하위 폴더 `1_장소`, `2_장소`. 하위 폴더는 상위 폴더의 날짜를 물려받는다.
- 폴더 이름의 날짜와 장소명, `find_place` 결과(주소, 업종, 전화)는 사실로 취급한다. 그 밖의 사실은 메모, 리서치 노트, 웹 검색 확인값만 쓴다.
- 날짜 해석은 `core/dates.py`(폴더 이름과 사용자 입력 공통), 폴더 검색과 오타 비교와 확인 질문은 `core/folders.py`. 정확히 하나만 일치할 때만 바로 진행하고, 그 밖에는 "이게 맞습니까?" 확인 전에 `scan_media`가 읽지 않는다.

## 절대 규칙
- **발행 기능을 만들지 않는다.** 발행 버튼은 `core/browser.py`의 가드 스크립트(캡처 단계 차단)와 `core/editor.py`의 `safe_click()` 두 겹으로 막는다. 가드 설치에 실패하면 작업을 중단한다. 이 가드를 제거하거나 우회하지 않는다.
- 네이버 비밀번호를 받거나 저장하는 코드를 만들지 않는다. 로그인은 사용자가 브라우저 창에서 직접 하고, 영구 프로필 쿠키만 재사용한다.
- 파일 접근은 `media.check_allowed()`를 반드시 통과시킨다. `MEDIA_ROOTS`와 캐시 폴더 밖은 읽지도 올리지도 않는다.
- 하루 임시저장 한도(`DAILY_DRAFT_LIMIT`, 기본 2)를 도구 인자로 우회할 수 없게 둔다.
- 고정 좌표 클릭과 추측성 대체 동작을 넣지 않는다. 요소 기준 클릭은 결과(커서 위치)를 확인할 때만 쓴다.
- 검증은 텍스트 전문 대조 1회다. 스크린샷 검증과 자동 재시도를 넣지 않는다. 실패 스크린샷은 디스크에만 저장한다(`RETURN_ERROR_SCREENSHOT=true`일 때만 반환).
- 블로그 읽기는 세 가지뿐이다. 모두 공개 글만, 화면에 보이는 브라우저로 천천히 읽는다.
  1. 내 블로그(`NAVER_BLOG_ID`) 최근 게시물 최대 5개, 글쓰기마다(`core/blog_reader.py`). `NBW_HOME/my_style.md`(10절)를 새로 쓰고 겹침 비교 대상으로 쓴다.
  2. 같은 장소의 최신 블로그 리뷰 최대 `PLACE_REVIEWS_LIMIT`(10)편, 장소 글의 장소마다 반드시(`write_post`가 24시간 안의 읽은 기록이나 실패 기록이 없으면 거부)(`core/place_reviews.py`, 플레이스 '블로그 리뷰' 최신순). Claude에게는 수치, 제목, 소제목, 60자 이하 정보 줄만 보낸다. 본문 줄은 `NBW_HOME/place_reviews/`에만 두고 24시간 동안 겹침 검사에만 쓴다.
  3. 네이버 주제별 인기글과 최신글 최대 `TREND_POSTS`(15)편, `TREND_MAX_AGE_DAYS`(7)일마다(`core/trends.py`). 형식 수치만 `NBW_HOME/trends/`에 저장하고 본문은 저장하지 않는다. 가이드의 '부록 B. 최신 경향'이 된다.
  2와 3은 `READ_OTHER_BLOGS=false`면 읽지 않고 장소 리뷰 필수 확인도 끈다. 같은 장소 리뷰 파일은 24시간 뒤 `purge_expired()`가 지우고, 경향 파일에는 글 주소를 저장하지 않는다.
  다른 사람 글의 문장, 제목, 소제목을 글에 쓰지 않는다(`archive.overlap_warnings`가 같은 장소 리뷰와 거의 같은 문장을 한 문장부터 경고).
- `write_post`는 최근 게시물을 읽은 지 `STYLE_SYNC_MAX_AGE_MIN`분이 지났으면 거부한다. 이 조건을 도구 인자로 우회할 수 없게 둔다.
- 사진은 `scan_media`의 `order`(촬영 시각순: EXIF 원본 시각과 시간대 → 파일 이름 날짜 → 파일 수정 시각, `media.capture_time()`)대로 넣는다. 두 사람이 같은 Drive 폴더에 올리므로 파일 이름순을 쓰지 않는다. 순서가 다르면 `write_post`가 경고만 한다(대표 사진, 영상 장면 제외).
- 내 블로그 다른 글 링크(`link` 블록, `my_post_links`)는 사용자가 요청할 때만 마무리 상자 뒤에 넣는다. 내 블로그 주소만 허용한다(`Block` 검증).
- 사진 속 음식의 이름과 설명도 본문에 쓰지 않고 fill로 넣는다(추천 문구에 정식 메뉴명). 제목에 음식 이름을 넣지 않는다. 글 끝은 `quote` 블록(말풍선, 포스트잇, 프레임)이다.
- 주관 평가(맛, 외관 후기, 분위기, 느낀 점, 추천)는 쓰지 않고 `fill` 블록으로 `[이 부분에 직접 입력하시기 바랍니다]`(`formatting.PLACEHOLDER`)와 그 아래 `(추천 문구)` 줄 2~3개를 넣는다. 추천 문구는 사용자가 고르는 예시이고 사실로 쓰지 않는다. 사용자 메모의 평가만 `text`로 그대로 쓴다.
- 저장은 완료 신호(알림, 저장 글 수, 저장 응답)를 확인한 뒤에만 성공으로 보고한다. 미확인 저장도 하루 한도와 중복 방지에 센다.
- 실계정 테스트는 임시저장까지만, 하루 1~2건만 한다. `~/.naver-blog-writer/browser-profile`은 읽거나 커밋하지 않는다.

## 기술 스택
- Python 3.10 이상, uv (`uv.lock` 커밋 유지)
- MCP Python SDK **1.x의 FastMCP** (`mcp[cli]>=1.25,<2`). 2.x는 API가 달라 올리지 않는다.
- Playwright async API(Chrome 채널 권장), Pillow와 pillow-heif, imageio-ffmpeg
- 외부: Google Chrome, Google Drive for desktop, Claude Desktop. Google Drive API 연동은 넣지 않는다.

## 명령어
```bash
uv sync                                                    # .venv 생성 (uv.lock 기준)
BROWSER_CHANNEL=chrome uv run python tests/mock_editor_test.py   # 가짜 에디터 회귀 테스트 45항목 (네이버 접속 없음)
uv run python tests/folders_test.py                        # 폴더 찾기(날짜 형식, 오타, 확인 질문), 두 사람 사진 촬영 시각 정렬 24항목
BROWSER_CHANNEL=chrome uv run python tests/place_blog_test.py    # 장소, 내 스타일, 같은 장소 리뷰, 최신 경향, 다른 블로그 끄기 28항목
uv run python tests/archive_test.py                        # 겹침 경고 10항목
uv run python tests/formatting_test.py                     # 줄 정리, 추천 문구, 말투와 제목 형식, 내 글 링크, 개인 규칙 파일 점검 36항목
uvx ruff check src tests
uv run naver-blog-writer                                   # 서버 직접 실행 (보통은 Claude Desktop이 실행)
uv run mcp dev src/naver_blog_writer/server.py             # MCP Inspector (Node 필요)
uv run naver-blog-login                                    # 터미널 로그인 도우미
```
코드를 바꾼 뒤에는 ruff와 다섯 테스트를 통과시킨 다음 보고한다.

## 폴더 구조
```
style/default.md          # 글쓰기 스타일 가이드 (get_style_guide가 읽음)
style/style_guide.md      # 개인 고정 규칙 틀(11절). STYLE_GUIDE_PATH로 연결, 주석은 빼고 읽음, 비면 붙지 않음
src/naver_blog_writer/
├── server.py             # FastMCP 생성, INSTRUCTIONS, 등록, main(). 로직 없음
├── config.py             # 환경 변수와 경로 (모두 절대경로)
├── login_cli.py          # 터미널 로그인 도우미 (print 허용)
├── tools/                # MCP 도구. 파일 1개 = 도구 1개, 파일명 = 함수명 = 도구명
│   ├── __init__.py       # TOOLS 목록(노출 순서)과 register_all()
│   ├── get_style_guide.py  recent_posts.py  list_folders.py  scan_media.py  view_images.py
│   ├── extract_video_frames.py  find_place.py  place_reviews.py  load_research.py  save_research.py
│   ├── write_post.py     # Block 모델, 정리, lint, 한도, 입력, 검증, 임시저장
│   └── my_post_links.py  refresh_trends.py  check_setup.py  login_status.py  open_login_window.py  inspect_editor.py
├── prompts/              # MCP 프롬프트(+ 메뉴의 /write, /research). 파일명 = 프롬프트명
│   ├── write.py          # 입력 모두 선택: 폴더 또는 주제, 제목, 큰 틀, 메모
│   └── research.py       # 최근 N개월, 출처 5개 이상, 광고 제외, 충돌 병기
└── core/                 # 공유 로직. MCP를 import하지 않는다
    ├── media.py  browser.py  editor.py  selectors.py
    ├── dates.py          # 날짜 표현 해석 (폴더 이름, 사용자 입력)
    ├── folders.py        # 폴더 검색, 오타 비교, "이게 맞습니까?" 확인 상태
    ├── place.py          # 네이버 지도 장소 검색
    ├── blog_reader.py    # 내 블로그 최근 게시물 읽기, my_style.md 생성
    ├── post_analysis.py  # 다른 사람 글 하나의 형식 분석과 집계 (place_reviews, trends 공용)
    ├── place_reviews.py  # 같은 장소 최신 리뷰 목록, 요약, 겹침 검사용 캐시
    ├── trends.py         # 주제별 인기글 최신 경향 (7일 캐시, 부록 B)
    ├── formatting.py     # 한 줄 한 호흡 정리, fill 렌더링, lint(말투 점검 포함)
    ├── archive.py        # 최근 글 5개 기록과 겹침 경고 (블로그 사이트를 읽지 않음)
    ├── style.py  notes.py  history.py
tests/mock_editor/         # 가짜 SmartEditor (정렬, 굵게, 인용구 모양과 여러 줄, 구분선, 장소, 링크 카드, 캡션, 늦게 뜨는 '작성 중인 글' 창, 이모지 손실, 발행 버튼)
```

## 도구 작성 규칙
- 새 도구는 `tools/<도구명>.py`에 만들고 `ANNOTATIONS = ToolAnnotations(...)`를 정의한 뒤 `tools/__init__.py`의 `TOOLS`에 추가한다.
- docstring이 Claude가 보는 도구 설명이다. 한국어로 언제 쓰는지와 주의점을 2~5줄로 쓴다.
- 입력 검증 실패는 예외로 던진다. 브라우저 작업 실패는 `FAILED ...` 텍스트로 반환한다.
- 이미지는 `mcp.server.fastmcp.Image`로 반환하고 한 번에 최대 10장, 긴 변 1024px 기본.
- 작업 순서(스타일 가이드와 최근 글과 최신 경향, 폴더 찾기와 확인, 스캔, 보기, 장소 찾기, 같은 장소 리뷰, 요청 시 내 글 링크, dry_run, 임시저장)는 `server.py`의 `INSTRUCTIONS`와 `prompts/write.py`에 있다. 바꾸면 둘 다 고친다.
- 파일, 이미지, ffmpeg처럼 오래 걸리는 동기 작업은 `asyncio.to_thread`로 돌린다. 오래 걸리는 도구는 `ctx.report_progress`로 진행 상황을 보낸다.
- 사용자를 기다리는 도구(로그인)는 호출을 붙잡지 않는다. 창을 띄우고 바로 돌아온 뒤 상태 도구로 확인한다.

## 코딩 규칙
- stdio MCP라서 stdout은 프로토콜 전용이다. `print()` 금지(`login_cli.py` 제외). 로그는 stderr와 `~/.naver-blog-writer/server.log`.
- 브라우저는 `core.browser.browser_session()`으로만 연다. 도구 호출마다 열고 닫고, 잠금으로 동시 실행을 막는다.
- 셀렉터는 `core/selectors.py`에만 둔다. 해시 클래스는 `[class*='publish_btn']`처럼 앞부분만 매칭하고, 툴바 버튼은 `*_NAMES`(버튼 글자) 대체 수단을 같이 둔다.
- 클릭은 항상 `editor.safe_click()`을 쓴다.
- 플랫폼 차이는 코드에서 분기한다(macOS 줄 끝 이동 `Meta+ArrowRight`, 그 외 `End`). 한글 경로는 비교 전에 NFC로 정규화한다.
- 사용자에게 보이는 메시지는 한국어, 식별자와 로그 키는 영어. 새 의존성은 `uv add`로만 추가한다.

## SmartEditor ONE 함정
- 에디터는 `iframe#mainFrame` 안에 있다. `find_editor_frame()`으로 찾는다.
- "작성 중인 글이 있습니다(이어서 작성하시겠습니까?)" 창은 저장하지 않고 편집하다 만 내용이 있으면 에디터가 보이고 1~3초 뒤에 늦게 뜨고, 화면 전체를 덮어 제목 칸과 툴바 클릭을 막는다. `open_editor()`의 `settle_draft_popup()`이 4초 동안 지켜보다 '취소'를 눌러 항상 새 글로 시작한다('저장' 목록의 임시저장 글은 지워지지 않음). `click_paragraph_end()`와 `_click_toolbar()`도 막히면 이 창을 닫는다.
- "작성 중인 글이 있습니다" 팝업은 글쓰기 중간에도 뜬다. `dismiss_popups()`를 블록 전후와 저장 직전에 호출한다. Playwright `add_locator_handler`는 쓰지 않는다(팝업이 사라진 뒤 30초 대기가 생김). 이 팝업의 '취소'만 문구로 골라 누른다. 범용 `.se-popup-button-cancel`은 사진 업로드 진행 팝업의 취소를 눌러 업로드를 끊으므로 쓰지 않는다.
- 사진은 블록이 먼저 생기고 업로드가 나중에 끝난다. `wait_upload_done()`이 이미지 주소가 http(s)로 바뀌고 로딩 표시가 사라질 때까지 기다린다.
- 글쓰기 주소는 로그인이 만료되면 몇 초 뒤 자바스크립트로 nid.naver.com으로 넘어간다. 공개 블로그 주소는 만료돼도 열리므로 로그인 확인에 쓰지 않는다.
- 사진은 사진 버튼과 `page.expect_file_chooser()`로 넣고, 대체 경로는 `input#hidden-file`.
- 블록(사진, 인용구, 구분선, 지도) 안에서 아래 문단으로 빠져나가는 키 조작은 없다. 인용구 안의 Enter는 같은 인용구 안에 새 줄을 만든다(소제목은 한 줄로만 넣고, 마무리 상자 `insert_quote()`는 이것으로 여러 줄을 넣는다). 실행 취소도 엉뚱한 글자를 지운다. 그래서 블록을 넣기 전에 `prepare_block_slot()`(마지막 문단 클릭 → Enter → ArrowUp)으로 아래에 빈 문단을 남겨 두면 텍스트 블록이 나뉘어 블록 아래에 문단이 생긴다. `move_to_end()`는 그 문단을 클릭할 뿐 블록 안에서 Enter를 누르지 않는다.
- 브라우저의 선택 영역(getSelection)은 에디터 내부 커서와 다르다. 글을 입력한 직후 키보드 위치를 믿지 말고 블록을 넣기 전에 마지막 문단을 직접 클릭한다. 위치 확인은 결과(블록 배치 순서, `verify_text`의 layout)로 한다.
- 이모지(4바이트 문자)가 섞인 글자를 한 번에 넣으면 이모지만 남는다. `type_text()`가 이모지 앞뒤로 나눠 입력한다.
- 변형 선택자가 붙은 2바이트 기호(✔️ ❤️ ☀️의 U+FE0F)는 따로 넣어도 그다음 입력과 Enter가 사라진다. `editor_text()`가 입력 전에 선택자만 지운다(✔로 들어감). 검증 `_norm`도 선택자를 무시한다.
- 정렬: '정렬 열기'(`data-name='align-drop-down-with-justify'`) → `se-toolbar-option-align-center-button`. 정렬은 Enter로 만든 새 문단과 블록 아래로 나뉜 문단에 이어진다. 커서가 제목에서 본문으로 넘어갈 때 툴바가 다시 그려지므로(제목용 `align-drop-down-without-right`) 열린 메뉴와 같은 `data-name`의 항목만 누른다(`set_align()`).
- 가운데 정렬 문단의 한가운데를 누르면 `.se-selection` 레이어가, 사진을 넣은 직후에는 오른쪽 사이드바가 클릭을 가로챈다. `click_paragraph_end()`가 문단의 오른쪽 끝, 왼쪽 끝, 가운데를 시험 클릭(trial)해 누를 수 있는 자리만 누른다. 제목과 본문 첫 줄 클릭도 같은 함수로 하며, 막히면 '작성 중인 글' 창을 닫고 기다린다.
- 인용구 모양: '인용구 선택'(`se-text-icon-toolbar-select-option-button[data-name='quotation']`) → `se-toolbar-option-insert-quotation-{style}-button`. 소제목 기본은 `quotation_line`.
- 입력을 너무 빠르게 넣으면 에디터가 잠깐 바쁜 순간(굵게 전환 직후 등)에 들어온 줄과 Enter를 통째로 버린다(24줄 중 1~15줄 손실). `write_paragraphs()`가 줄마다 `LINE_PACE_S`(0.12초), 굵게 전환마다 `BOLD_SETTLE_S`(0.15초) 쉰다. 이 간격을 줄이지 않는다.
- 굵게는 Cmd/Ctrl+B로 켜고 끈다. 툴바 굵게 버튼의 `se-is-selected`는 줄을 바꾼 직후 이전 상태로 남아 있으므로 상태 판단에 쓰지 않는다.
- 사진 설명칸은 사진을 선택해야 보인다.
- 링크 카드: 툴바 '링크'(`data-name='oglink'`)는 눌러도 입력 창이 뜨지 않는다. 본문의 빈 줄에 주소를 입력하고 Enter를 누르면 에디터가 미리보기 카드(`se-oglink`)를 만들고 카드 아래 빈 문단으로 커서를 옮긴다(`insert_link()`). 주소 줄은 카드 위에 링크로 남는다. 붙여넣기 이벤트를 흉내 내면 무시된다.
- 장소 팝업: Enter로는 검색되지 않고 '검색' 버튼을 누른다. '추가'는 결과 항목에 마우스를 올려야 보인다. '추가' 적용(`se-is-attached`) 후 '확인'을 누른다. Esc로 닫히지 않는다('팝업 닫기'). 화면 밖의 '글감 검색' 입력칸과 '추가' 버튼이 많으므로 반드시 팝업 안에서만 찾는다.
- 장소 '검색' 클릭이 가끔 무시된다(결과 영역에 '검색 전' 안내문이 그대로 남음). `_search_place()`는 '검색 전' 상태면 다시 입력하고 다시 검색하며, '검색 전'이 사라졌는데 결과가 없을 때만 0건으로 본다. 0건이면 `insert_place()`가 상호명만(마지막 단어 제외)으로 다시 검색하고 주소로 지점을 고른다.
- 장소 첫 첨부는 지도 이미지 생성 요청(staticmap)이 실패해 '확인'이 먹지 않는 경우가 많다. `insert_place()`가 최대 3번 다시 시도한다. 숨김 창(HEADLESS=true)에서는 지도 이미지를 못 만들어 첨부가 실패한다.
- 저장 글 수는 `save_count_btn`(저장 확인 신호로 사용).
- 태그는 본문 끝 `#태그`로 넣는다. 카테고리와 정식 태그는 발행 레이어에 있어 사용자가 발행할 때 고른다.
- 실제 화면과 셀렉터가 어긋나면 `inspect_editor` 결과를 보고 `selectors.py`를 고치고, mock HTML도 같이 고친다.

## 참고 문서
- 설치, 환경 변수, Claude Desktop 설정, 문제 해결: `README.md`
- 개인 값(블로그 ID, Drive 경로)은 레포에 넣지 않는다. 로컬 실행 값은 `.env`(gitignore 대상, 형식은 README '설정하기'), 메모는 `CLAUDE.local.md`.
- 설정은 환경 변수(Claude Desktop의 env)가 `.env`보다 우선한다. 테스트는 `NBW_NO_DOTENV=1`로 `.env`를 읽지 않는다.
