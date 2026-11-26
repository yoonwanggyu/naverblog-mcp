"""네이버 SmartEditor ONE 셀렉터 모음.

네이버가 화면을 바꾸면 이 파일만 고치면 됩니다.
- 각 항목은 앞에서부터 차례로 시도합니다.
- 해시가 붙는 클래스(예: publish_btn__Y5mLP)는 [class*="publish_btn"]처럼 앞부분만 매칭합니다.
- *_NAMES 항목은 버튼의 접근성 이름(화면 글자)으로 찾는 대체 수단입니다. 클래스가 바뀌어도 대개 유지됩니다.
"""

# 글쓰기 페이지 주소. {blog_id} 자리에 블로그 ID가 들어갑니다.
WRITE_URLS = [
    "https://blog.naver.com/{blog_id}?Redirect=Write&",
    "https://blog.naver.com/{blog_id}/postwrite",
]

# 에디터가 로드됐는지 판단하는 기준(제목 영역)
EDITOR_READY = [".se-documentTitle", ".se-section-documentTitle", ".se-title-text"]

# 팝업. "작성 중인 글이 있습니다" 팝업은 글쓰기 중간에도 뜰 수 있어 블록마다 확인합니다.
# 반드시 '작성 중인' 문구가 있는 팝업의 취소만 누릅니다. 범용 '.se-popup-button-cancel'은 쓰지 않습니다
# (사진 업로드 진행 팝업의 취소를 눌러 업로드를 끊는 사고를 막기 위해서).
POPUP_DRAFT_CANCEL = [
    ".se-popup-alert-confirm:has-text('작성 중인') .se-popup-button-cancel",
    ".se-popup:has-text('작성 중인') .se-popup-button-cancel",
]
POPUP_HELP_CLOSE = [".se-help-panel-close-button", "button.btn_close._btn_close"]

# 제목, 본문
TITLE_PARAGRAPH = [
    ".se-documentTitle .se-text-paragraph",
    ".se-section-documentTitle .se-text-paragraph",
    ".se-placeholder.se-fs32",
]
BODY_FIRST_PARAGRAPH = [
    ".se-component.se-text .se-text-paragraph",
    ".se-placeholder.se-fs15",
]

# 문서 안의 컴포넌트
COMPONENT = ".se-component"
TEXT_COMPONENT_CLASS = "se-text"
TEXT_PARAGRAPH = ".se-text-paragraph"
IMAGE_COMPONENT = ".se-component.se-image, .se-component.se-imageStrip, .se-component.se-imageGroup"
QUOTE_COMPONENT = ".se-component.se-quotation"
DIVIDER_COMPONENT = ".se-component.se-horizontalLine"
PLACE_COMPONENT = ".se-component.se-placesMap, .se-component.se-map"
# 동영상: '동영상' 버튼 → '동영상 업로더' 창 → '동영상 추가'(파일 선택) → '업로드 완료' 문구 → 제목(필수, 40자) → '완료'
# (실제 에디터에서 확인, 5초 영상 업로드 약 11초). 창 안의 요소는 모두 VIDEO_POPUP 안에서만 찾습니다.
VIDEO_COMPONENT = ".se-component.se-video"
VIDEO_BUTTON = ["button[data-name='video']", "button.se-video-toolbar-button"]
VIDEO_BUTTON_NAMES = ["동영상"]
VIDEO_POPUP = [".se-popup-video-upload"]
VIDEO_LOCAL_BUTTON = ["button.nvu_local", "button.nvu_btn_local"]
VIDEO_TITLE = ["#nvu_inp_box_title", "input.nvu_inp"]
VIDEO_SUBMIT = ["button.nvu_btn_submit"]
VIDEO_CLOSE = ["button.nvu_btn_close"]
VIDEO_DONE_TEXT = "업로드 완료"
VIDEO_FAIL_TEXT = r"업로드 실패|실패했습니다|지원하지 않는"
# 링크 미리보기 카드. 툴바 '링크' 버튼은 눌러도 창이 뜨지 않고, 본문 줄에 주소를 입력하고 Enter를 누르면 카드가 생깁니다(실제 에디터에서 확인).
OGLINK_COMPONENT = ".se-component.se-oglink"

# 툴바 버튼 (클래스 우선, 실패하면 이름으로)
IMAGE_BUTTON = ["button.se-image-toolbar-button", "button[data-name='image']"]
IMAGE_BUTTON_NAMES = ["사진"]
QUOTE_BUTTON = ["button.se-insert-quotation-default-toolbar-button", "button[data-name='quotation']"]
QUOTE_BUTTON_NAMES = ["인용구"]
# 인용구 모양 고르기: 인용구 버튼 옆 화살표를 누르면 모양 6개(default, quotation_line, quotation_bubble,
# quotation_underline, quotation_postit, quotation_corner)가 나옵니다. {style} 자리에 모양 이름이 들어갑니다.
QUOTE_STYLE_DROPDOWN = ["button.se-text-icon-toolbar-select-option-button[data-name='quotation']"]
QUOTE_STYLE_OPTION = ["button.se-toolbar-option-insert-quotation-{style}-button", "button[data-name='quotation'][data-value='{style}']"]
# 문단 정렬: '정렬 열기'를 누르면 left, center, right, justify 네 가지가 나옵니다.
ALIGN_DROPDOWN = ["button[data-name='align-drop-down-with-justify']", "button[data-name^='align-drop-down']",
                  "button[class*='se-align-'][class*='toolbar-button']"]
