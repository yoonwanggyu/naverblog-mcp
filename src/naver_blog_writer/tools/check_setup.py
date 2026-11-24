"""check_setup: 설치와 설정을 한 번에 점검합니다."""

import platform

from mcp.types import ToolAnnotations

from .. import config
from ..core import blog_reader, gemini_writer, history, media, style, trends
from ..core.browser import browser_session, check_login

ANNOTATIONS = ToolAnnotations(title="설치 점검", readOnlyHint=True, openWorldHint=True)


async def check_setup(check_browser: bool = True) -> dict:
    """설치와 설정이 올바른지 한 번에 점검합니다. 새 컴퓨터에 설치한 직후 가장 먼저 실행합니다."""
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"item": name, "ok": ok, "detail": detail})

    add("OS", True, f"{platform.system()} {platform.release()}, Python {platform.python_version()}")
    if config.DRAFT_WRITER == "gemini":
        try:
            add("Gemini 문안 작성", True, f"{gemini_writer.executable()} ({config.AGY_MODEL}, 로그인은 터미널에서 확인)")
        except ValueError as e:
            add("Gemini 문안 작성", False, str(e))
    add("NAVER_BLOG_ID", bool(config.BLOG_ID), config.BLOG_ID or "비어 있음. 설정 env에 블로그 ID를 넣으세요.")
    if not config.MEDIA_ROOTS:
        add("MEDIA_ROOTS", False, "비어 있음. Google Drive의 블로그 사진 폴더 경로를 넣으세요.")
    for root in config.MEDIA_ROOTS:
        if root.is_dir():
            try:
                n = sum(1 for p in root.rglob("*") if p.suffix.lower() in media.IMAGE_EXT | media.VIDEO_EXT)
                add("MEDIA_ROOTS", True, f"{root} (사진과 영상 {n}개)")
            except PermissionError:
                add("MEDIA_ROOTS", False, f"{root} 읽기 권한 없음. macOS 개인정보 보호 설정에서 Claude의 파일 접근을 허용하세요.")
        else:
            add("MEDIA_ROOTS", False, f"{root} 폴더가 없습니다. Finder에서 Option+Command+C로 경로를 다시 복사하세요.")
    try:
        sg = style.load_style_guide()
        add("스타일 가이드", True, ", ".join(sg["sources"]))
    except Exception as e:
        add("스타일 가이드", False, str(e))
    try:
        config.ensure_dirs()
        probe = config.HOME / ".write_test"
        probe.write_text("ok")
        probe.unlink()
        add("NBW_HOME", True, str(config.HOME))
    except Exception as e:
        add("NBW_HOME", False, f"{config.HOME} 쓰기 실패: {e}")
    try:
        add("ffmpeg", True, media.ffmpeg_exe())
    except Exception as e:
        add("ffmpeg", False, f"영상 프레임 추출 불가: {e}")
    add("HEIC 지원", media.HEIF_OK, "" if media.HEIF_OK else "pillow-heif 설치 필요")
    add("하루 임시저장 한도", True, f"{history.drafts_today()}/{config.DAILY_DRAFT_LIMIT}건 사용")
    add("발행", True, "코드에서 차단됨. 발행은 사용자가 직접 합니다.")
    cache = blog_reader.load_cache()
    add("내 스타일(최근 게시물)", True, f"{len(cache.get('posts', []))}개, 마지막으로 읽은 시각 {cache.get('synced_at') or '없음'} "
        "(get_style_guide가 매번 갱신)")
    add("사진 GPS 제거", True, "켜짐" if config.STRIP_GPS else "꺼짐 (원본 위치 정보가 함께 올라갑니다)")
    if config.READ_OTHER_BLOGS:
        ages = []
        for topic in ("맛집", "여행", "결혼준비", "취업준비"):
            data = trends.load(topic)
            if data:
                ages.append(f"{data['directory_name']} {data['updated_at']}")
        add("다른 블로그 읽기", True, "켜짐: 같은 장소 리뷰(장소 글마다), 주제별 인기글 경향(7일마다). 저장된 경향: "
            + (", ".join(sorted(set(ages))) or "아직 없음(첫 get_style_guide 때 만듦)"))
    else:
        add("다른 블로그 읽기", True, "꺼짐(READ_OTHER_BLOGS=false): 내 글 5개와 기본 가이드만 씁니다.")
    if check_browser:
        try:
            async with browser_session() as (_ctx, page):
                res = await check_login(page)
            add("브라우저 실행", True, config.BROWSER_CHANNEL or "Playwright Chromium")
            add("네이버 로그인", bool(res.get("logged_in")), res.get("reason", "로그인됨"))
        except Exception as e:
            hint = ""
            if "Executable doesn't exist" in str(e) or "chrome" in str(e).lower():
                hint = " 크롬을 설치하거나 BROWSER_CHANNEL을 비우고 'uv run playwright install chromium'을 실행하세요."
            add("브라우저 실행", False, f"{e}{hint}"[:400])
    return {"all_ok": all(c["ok"] for c in checks), "checks": checks}
