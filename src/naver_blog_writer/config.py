"""환경 변수 기반 설정.

값은 두 곳에서 읽습니다. 같은 이름이 둘 다 있으면 1번이 우선합니다.
1. 실행 환경의 환경 변수 (Claude Desktop 설정 파일의 "env" 항목)
2. 레포 루트의 .env 파일 (터미널에서 로그인 도우미나 테스트를 돌릴 때 같은 설정을 쓰기 위함)
API 키나 비밀번호는 필요 없습니다. 로그인은 브라우저 창에서 직접 한 번만 합니다.
"""

from __future__ import annotations

import os
import re
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _paths(name: str) -> list[Path]:
    raw = os.environ.get(name, "")
    out: list[Path] = []
    for part in raw.split(os.pathsep):
        part = part.strip().strip('"')
        if part:
            out.append(Path(part).expanduser().resolve())
    return out


# 레포 루트 (src/naver_blog_writer/config.py 기준 두 단계 위)
REPO_ROOT: Path = Path(__file__).resolve().parents[2]


def _load_dotenv(path: Path) -> None:
    """KEY=VALUE 형식의 .env를 읽어, 아직 없는 환경 변수만 채웁니다. NBW_NO_DOTENV=1이면 읽지 않습니다."""
    if os.environ.get("NBW_NO_DOTENV") or not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip().removeprefix("export ").strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()  # 줄 끝 주석 제거: KEY=값  # 설명
        if key:
            os.environ.setdefault(key, value)


_load_dotenv(REPO_ROOT / ".env")

# Gemini는 명시적으로 켠 설치에서만 글감을 외부로 보냅니다. Desktop은 터미널과 PATH가 다를 수 있습니다.
DRAFT_WRITER = os.environ.get("DRAFT_WRITER", "claude").strip().lower()
if DRAFT_WRITER not in {"claude", "gemini"}:
    raise ValueError("DRAFT_WRITER는 claude 또는 gemini입니다.")
AGY_PATH = os.environ.get("AGY_PATH", "agy").strip() or "agy"
AGY_MODEL = os.environ.get("AGY_MODEL", "gemini-3.8-flash-medium").strip()
# Gemini 문안 생성 제한 시간(초). 접수·조회 구조라 Claude Desktop의 도구 대기(약 80초)와 무관합니다.
# 250초였을 때 사진 93장·글 자리 138개 모음집이 시간 초과로 실패했습니다(2026-11-26).
AGY_TIMEOUT_S: int = _int("AGY_TIMEOUT_S", 900)

# 서버가 쓰는 모든 파일(브라우저 프로필, 캐시, 로그)의 기준 폴더. 절대경로라서 cwd 영향을 받지 않습니다.
HOME: Path = Path(os.environ.get("NBW_HOME", str(Path.home() / ".naver-blog-writer"))).expanduser().resolve()
PROFILE_DIR: Path = HOME / "browser-profile"   # 로그인 쿠키. 외부에 공유하지 마세요.
CACHE_DIR: Path = HOME / "cache"               # 미리보기, 영상 프레임, HEIC 변환본
ERROR_DIR: Path = HOME / "errors"              # 실패 시 스크린샷 (디스크에만 저장)
RESEARCH_DIR: Path = HOME / "research"         # /research 결과 노트
MY_POSTS_DIR: Path = HOME / "my_posts"         # 내 블로그 최근 게시물 캐시 (get_style_guide가 매번 갱신)
MY_STYLE_PATH: Path = HOME / "my_style.md"     # 최근 게시물로 자동 생성하는 '내 스타일' (매번 덮어씀)
HISTORY_PATH: Path = HOME / "post_history.json"
LOG_PATH: Path = HOME / "server.log"

# 네이버 블로그 ID (blog.naver.com/<여기>). 비밀번호는 필요 없습니다.
BLOG_ID: str = os.environ.get("NAVER_BLOG_ID", "").strip()

# 사진 루트 폴더 목록 (Google Drive for desktop 동기화 폴더). 여러 개면 OS 경로 구분자로 연결.
MEDIA_ROOTS: list[Path] = _paths("MEDIA_ROOTS")

# 스타일 가이드: 기본은 레포의 style/default.md. STYLE_GUIDE_PATH가 있으면 그 내용을 뒤에 붙여 우선 적용.
DEFAULT_STYLE_GUIDE: Path = REPO_ROOT / "style" / "default.md"
# 개인 고정 규칙 파일(11절). 상대 경로는 레포 폴더 기준입니다(예: style/style_guide.md).
_sg = os.environ.get("STYLE_GUIDE_PATH", "").strip().strip('"')
STYLE_GUIDE_PATH: Path | None = None
if _sg:
    _p = Path(_sg).expanduser()
    STYLE_GUIDE_PATH = (_p if _p.is_absolute() else REPO_ROOT / _p).resolve()