ALIGN_OPTION = ["button.se-toolbar-option-align-{align}-button"]
DIVIDER_BUTTON = ["button.se-insert-horizontal-line-default-toolbar-button", "button[data-name='horizontal-line']"]
DIVIDER_BUTTON_NAMES = ["구분선"]
PLACE_BUTTON = ["button.se-map-toolbar-button", "button[data-name='map']"]
PLACE_BUTTON_NAMES = ["장소"]
FILE_INPUT = ["input#hidden-file", "input[type='file'][accept*='image']", "input[type='file']"]
# 사진 여러 장을 한 번에 고르면 '사진 첨부 방식' 창이 뜹니다: 개별사진(list), 콜라주(collage), 슬라이드(slide).
# 콜라주와 슬라이드는 그룹사진 블록(se-imageGroup se-l-collage / se-l-slide) 하나가 되고 최대 10장입니다
# (11장 이상이면 두 선택지가 disabled, 안내문 '그룹사진은 최대 10장의 사진으로 만들 수 있습니다'). 한 번에 고를 수 있는 파일은 50개이고
# 넘는 파일은 '첨부 개수 오류'로 문서에서 지워집니다. 글 전체로는 나눠 넣으면 93장까지 들어갔습니다(2026-11-26 실제 에디터).
IMAGE_GROUP_COMPONENT = ".se-component.se-imageGroup"
IMAGE_TYPE_POPUP = [".se-popup-image-type"]
IMAGE_TYPE_INPUT = "#image-type-{layout}"          # input type=button. disabled면 그 방식은 고를 수 없음
IMAGE_TYPE_LABEL = "label[for='image-type-{layout}']"
# 나란히: 개별 사진을 다른 사진의 오른쪽 가장자리로 끌어다 놓으면 한 줄(se-imageStrip, 2~3장)로 합쳐집니다. 업로드 창의
# 선택지가 아닙니다. 끄는 동안 놓을 자리에 오른쪽 표시(se-drop-indicator-right, 화면의 초록 세로선)가 뜹니다(2026-11-26 실제 에디터).
STRIP_COMPONENT = ".se-component.se-imageStrip"
SINGLE_IMAGE_COMPONENT = ".se-component.se-image"
DROP_RIGHT = ".se-drop-indicator-right"
# 업로드가 거부되면 '파일 전송 오류' 창이 뜹니다(예: '첨부 개수 오류', '일시적으로 파일전송을 사용할 수 없습니다. 잠시 후 다시 시도해주세요.').
UPLOAD_ERROR_TEXT = "파일 전송 오류"

# 사진 업로드 진행 표시. 이것이 보이는 동안은 업로드가 끝나지 않은 것으로 봅니다.
IMAGE_UPLOADING = [".se-image-loading", "[class*='loading']", "[class*='progress']", "[class*='uploading']"]  # 사진 블록 안
UPLOAD_PROGRESS = [".se-popup-progress", ".se-popup:has-text('올리는 중')", ".se-popup:has-text('업로드 중')", "[class*='upload'][class*='progress']"]
# '.se-popup-progress': '업로드 준비 중입니다. N개 파일 0/N', '파일이름 크기 2/N'을 보여 주는 진행 창(실제 에디터에서 확인)

# 인용구 안 본문, 사진 설명
QUOTE_TEXT = [".se-quote .se-text-paragraph", ".se-quotation-container .se-text-paragraph", ".se-text-paragraph"]
IMAGE_CAPTION = [".se-caption .se-text-paragraph", ".se-module-text.se-caption", "[class*='caption'] .se-text-paragraph"]

# 장소 검색 팝업 (실제 에디터에서 확인한 구조). 아래 셀렉터는 모두 PLACE_POPUP 안에서만 찾습니다.
# 화면에는 '글감 검색' 입력칸과 '추가' 버튼이 여러 개 있으므로 팝업 밖을 찾으면 엉뚱한 요소를 누릅니다.
PLACE_POPUP = [".se-popup-placesMap"]
PLACE_SEARCH_INPUT = ["input.react-autosuggest__input", "input[placeholder*='장소']"]
PLACE_SEARCH_BUTTON = [".se-place-search-button"]
PLACE_RESULT_ITEM = [".se-place-map-search-result-item"]
PLACE_BEFORE_SEARCH = [".se-place-map-search-info-description-before-search"]   # 아직 검색이 실행되지 않은 상태의 안내문
PLACE_RESULT_ADD = [".se-place-add-button"]                 # 결과 항목 안의 '추가'
PLACE_ATTACHED = [".se-place-attached-delete-button"]          # '추가'가 적용되면 생기는 '장소 삭제' 버튼
PLACE_CONFIRM = [".se-popup-button-confirm"]
PLACE_LAYER_CLOSE = [".se-popup-close-button"]               # Esc로는 닫히지 않음

# 상단 버튼
SAVE_BUTTON = ["button[class*='save_btn']"]          # "저장" (임시저장)
SAVE_BUTTON_NAMES = ["저장"]
# 저장 완료 신호 (하나라도 확인되면 저장 성공으로 봅니다)
SAVE_COUNT = ["button[class*='save_count']", "[class*='save_btn'] [class*='count']"]   # 저장 글 개수 숫자
SAVE_TOAST = [".se-toast-popup", "[class*='toast']", "[class*='Toast']", "[role='alert']", "[role='status']"]
SAVE_DONE_TEXT = r"(임시\s*)?저장(이)?\s*(되었|됐|완료)"
SAVE_RESPONSE_URL = r"(RabbitWrite|TempSave|tempSave|SaveDraft|saveDraft|temporary|draft)"

# 발행 차단 대상. 자동화 브라우저에서는 이 요소들의 클릭을 페이지 수준에서 막습니다.
PUBLISH_BLOCKLIST = [
    "[data-testid='seOnePublishBtn']",
    "button[class*='publish_btn']",
    "button[class*='confirm_btn']",
]
