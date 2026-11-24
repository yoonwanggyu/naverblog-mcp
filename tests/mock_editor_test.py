"""가짜 SmartEditor 페이지로 브라우저 조작 로직을 검증합니다. 네이버에 접속하지 않습니다.

    BROWSER_CHANNEL=chrome uv run python tests/mock_editor_test.py

BROWSER_CHANNEL을 지정하지 않으면 Playwright Chromium을 씁니다(uv run playwright install chromium 필요).
검증 항목: 최근 글 확인 강제, 블록 순서, 한 줄 한 호흡 정리, 직접 입력 자리와 추천 문구, 가운데 정렬, 굵게,
인용구 소제목과 모양, 구분선, 사진 설명, 업로드 완료 대기,
업로드 진행 팝업 보호, 주소로 장소 고르기, 장소 실패 시 검색창 닫기, 중간 팝업 자동 취소, 저장 완료 확인과 미확인,
발행 버튼 차단(코드와 페이지 두 겹), 최근 글 겹침 경고(블로그 글 포함), 진행 상황 보고, 하루 한도.
"""

import asyncio
import functools
import http.server
import json
import logging
import os
import re
import sys
import tempfile
import threading
import time
from pathlib import Path

from PIL import Image

HERE = Path(__file__).parent
tmp = Path(tempfile.mkdtemp(prefix="nbw_test_"))
media_dir = tmp / "media"
media_dir.mkdir()
for name, color in (("a.jpg", (40, 120, 200)), ("b.jpg", (200, 80, 40)), ("c.jpg", (20, 160, 60))):
    Image.new("RGB", (800, 600), color).save(media_dir / name)
import subprocess  # noqa: E402

import imageio_ffmpeg  # noqa: E402

for vname in ("clip1.mp4", "clip2.mp4"):  # 폴더에 영상 2개: 사용자가 고른 영상만 넣을 수 있어야 함
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=blue:s=64x48:d=1",
                    "-metadata", "location=+37.5+127.0/", "-y", str(media_dir / vname)], check=True)

os.environ["NBW_NO_DOTENV"] = "1"  # 테스트는 레포의 .env를 읽지 않습니다
os.environ.update(MEDIA_ROOTS=str(media_dir), NAVER_BLOG_ID="testblog", NBW_HOME=str(tmp / "home"),
                  HEADLESS=os.environ.get("HEADLESS", "true"), DAILY_DRAFT_LIMIT="3")

handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(HERE / "mock_editor"))
handler.log_message = lambda *a: None
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "WARNING"), format="%(asctime)s %(name)s %(message)s")
httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
port = httpd.server_address[1]

from naver_blog_writer import config  # noqa: E402
from naver_blog_writer.core import blog_reader, draft_jobs, editor, place_reviews  # noqa: E402
from naver_blog_writer.core import selectors as S  # noqa: E402
from naver_blog_writer.core.browser import browser_session  # noqa: E402
from naver_blog_writer.core.formatting import PLACEHOLDER  # noqa: E402
from naver_blog_writer.tools.draft_status import draft_status  # noqa: E402
from naver_blog_writer.tools.recent_posts import recent_posts  # noqa: E402
from naver_blog_writer.tools.write_post import Block, write_post  # noqa: E402

S.WRITE_URLS[:] = [f"http://127.0.0.1:{port}/index.html?{{blog_id}}"]
cap: dict = {}
_save = editor.save_draft


async def save_wrap(frame, timeout_s=None):
    if cap.get("no_save"):
        await frame.evaluate("window.__noSave = true")
    cap["publish_clicked"] = await frame.evaluate("window.__publishClicked === true")
    cap["mid_popup_closed"] = await frame.evaluate("window.__midPopupClosed === true")
    cap["upload_canceled"] = await frame.evaluate("window.__uploadCanceled === true")
    cap["place_layer_open"] = await frame.evaluate("document.getElementById('placeLayer').classList.contains('show')")
    cap["wrong_add"] = await frame.evaluate("window.__wrongAdd === true")
    cap["late_popup"] = await frame.evaluate("({closed: window.__latePopupClosed === true, resumed: window.__latePopupResumed === true})")
    res = await _save(frame, timeout_s)
    cap["doc"] = await frame.evaluate("window.__saved")
    return res