# 내 스타일: 글을 쓸 때마다 내 블로그의 최근 게시물을 이 개수만큼 읽습니다(5개 고정 권장).
RECENT_POSTS_LIMIT: int = max(1, min(_int("RECENT_POSTS_LIMIT", 5), 10))
# 최근 게시물을 읽은 지 이 시간(분)이 지나면 write_post가 get_style_guide를 먼저 호출하라고 요구합니다.
STYLE_SYNC_MAX_AGE_MIN: int = _int("STYLE_SYNC_MAX_AGE_MIN", 180)
# 최신 경향: 네이버 주제별 인기글을 이 일수마다 다시 분석합니다(get_style_guide가 자동으로, refresh_trends로 직접).
TREND_MAX_AGE_DAYS: int = max(1, _int("TREND_MAX_AGE_DAYS", 7))
TREND_POSTS: int = max(5, min(_int("TREND_POSTS", 15), 30))
# 다른 사람 블로그 읽기(같은 장소 리뷰, 주제별 인기글 경향). false면 둘 다 읽지 않고 내 글 5개와 기본 가이드만 씁니다.
READ_OTHER_BLOGS: bool = _bool("READ_OTHER_BLOGS", True)
# 같은 장소 리뷰: place_reviews가 읽는 최신 블로그 리뷰 수
PLACE_REVIEWS_LIMIT: int = max(1, min(_int("PLACE_REVIEWS_LIMIT", 10), 20))

# 브라우저 옵션
HEADLESS: bool = _bool("HEADLESS", False)              # 처음에는 false로 두고 동작을 눈으로 확인하세요.
BROWSER_CHANNEL: str = os.environ.get("BROWSER_CHANNEL", "").strip()  # "chrome" 또는 "msedge" 권장
SLOW_MO_MS: int = _int("SLOW_MO_MS", 0)

# 본문 입력 방식: "insert"(빠름, 기본) 또는 "type"(한 글자씩, 느리지만 호환성 높음)
TYPING_MODE: str = os.environ.get("TYPING_MODE", "insert").strip().lower()

# 본문 정렬: center(기본, 맛집과 여행 상위 블로그 대부분) 또는 left. 사진, 인용구 아래 문단에도 같은 정렬을 맞춥니다.
TEXT_ALIGN: str = os.environ.get("TEXT_ALIGN", "center").strip().lower()
if TEXT_ALIGN not in ("left", "center", "right", "justify"):
    TEXT_ALIGN = "center"
# 소제목 인용구 모양: quotation_line(기본, 세로선), quotation_underline(밑줄), default(큰따옴표),
# quotation_bubble(말풍선), quotation_postit(포스트잇), quotation_corner(모서리). 블록마다 style로 바꿀 수 있습니다.
SUBTITLE_STYLE: str = os.environ.get("SUBTITLE_STYLE", "quotation_line").strip()

# 하루 임시저장 최대 건수 (네이버 약관상 자동화는 회색지대라 1~2건 권장)
DAILY_DRAFT_LIMIT: int = _int("DAILY_DRAFT_LIMIT", 2)

# 같은 글을 연속으로 저장하는 것을 막는 시간(분)
DUPLICATE_WINDOW_MIN: int = _int("DUPLICATE_WINDOW_MIN", 60)

# 업로드 전에 사진의 GPS 위치 정보를 지웁니다(기본 true). 집 근처 사진 등의 위치 노출을 막습니다.
STRIP_GPS: bool = _bool("STRIP_GPS", True)

# view_images 한 번의 응답 크기 상한(바이트, base64 기준). Claude Desktop의 큰 응답 거부를 피합니다.
PREVIEW_BUDGET_BYTES: int = _int("PREVIEW_BUDGET_BYTES", 900_000)

# 저장 버튼을 누른 뒤 저장 완료 신호를 기다리는 시간(초)
SAVE_CONFIRM_TIMEOUT_S: int = _int("SAVE_CONFIRM_TIMEOUT_S", 15)

# 실패 시 스크린샷을 Claude에게 이미지로 돌려줄지 (기본 false: 토큰 절약, 파일 경로만 반환)
RETURN_ERROR_SCREENSHOT: bool = _bool("RETURN_ERROR_SCREENSHOT", False)


def ensure_dirs() -> None:
    for d in (HOME, PROFILE_DIR, CACHE_DIR, ERROR_DIR, RESEARCH_DIR, MY_POSTS_DIR):
        d.mkdir(parents=True, exist_ok=True)
