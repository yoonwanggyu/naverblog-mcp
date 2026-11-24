"""/write: 사진 폴더 또는 주제로 블로그 초안을 만들어 임시저장합니다. 모든 입력은 선택입니다."""

from ..core.writing_rules import COPY_RULES, GEMINI_HANDOFF, PRIORITIES

TITLE = "블로그 글쓰기"


def write(folder_or_topic: str = "", title: str = "", outline: str = "", memo: str = "") -> str:
    """사진 폴더(장소, 날짜) 또는 주제로 글을 써서 임시저장합니다. 제목, 큰 틀(소주제 순서), 소재와 한 줄 메모는 선택 입력입니다."""
    target = folder_or_topic.strip() or "(미지정) list_folders를 query 없이 호출해 최근 폴더 5개를 보여 주고 고르게 할 것"
    t = title.strip() or "(미지정) 스타일 가이드의 제목 규칙으로 정할 것"
    o = outline.strip() or "(미지정) 사진의 시간 흐름과 스타일 가이드의 구성 규칙으로 정할 것"
    m = memo.strip() or "(없음)"
    return f"""네이버 블로그 초안을 작성해서 임시저장해 줘. 채워진 입력값은 그대로 따르고, 미지정 항목은 네가 정한다.

[입력값]
- 대상 폴더 또는 주제: {target}
- 제목: {t}
- 큰 틀(다룰 소주제 순서): {o}
- 소재와 한 줄 메모: {m}

{PRIORITIES}

[진행 순서]
1. get_style_guide(topic=글 주제)를 호출한다. 내 블로그 최근 게시물(최대 5개)로 갱신된 '내 스타일'과 주제별 '최신 경향'(7일마다 갱신)을 따르고, 그 글들과 제목, 메인 키워드, 도입 유형, 도입과 마무리 문장, 소제목 표현이 겹치지 않게 계획한다.
2. 사진 글이면 list_folders(query=대상)로 폴더를 찾는다. needs_confirmation=true면 confirm_question을 그대로 보여 주고 "이게 맞습니까?"에 대한 내 답을 기다린다. 맞다고 하면 scan_media(user_confirmed=true), 그다음 view_images로 사진을 확인한다. 영상은 extract_video_frames로 본다. 스캔·미리보기·리서치·내 글 링크에는 이번 가이드의 writing_ref를 guide_ref로 준다.
3. 장소 글이면 find_place(query=장소명, folder=폴더 경로)로 위치를 찾는다. place_block은 place 블록으로(도입 바로 다음), checklist_line은 지도 아래 정보 줄(📍)에 쓴다. confident=false면 후보를 보여 주고 나에게 묻는다.
   위치가 정해지면 장소마다 반드시 place_reviews(place_id=...)로 같은 장소의 최신 리뷰 10편을 분석한다(안 하면 저장이 거부된다). 그 글들의 문장, 제목, 소제목은 쓰지 않고,
   less_covered(덜 다룬 내용)를 내 사진과 메모로 채워 차별점을 만든다.
4. {COPY_RULES}
5. 미확인 사실은 load_research나 웹 검색으로 확인한다. 내 다른 글 링크를 요청했으면 my_post_links를 호출한다.
6. 사진과 영상은 scan_media의 order대로 배치한다. 영상이 여러 개면 video_question을 보여 주고 내가 고른 영상만 video 블록으로 넣는다.
7. 필수 사실이 빠진 경우만 질문하고, 나머지는 바로 진행한다. 구성안 승인을 따로 받지 않는다.
8. {GEMINI_HANDOFF}
   write_post(dry_run=true, main_keyword=..., hook_pattern=...)로 정리 결과를 확인한다. lint_warnings(마침표, 같은 어미 반복, 촬영 시각, 긴 줄 같은 말투 경고 포함)는 고칠 수 있는 만큼, overlap_warnings는 모두 고친다.
9. write_post로 작업을 접수한다. 접수는 성공이 아니다. draft_status(job_id, wait_seconds=10)로 확인하고 running이면 같은 ID로 조회만 계속한다. 접수 응답을 놓치면 draft_status()로 최근 작업 제목과 상태를 확인한다. saved일 때만 임시저장을 보고한다. 검증은 1회만 하고, 불일치·저장 미확인·실패·상태 불명이면 재전송하지 말고 결과를 알린다.
10. 끝나면 제목, 소제목 목록, 사진 수, 글자 수, 검증 결과, 내가 발행 전에 할 일(직접 입력 자리 N곳과 주제, 추천 문구 줄 정리, 확인필요 N곳, 카테고리 선택, 사진 모자이크 확인 등)을 짧게 알려 준다.
"""