editor.save_draft = save_wrap


async def finish_draft(receipt: list) -> dict:
    job_id = json.loads(receipt[0])["job_id"]
    seen = set()
    while True:
        state = json.loads((await draft_status(job_id))[0])
        seen.add(state["done"])
        if state["status"] != "running":
            break
        await asyncio.sleep(0.1)
    cap["progress"] = seen
    # 결과는 창을 닫기 전에도 보입니다. 다음 독립 브라우저 테스트는 프로필 반환 후 시작합니다.
    await draft_jobs._active
    return state["result"]


BLOCKS = [
    Block(type="text", text="  성수에서 데이트를 했어요. 날씨가 정말 좋더라고요. 첫 코스는 카페였어요.\n\n**카페어니언** 이야기부터 할게요"),
    Block(type="subtitle", text="성수 카페어니언 분위기는 어땠나요?"),
    Block(type="image", path=str(media_dir / "a.jpg"), caption="성수 카페어니언 2층 창가 자리"),
    Block(type="text", text=f"2층은 창가 자리가 길게 이어져 있어요. {PLACEHOLDER}"),
    Block(type="fill", topic="팡도르 맛", suggestions=["겉은 바삭하고 속은 촉촉했어요", "둘이 나눠 먹기 딱 좋은 크기였어요"]),
    Block(type="image", path=str(media_dir / "b.jpg")),
    Block(type="video", path=str(media_dir / "clip1.mp4"), video_title="성수 카페어니언 영상", user_selected=True),
    Block(type="place", query="카페어니언 성수동", address="킨텍스로 255 상가234호"),
    Block(type="place", query="없는장소"),
    Block(type="place", query="카페어니언 일산", address="킨텍스로 255 상가234호"),
    Block(type="divider"),
    Block(type="subtitle", text="성수 카페어니언은 어디에 있나요?", style="quotation_underline"),
    Block(type="text", text="**✔️ 팡도르 / 7,500원**\n다음엔 저녁도 먹어볼게요. 또 올 것 같아요 😊"),
    Block(type="quote", style="quotation_bubble", text="성수 카페 찾는 분이라면 참고해 보세요\n궁금한 점은 **댓글**로 남겨 주세요 :)"),
    Block(type="text", text="카페 말고 다른 데이트 기록도 궁금하시다면\n여기 남겨 둘게요"),
    Block(type="link", url="https://m.blog.naver.com/testblog/224000000001"),
]
TITLE = "성수 데이트 코스 카페어니언 후기"
TAGS = ["성수데이트", "카페어니언"]
SPONSOR = "이 글은 업체로부터 음료를 제공받아 작성한 후기입니다."
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


