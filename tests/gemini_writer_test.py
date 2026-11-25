"""가짜 agy 프로세스로 Gemini 도구 → 검증된 문안 회수 → 기존 dry_run까지 확인합니다. 외부 연결 없음."""

import asyncio
import base64
import hashlib
import json
import os
import sys
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

from PIL import Image


def fake_agy():
    args = sys.argv[2:]
    assert args[0] == "acp" and "--sandbox" in args and "--include-default-customizations=false" in args
    settings = Path.home()/".gemini/antigravity-cli"/args[args.index("--cli_dir_name")+1]/"settings.json"
    assert "mcp(*)" in json.loads(settings.read_text())["permissions"]["deny"]
    assert "--dangerously-skip-permissions" not in args and "MEDIA_ROOTS" not in os.environ
    assert not list(Path.cwd().iterdir()), "글감과 사진은 작업 폴더로 전달하면 안 됨"
    def request(method):
        msg = json.loads(sys.stdin.readline())
        assert msg["method"] == method
        return msg

    def send(data):
        print(json.dumps({"jsonrpc": "2.0", **data}), flush=True)

    feature = args[args.index("--feature") + 1]
    init = request("initialize")
    send({"id": init["id"], "result": {"protocolVersion": 1, "agentCapabilities": {"promptCapabilities": {"image": feature != "no_images"}}}})
    if feature == "no_images":
        assert not sys.stdin.readline()
        return
    new = request("session/new")
    assert not new["params"]["mcpServers"]
    send({"id": new["id"], "result": {"sessionId": "s", "models": {"availableModels": [{"modelId": "gemini-test"}] if feature != "no_model" else []}}})
    if feature == "no_model":
        assert not sys.stdin.readline()
        return
    model = request("session/set_model")
    assert model["params"] == {"sessionId": "s", "modelId": "gemini-test"}
    send({"id": model["id"], "result": {}})
    req = request("session/prompt")
    content = req["params"]["prompt"]
    prompt = content[0]["text"]
    assert hashlib.sha256(prompt.encode()).hexdigest() == args[args.index("--expected-text")+1]
    assert all(b["type"] == "image" and b["mimeType"] == "image/jpeg" for b in content[1:])
    hashes = [hashlib.sha256(base64.b64decode(b["data"], validate=True)).hexdigest() for b in content[1:]]
    assert hashes == args[args.index("--expected-images")+1].split(",")
    assert all(s in prompt for s in ("시범 자료", "내본문예시_SENTINEL", "개인규칙_SENTINEL", "최신경향_SENTINEL",
                                     "사용자 개요", "500자이후메모", "가지구이가 달달했어요", "확정한 지점"))
    assert not any(s in prompt for s in ("비공개비교_SENTINEL", "다른지점_SENTINEL", "다른글가이드_SENTINEL", "127.987654", "https://blog.naver.com/testblog/123"))
    time.sleep(0.15)
    if "인증실패" in prompt:
        sys.exit(1)
    if feature == "permission":
        send({"id": 120, "method": "session/request_permission", "params": {"sessionId": "s"}})
        assert json.loads(sys.stdin.readline())["result"]["outcome"]["outcome"] == "cancelled"
        return
    # 실제 Gemini 원고처럼 text 자리에 문단 사이 빈 줄("")을 넣고, 앞뒤에 설명과 코드 울타리를 붙입니다.
    data = {"title": "[전시] 종이학 후기 🕊", "parts": [{"slot": 1, "lines": ["", "1관에 종이학이 있어요", "", "", "2관은 공사 중이었어요", ""]},
            {"slot": 2, "lines": ["종이학이 눈에 들어왔어요", "접힌 선이 기억에 남아요"]},
            {"slot": 3, "lines": ["궁금한 점 있으세요?", "댓글 남겨 주세요"]}]}
    if "잘못된자리" in prompt:
        data["parts"][1]["slot"] = 3
    if "권한필드" in prompt:
        data.update(path="사진.jpg", force=True)
    text = "원고입니다.\n```json\n" + json.dumps(data, ensure_ascii=False) + "\n```\n"
    send({"method": "session/update", "params": {"sessionId": "s", "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}}}})
    send({"id": req["id"], "result": {"stopReason": "end_turn"}})


async def test():
    from naver_blog_writer import config
    from naver_blog_writer.core import blog_reader, notes, post_analysis, trends, writing_packet, writing_sources
    from naver_blog_writer.core.writing_packet import Brief, Slot
    from naver_blog_writer.core.writing_rules import COPY_RULES, PRIORITIES
    from naver_blog_writer.prompts.write import write
    from naver_blog_writer.server import INSTRUCTIONS, build_server
    from naver_blog_writer.tools import extract_video_frames, find_place, place_reviews
    from naver_blog_writer.tools.gemini_draft_status import gemini_draft_status
    from naver_blog_writer.tools.generate_gemini_draft import generate_gemini_draft
    from naver_blog_writer.tools.get_style_guide import get_style_guide
    from naver_blog_writer.tools.load_research import load_research
    from naver_blog_writer.tools.prepare_writing_packet import prepare_writing_packet
    from naver_blog_writer.tools.read_writing_packet import read_writing_packet
    from naver_blog_writer.tools.view_images import view_images
    from naver_blog_writer.tools.write_post import Block, write_post

    blog_reader.save_cache({"blog_id": "testblog", "synced_ts": time.time(), "posts": []})
    config.MY_STYLE_PATH.write_text("내본문예시_SENTINEL\n주소: https://blog.naver.com/testblog/123", encoding="utf-8")
    personal = config.HOME / "rules.md"
    personal.write_text("- 개인규칙_SENTINEL", encoding="utf-8")
    with patch.object(config, "STYLE_GUIDE_PATH", personal), patch.object(trends, "load", return_value={
        "aggregate": {"posts": 2}, "directory_name": "여행", "updated_at": "test"}), patch.object(trends, "render", return_value="최신경향_SENTINEL"):
        guide = await get_style_guide(topic="여행", refresh=False)
    assert guide["draft_writer"] == "gemini"
    config.MY_STYLE_PATH.write_text("다른글가이드_SENTINEL", encoding="utf-8")
    await get_style_guide(topic="카페", refresh=False)  # 중간에 다른 글 요청이 와도 첫 요청에는 첫 가이드가 갑니다.

    @asynccontextmanager
    async def browser_stub():
        yield None, None

    wrong = {"place_id": "1", "name": "다른지점_SENTINEL", "address": "다른동"}
    selected = {"place_id": "2", "name": "확정한 지점", "category": "전시장", "address": "종이동", "phone": "번호",
                "lon": 127.987654, "distance_m": 5}
    with patch.object(find_place, "browser_session", browser_stub), patch.object(find_place.place, "search_places", return_value={
        "best": wrong, "candidates": [wrong, selected], "confident": False}):
        found = await find_place.find_place("전시")
    public = "가지구이가 달달했어요"
    analysis = post_analysis.analyze({"title": "공개 리뷰 제목", "blocks": [{"k": "text", "paras": [
        {"t": "비공개비교_SENTINEL"}, {"t": public}, {"t": "런치 12,000원"}]}]})
    listed = [{"blog_id": "reviewer", "log_no": "123", "date": "2026-11-20", "title": "공개 리뷰 제목", "url": "https://blog.naver.com/reviewer/123"}]
    with patch.object(place_reviews, "browser_session", browser_stub), patch.object(place_reviews.core, "list_reviews", return_value=listed), \
            patch.object(post_analysis, "read_post", return_value=analysis):
        reviews = await place_reviews.place_reviews("2", "확정한 지점")
    assert "비공개비교_SENTINEL" in (config.HOME / "place_reviews/2.json").read_text(encoding="utf-8")
    ref = guide["writing_ref"]
    media_root = config.MEDIA_ROOTS[0]
    photo, frame, video = media_root/"test.jpg", media_root/"frame.jpg", media_root/"test.mp4"
    Image.new("RGB", (100, 100), "blue").save(photo)
    Image.new("RGB", (80, 40), "red").save(frame)
    video.touch()
    seen = (await view_images([str(photo)], ref))[-1].data
    with patch.object(extract_video_frames.media, "extract_frames", return_value=[frame]):
        seen_frame = (await extract_video_frames.extract_video_frames(str(video), ref, 1))[-1].data
    # 같은 사진을 다시 보면(한 호출 안의 반복 포함) 새로 담지 않고 같은 V번호를 돌려줍니다.
    again = await view_images([str(photo)] * 3, ref)
    assert [x for x in again if isinstance(x, str) and "(A1/V1)" in x] and len(writing_sources.collection(ref).images) == 2
    # 10장 초과 페이지 경계: 서로 다른 사진 9장을 더 봅니다.
    others = [media_root / f"other{n}.jpg" for n in range(9)]
    for n, other in enumerate(others):
        Image.new("RGB", (60 + n, 60), "green").save(other)
    seen_others = [i.data for i in await view_images([str(o) for o in others], ref) if not isinstance(i, str)]
    expected = [seen, seen_frame, *seen_others]
    for f in (photo, frame, *others):
        f.unlink()  # 집필 시 원본 재읽기 금지
    notes.save("전시노트", "종이학 확인: 가상박물관 https://example.org/exhibit")
    await load_research("전시노트", guide_ref=ref)
    brief = Brief(request="시범 자료: 전시", title="지정 제목", outline="사용자 개요", memo="기억해요\n" * 110 + "500자이후메모",
                    facts=["1관에 종이학이 있음"], guide_ref=guide["writing_ref"], place_refs=[found["candidates"][1]["writing_ref"]],
                    user_updates=["정정: 전시명은 종이학"], web_findings=["박물관 공식 안내: 종이학 전시"], visual_count=len(expected),
                    review_refs=[reviews["writing_ref"]], slots=[Slot(kind="text", hint="동선"),
                    Slot(kind="fill", hint="전시 감상"), Slot(kind="quote", hint="마무리")])
    with patch.object(place_reviews.core, "load_recent_for_overlap", side_effect=AssertionError("본문 캐시 접근")):
        info = await prepare_writing_packet(brief)
        pid = info["packet_id"]
        prompt, = await read_writing_packet(pid, 0)
        pages = [await read_writing_packet(pid, n) for n in range(1, info["pages"])]
        assert [len(page) for page in pages] == [10, 1] and [i.data for page in pages for i in page] == expected
    for rules in (PRIORITIES, COPY_RULES):
        assert all(rules in instructions for instructions in (INSTRUCTIONS, write(), prompt))
    data = json.loads(prompt.rsplit("\n\n", 1)[1])
    assert data["guide"] == guide["guide"].replace("https://blog.naver.com/testblog/123", "[URL 생략]")
    assert data["topic"] == "여행" and (data["request"], data["title"], data["outline"], data["memo"]) == (brief.request, brief.title, brief.outline, brief.memo)
    assert data["places"][0]["phone"] == "번호" and data["place_reviews"][0]["reviews"][0]["info_lines"] == ["런치 12,000원"]
    assert data["user_updates"] == brief.user_updates and data["web_findings"] == brief.web_findings
    assert "종이학 확인: 가상박물관" in data["evidence"][-1]["content"] and data["evidence"][1]["kind"] == "video_frame"
    with patch.object(config, "DRAFT_WRITER", "claude"):
        assert (await prepare_writing_packet(brief))["packet_id"] == pid
    # 별도 글의 미리보기 개수는 이 글과 섞이지 않습니다.
    assert not writing_sources.collection((await get_style_guide(refresh=False))["writing_ref"]).images
    print("PASS  두 모델의 작성 지침과 가이드 일치, 긴 메모 보존, 확정 지점과 공개 분석만 연결")
    assert {"generate_gemini_draft", "gemini_draft_status"} <= {t.name for t in await build_server().list_tools()}

    # Windows에서도 실행 가능한 가짜 CLI: 실제 파이프·argv·cwd 처리는 그대로 두고 실행 파일만 대체합니다.
    spawn = asyncio.create_subprocess_exec
    calls = []
    expected_text = prompt
    feature = "ok"

    async def fake_exec(exe, *args, **kwargs):
        calls.append(exe)
        return await spawn(sys.executable, str(Path(__file__).resolve()), "--fake-agy", *args,
                           "--expected-text", hashlib.sha256(expected_text.encode()).hexdigest(), "--feature", feature,
                           "--expected-images", ",".join(hashlib.sha256(b).hexdigest() for b in expected), **kwargs)

    with patch.object(asyncio, "create_subprocess_exec", fake_exec):
        receipt = await generate_gemini_draft(pid)
        assert receipt["status"] == "running"
        assert (await generate_gemini_draft(pid))["generation_id"] == receipt["generation_id"]
        state = await gemini_draft_status(wait_seconds=5)
        assert state["status"] == "ready", state
        draft = state["draft"]
        assert (await generate_gemini_draft(pid))["status"] == "ready" and len(calls) == 1
        assert draft["blocks"][0]["text"] == "1관에 종이학이 있어요\n\n2관은 공사 중이었어요", draft["blocks"][0]
        blocks = [Block(**b) for b in draft["blocks"]]
        plan = json.loads((await write_post(draft["title"], blocks, dry_run=True))[1])
        assert plan["blocks"][1]["topic"] == "전시 감상" and plan["to_fill_before_publish"]["추천_문구_줄"] == 2
        print("PASS  작성 자료와 문안 자리 → 접수·중복 방지 → 결과 회수 → 기존 dry_run")

        for case in ("잘못된자리", "권한필드", "인증실패", "no_images", "no_model", "permission"):
            feature = case
            fail_id = (await prepare_writing_packet(brief.model_copy(update={"request": "시범 자료 " + case})))["packet_id"]
            expected_text, = await read_writing_packet(fail_id)
            r = await generate_gemini_draft(fail_id)
            state = await gemini_draft_status(r["generation_id"], 5)
            assert state["status"] == "failed" and "draft" not in state, (case, state)
            need = {"no_images": "이미지 입력", "no_model": "지정 모델", "permission": "권한 요청"}.get(case)
            assert not need or need in state["message"], state
            assert state["packet_id"] == fail_id and "read_writing_packet" in state["fallback"], state
            if case in ("잘못된자리", "권한필드"):
                assert Path(state["raw_saved"]).read_text(encoding="utf-8").startswith("원고입니다."), state
            if case == "권한필드":
                assert "Extra inputs" in state["message"] or "path" in state["message"], state["message"]
        assert len(calls) == 7 and not config.HISTORY_PATH.exists()
        print("PASS  11개 JPEG와 텍스트 해시·순서 일치, 자리/필드 오류·CLI 종료·모델/이미지 미지원·권한 요청 시 중단")

        with patch.object(config, "DRAFT_WRITER", "claude"):
            try:
                await generate_gemini_draft(pid)
            except ValueError as e:
                assert "꺼져" in str(e)
            else:
                raise AssertionError("Claude 모드에서 Gemini가 켜졌습니다")
        with patch.object(config, "READ_OTHER_BLOGS", False):
            off = await get_style_guide(refresh=False)
            off_prompt = writing_packet.freeze(brief.model_copy(update={"guide_ref": off["writing_ref"], "visual_count": 0})).text
            assert public not in off_prompt and "최신경향_SENTINEL" not in off_prompt
        with patch.object(place_reviews, "browser_session", browser_stub), patch.object(place_reviews.core, "analyze_place", new=AsyncMock(side_effect=ValueError("실패"))):
            failed = await place_reviews.place_reviews("2", "확정한 지점")
        no_reviews = writing_packet.freeze(brief.model_copy(update={"review_refs": [failed.split("writing_ref: ")[1]]})).text
        assert public not in no_reviews and "리뷰 조회 실패" in no_reviews
        for update in ({"visual_count": 0}, {"guide_ref": "missing"}, {"guide_ref": brief.place_refs[0]}, {"review_refs": []}, {"place_refs": [wrong["writing_ref"]]}):
            try:
                await prepare_writing_packet(brief.model_copy(update=update))
            except ValueError:
                pass
            else:
                raise AssertionError(f"잘못된 참조가 허용됐습니다: {update}")
        try:
            await prepare_writing_packet(brief.model_copy(update={"facts": ["/Users/abc/file.jpg"]}))
        except ValueError as e:
            assert "경로" in str(e)
        else:
            raise AssertionError("경로가 글감에 포함됐습니다")
        assert len(calls) == 7
        print("PASS  Claude 모드, 다른 글 읽기 설정, 경로 전송 차단")

    fresh_ref = (await get_style_guide(refresh=False))["writing_ref"]
    fresh = writing_sources.collection(fresh_ref)
    try:
        fresh.previews([(media_root / f"p{n}.jpg", seen) for n in range(101)], "photo")
    except ValueError as e:
        assert "한도" in str(e) and not fresh.images
    else:
        raise AssertionError("초과 장수가 조용히 생략됐습니다.")
    dup = writing_sources.collection((await get_style_guide(refresh=False))["writing_ref"])
    assert dup.previews([(photo, b"small")], "photo") == ["A1/V1"]
    assert dup.previews([(photo, b"larger-jpeg"), (photo, b"x")], "photo") == ["A1/V1", "A1/V1"] and dup.images == [b"larger-jpeg"]
    assert dup.previews([(video, b"f1"), (video, b"f2"), (video, b"f1")], "video_frame") == ["A2/V2", "A2/V3", "A2/V2"]
    assert len(dup.images) == 3 and [e["visual"] for e in dup.evidence] == ["V1", "V2", "V3"]
    try:
        await prepare_writing_packet(brief.model_copy(update={"memo": "가" * 200_000}))
    except ValueError as e:
        assert "512KiB" in str(e)
    else:
        raise AssertionError("초과 텍스트가 조용히 생략됐습니다.")
    text_info = await prepare_writing_packet(brief.model_copy(update={"guide_ref": fresh_ref, "visual_count": 0,
                                                                       "place_refs": [], "review_refs": []}))
    assert text_info["images"] == 0 and text_info["pages"] == 1
    assert len(await read_writing_packet(text_info["packet_id"])) == 1
    print("PASS  한도 초과 시 부분 저장 없이 중단, 사진 없는 글은 텍스트 한 쪽만 확정")


if __name__ == "__main__":
    if sys.argv[1:2] == ["--fake-agy"]:
        fake_agy()
    else:
        with tempfile.TemporaryDirectory(prefix="nbw_gemini_") as home:
            os.environ.update(HOME=home, USERPROFILE=home, NBW_NO_DOTENV="1", NBW_HOME=home, NAVER_BLOG_ID="testblog", MEDIA_ROOTS=home,
                              READ_OTHER_BLOGS="true", DRAFT_WRITER="gemini", AGY_PATH=sys.executable, AGY_MODEL="gemini-test")
            asyncio.run(test())