async def main() -> int:
    try:
        await write_post(title=TITLE, blocks=BLOCKS, dry_run=True)
        check("최근 글 미확인 시 거부", False, "통과해 버림")
    except ValueError as e:
        check("최근 글 미확인 시 거부", "get_style_guide" in str(e))

    # get_style_guide가 블로그에서 최근 글을 읽은 상태를 만듭니다(블로그 글 1개).
    blog_reader.save_cache({"blog_id": "testblog", "synced_at": "test", "synced_ts": time.time(), "posts": [
        {"title": "성수 카페어니언 다녀온 날", "subtitles": ["성수 카페어니언 분위기는 어땠나요?"], "tags": ["성수"],
         "body": "블로그에 이미 올린 글입니다. 여기는 두 번째 문장이에요.", "char_count": 30, "image_count": 2, "layout": []}]})

    try:
        await write_post(title=TITLE, blocks=BLOCKS, tags=TAGS, sponsored=SPONSOR, dry_run=True)
        check("장소 리뷰를 읽지 않으면 거부", False, "통과해 버림")
    except ValueError as e:
        check("장소 리뷰를 읽지 않으면 거부", "place_reviews" in str(e) and "카페어니언" in str(e), str(e)[:80])
    config.READ_OTHER_BLOGS = False                                       # 끄면 장소 리뷰 확인을 하지 않음
    r_off = await write_post(title=TITLE, blocks=BLOCKS, tags=TAGS, sponsored=SPONSOR, dry_run=True)
    check("다른 블로그 읽기를 끄면 장소 리뷰 없이도 진행", r_off[0].startswith("검증 통과"))
    config.READ_OTHER_BLOGS = True
    place_reviews.save_for_overlap("1001", "카페어니언", [])            # 같은 장소 리뷰를 읽은 기록
    place_reviews.record_failure("1002", "없는장소", "테스트용 실패")     # 읽기에 실패한 기록: 막지 않고 안내만

    r = await write_post(title=TITLE, blocks=BLOCKS, tags=TAGS, sponsored=SPONSOR, dry_run=True)
    plan = json.loads(r[1])
    check("리뷰 읽기 실패한 장소는 안내만 남기고 진행", any("없는장소" in n for n in plan["place_review_notes"]), str(plan["place_review_notes"]))
    check("dry_run 한 줄 한 호흡 정리(긴 줄은 문장마다 나눔, 빈 줄은 묶음 경계)", plan["blocks"][0]["paragraphs"] == [
          "성수에서 데이트를 했어요.\n날씨가 정말 좋더라고요.\n첫 코스는 카페였어요.", "**카페어니언** 이야기부터 할게요"],
          str(plan["blocks"][0]["paragraphs"]))
    check("fill 블록: 직접 입력 자리 아래 추천 문구", plan["blocks"][4]["paragraphs"] == [
          f"{PLACEHOLDER}\n(추천 문구) 겉은 바삭하고 속은 촉촉했어요\n(추천 문구) 둘이 나눠 먹기 딱 좋은 크기였어요"],
          str(plan["blocks"][4]["paragraphs"]))
    check("직접 입력 자리는 독립 문단", plan["blocks"][3]["paragraphs"] == ["2층은 창가 자리가 길게 이어져 있어요.", PLACEHOLDER],
          str(plan["blocks"][3]["paragraphs"]))
    check("채울 자리 개수 보고", plan["to_fill_before_publish"]["직접_입력"] == 2 and plan["to_fill_before_publish"]["추천_문구_줄"] == 2,
          str(plan["to_fill_before_publish"]))
    check("겹침 비교 대상에 블로그 글 포함", any(c.startswith("블로그:") for c in plan["compared_with"])
          and any("소제목" in w for w in plan["overlap_warnings"]), str(plan["overlap_warnings"])[:90])

    r = await write_post(title=TITLE, blocks=BLOCKS, tags=TAGS, sponsored=SPONSOR, review_seconds=0)
    res = await finish_draft(r)
    check("임시저장 성공(저장 확인됨)", res.get("ok") is True and res.get("saved_confirmed") is True,
          res.get("save_signal", "") if res.get("ok") else r[0][:300])
    check("텍스트 검증 1회 통과", res.get("verify", {}).get("ok") is True, json.dumps(res.get("verify"), ensure_ascii=False))
    doc = cap.get("doc") or []
    kinds = [d.get("cls", "img").split()[0] if "cls" in d else "img" for d in doc]
    seq = [k for k in kinds if k != "se-text"]
    check("블록 순서", seq == ["se-documentTitle", "se-quotation", "img", "img", "se-video", "se-placesMap", "se-placesMap", "se-horizontalLine", "se-quotation",
                            "se-quotation", "se-oglink"], str(kinds))
    texts_before_card = [d.get("text", "") for d in doc[:-1]]
    check("내 글 링크: 주소 줄 아래 카드, 그 아래 태그", kinds[-2:] == ["se-oglink", "se-text"]
          and any("https://blog.naver.com/testblog/224000000001" in t for t in texts_before_card)
          and "#성수데이트" in doc[-1].get("text", ""), str(kinds[-4:]))
    quotes = [d["text"] for d in doc if d.get("cls", "").startswith("se-quotation")]
    check("소제목 글자가 쪼개지지 않음(인용구 다음 문단)", quotes[:2] == ["성수 카페어니언 분위기는 어땠나요?", "성수 카페어니언은 어디에 있나요?"], str(quotes))
    qcls = [d["cls"] for d in doc if d.get("cls", "").startswith("se-quotation")]
    check("인용구 모양(기본 세로선, 블록별 밑줄)", len(qcls) == 3 and "se-l-quotation_line" in qcls[0] and "se-l-quotation_underline" in qcls[1], str(qcls))
    check("마무리 상자: 말풍선 인용구 하나에 두 줄", len(qcls) == 3 and "se-l-quotation_bubble" in qcls[2]
          and quotes[2:] == ["성수 카페 찾는 분이라면 참고해 보세요\n궁금한 점은 댓글로 남겨 주세요 :)"], str(quotes[2:]))
    paras = [p for d in doc if d.get("cls", "").startswith("se-text") for p in d.get("paras", []) if re.sub(r"<[^>]+>|\s", "", p["html"])]
    off = [re.sub(r"<[^>]+>", "", p["html"])[:15] for p in paras if p["align"] != "center"]
    check("본문 전체 가운데 정렬(블록 아래 문단 포함)", bool(paras) and not off, f"{len(paras)}줄, 가운데 아님: {off[:3]}")
    check("**굵게** 입력", any(re.search(r"<b>카페어니언</b>", p["html"]) for p in paras), str([p["html"][:40] for p in paras if "카페어니언" in p["html"]][:2]))
    check("장소 팝업 밖의 '추가' 버튼을 누르지 않음", cap.get("wrong_add") is False)
    first_text = doc[1]["text"] if len(doc) > 1 else ""
    check("늦게 뜬 '작성 중인 글' 창은 '취소'로 닫고 새 글로 시작", cap.get("late_popup") == {"closed": True, "resumed": False}
          and doc and doc[0].get("text") == TITLE, f"{cap.get('late_popup')} 제목={doc[0].get('text') if doc else None}")
    check("새 글로 시작했다는 안내", any("'취소'를 누르고 새 글로 시작" in n for n in res.get("notes", [])), str(res.get("notes"))[:80])
    check("협찬 문구가 본문 맨 앞", first_text.startswith("이 글은 업체로부터"), first_text[:30])
    lines1 = [re.sub(r"<[^>]+>|&nbsp;", "", p["html"]).strip() for p in (doc[1].get("paras", []) if len(doc) > 1 else [])]
    check("묶음 안은 한 줄씩, 묶음 사이 빈 줄", lines1[2:7] == ["성수에서 데이트를 했어요.", "날씨가 정말 좋더라고요.", "첫 코스는 카페였어요.", "",
                                                    "카페어니언 이야기부터 할게요"], str(lines1[:8]))
    check("추천 문구 입력", any("(추천 문구) 겉은 바삭하고 속은 촉촉했어요" in d.get("text", "") for d in doc))
    check("직접 입력 문구 입력", any(PLACEHOLDER in d.get("text", "") for d in doc))
    check("사진 설명 입력", any(d.get("caption") == "성수 카페어니언 2층 창가 자리" for d in doc))
    imgs = [d for d in doc if "img" in d]
    check("사진 업로드 완료 후 진행", len(imgs) == 2 and all(d["src"].startswith("uploaded.svg") for d in imgs), str([d.get("src") for d in imgs]))
    check("업로드 진행 팝업의 취소를 누르지 않음", cap.get("upload_canceled") is False)
    places = [d["text"] for d in doc if d.get("cls", "").startswith("se-placesMap")]
    check("주소가 같은 장소 선택(동 붙은 검색어 0건 → 상호명만)", len(places) == 2 and "킨텍스로 255" in places[0] and "본점" not in places[0], str(places))
    check("주소가 맞는 지점이 없으면 다른 지점을 넣지 않고 상호명만으로 다시 검색", len(places) == 2 and "킨텍스로 255" in places[1], str(places[1:]))
    check("장소 실패 시 검색창 닫고 본문에 자리 표시", cap.get("place_layer_open") is False
          and any("📍 없는장소" in d.get("text", "") for d in doc), str(res.get("notes"))[:80])
    check("중간 팝업 자동 취소", cap.get("mid_popup_closed") is True)
    check("발행 버튼 미클릭", cap.get("publish_clicked") is False)
    check("해시태그 본문 끝", bool(doc) and "#성수데이트" in doc[-1].get("text", ""), doc[-1].get("text", "")[-40:] if doc else "")
    check("이모지가 섞인 문장 전체 입력", any("또 올 것 같아요 😊" in d.get("text", "") for d in doc))
    check("변형 선택자 이모지(✔️)가 섞인 굵은 줄 전체 입력", any("<b>✔ 팡도르 / 7,500원</b>" in p["html"] for p in paras)
          and any("다음엔 저녁도" in d.get("text", "") for d in doc), str([p["html"] for p in paras if "팡도르" in p["html"]]))
    check("상태 조회로 블록 진행과 완료 확인", any(0 < n <= len(BLOCKS) for n in cap["progress"])
          and max(cap["progress"]) == len(BLOCKS) + 4)

    async with browser_session() as (_ctx, page):
        frame = await editor.open_editor(page)
        btn = frame.locator("[data-testid='seOnePublishBtn']")
        try:
            await editor.safe_click(btn)
            check("코드 수준 발행 차단", False, "safe_click이 통과함")
        except editor.PublishBlockedError:
            check("코드 수준 발행 차단", True)
        await btn.click()  # safe_click을 우회한 직접 클릭도 페이지 가드가 막아야 함
        check("페이지 수준 발행 차단", await frame.evaluate("window.__publishClicked !== true"),
              f"blocked={await frame.evaluate('window.__nbwBlocked')}")

    r = await write_post(title="두 번째 글 테스트 제목입니다", blocks=[Block(type="text", text="두 번째 글입니다. 짧아요.")],
                         main_keyword="두 번째 글", hook_pattern="인사형", review_seconds=0)
    check("두 번째 저장", (await finish_draft(r))["ok"], r[0][:80])
    rec = await recent_posts()
    check("최근 글 기록(블로그 + 임시저장)", len(rec["blog_posts"]) == 1 and len(rec["drafts"]) == 2
          and rec["drafts"][0]["hook_pattern"] == "인사형", f"blog={len(rec['blog_posts'])} drafts={len(rec['drafts'])}")
    r = await write_post(title="두 번째 글 테스트 제목입니다 다시", blocks=[Block(type="text", text="두 번째 글입니다. 짧아요.")],
                         main_keyword="두 번째 글", hook_pattern="인사형", dry_run=True)
    check("겹침 경고 (dry_run)", len(json.loads(r[1])["overlap_warnings"]) >= 2, str(json.loads(r[1])["overlap_warnings"])[:80])

    cap["no_save"] = True
    config.SAVE_CONFIRM_TIMEOUT_S = 3
    r = await write_post(title="저장 실패 재현용 테스트 제목입니다", blocks=[Block(type="text", text="저장 신호가 없는 경우입니다.")],
                         review_seconds=0)
    res = await finish_draft(r)
    check("저장 미확인이면 성공으로 보고하지 않음", res.get("ok") is False and res.get("saved_confirmed") is False, res.get("save_signal", r[0][:80]))
    cap["no_save"] = False
    try:
        Block(type="link", url="https://blog.naver.com/someoneelse/224000000001")
        check("다른 사람 블로그 링크는 거부", False, "통과해 버림")
    except ValueError:
        check("다른 사람 블로그 링크는 거부", True)
    vids = [d for d in doc if d.get("cls") == "se-video"]
    check("영상 업로드: 제목과 함께 영상 블록, 위치 정보 지운 사본", len(vids) == 1 and "성수 카페어니언 영상" in vids[0]["text"]
          and vids[0]["video"].startswith("clip1_"), str(vids))
    try:
        await write_post(title=TITLE, blocks=[Block(type="video", path=str(media_dir / "clip2.mp4"))], dry_run=True)
        check("영상이 여러 개면 사용자가 고르기 전에는 거부", False, "통과해 버림")
    except ValueError as e:
        check("영상이 여러 개면 사용자가 고르기 전에는 거부", "사용자에게 먼저 물어보고" in str(e), str(e)[:60])
    try:
        await write_post(title="네 번째 글 테스트 제목입니다", blocks=[Block(type="text", text="네 번째 글입니다.")], review_seconds=0)
        check("하루 한도 차단(미확인 저장 포함)", False)
    except RuntimeError as e:
        check("하루 한도 차단(미확인 저장 포함)", "한도" in str(e))
    return 0 if all(ok for _, ok, _ in results) else 1


sys.exit(asyncio.run(main()))
