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
    # 사진 배치도 Gemini가 정합니다: V1(사진), V3~V4는 나란히, V5~V9는 슬라이드, V10은 흐려서 뺌, V11은 놓지 않음. V2는 영상 장면.
    data = {"title": "[전시] 종이학 후기 🕊", "parts": [
            {"slot": 1, "visuals": ["V1", "V3", "V4"], "layout": "strip", "lines": ["", "1관에 종이학이 있어요", "", "", "2관은 공사 중이었어요", ""]},
            {"slot": 2, "visuals": ["V5", "V6", "V7", "V8", "V9"], "layout": "slide", "lines": ["종이학이 눈에 들어왔어요", "접힌 선이 기억에 남아요"]},
            {"slot": 3, "lines": ["궁금한 점 있으세요?", "댓글 남겨 주세요"]}],
            "skipped": [{"visual": "V10", "reason": "흐림"}]}
    if "영상자리" in prompt:
        data["parts"][1]["visuals"] = ["V2", "V5", "V6"]
    if "중복사진" in prompt:
        data["parts"][1]["visuals"] = ["V1", "V6"]
    if "나란히한장" in prompt:
        data["parts"][0]["visuals"] = ["V1"]
    if "잘못된자리" in prompt:
        data["parts"][1]["slot"] = 3
    if "권한필드" in prompt:
        data.update(path="사진.jpg", force=True)
    text = "원고입니다.\n```json\n" + json.dumps(data, ensure_ascii=False) + "\n```\n"
    send({"method": "session/update", "params": {"sessionId": "s", "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}}}})
    send({"id": req["id"], "result": {"stopReason": "end_turn"}})


async def test():
    from naver_blog_writer import config
    from naver_blog_writer.core import (
        blog_reader,
        gemini_writer,
        notes,
        post_analysis,
        trends,
        writing_packet,
        writing_sources,
    )
    from naver_blog_writer.core.writing_packet import Brief, Slot
    from naver_blog_writer.core.writing_rules import COPY_RULES, PRIORITIES
    from naver_blog_writer.prompts.write import write
    from naver_blog_writer.server import INSTRUCTIONS, build_server
    from naver_blog_writer.tools import extract_video_frames, find_place, place_reviews
    from naver_blog_writer.tools.gemini_draft_status import gemini_draft_status
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
    # 사진 글인데 직접 입력 자리(fill)가 없으면 확정하지 않습니다. 소제목 구역에 fill이 없으면 경고합니다.
    try:
        brief.model_copy(update={"slots": [Slot(kind="text", hint="동선"), Slot(kind="quote", hint="마무리")]}).model_validate(
            {**brief.model_dump(), "slots": [{"kind": "text", "hint": "동선"}, {"kind": "quote", "hint": "마무리"}]})
    except ValueError as e:
        assert "fill" in str(e), str(e)
    else:
        raise AssertionError("fill 없는 사진 글 자료가 확정됐습니다")
    sec = brief.model_copy(update={"slots": [Slot(kind="text", hint="도입"), Slot(kind="subtitle", hint="1관"), Slot(kind="text", hint="1관 사실"),
                                             Slot(kind="subtitle", hint="2관"), Slot(kind="fill", hint="2관 감상"), Slot(kind="quote", hint="마무리")]})
    assert sec.sections_without_fill() == ["1관"], sec.sections_without_fill()
    # 소제목 자리에 놓은 사진은 소제목 바로 아래로 갑니다('필름카메라 컷' 같은 사진 구역). 글 자리 없는 구역은 fill 경고에서 뺍니다.
    cam = writing_packet.Packet("p", "", (b"x", b"y", b"z"), (Slot(kind="text", hint="본문"), Slot(kind="fill", hint="감상"),
                                Slot(kind="subtitle", hint="필름카메라 컷"), Slot(kind="quote", hint="마무리")), (),
                                (("photo", "/a/IMG_1.jpg"), ("photo", "/a/필름_01.jpg"), ("photo", "/a/필름_02.jpg")), 0)
    out = gemini_writer.assemble(cam, json.dumps({"title": "[여행 기록] 춘천 여행 🚞", "parts": [
        {"slot": 1, "visuals": ["V1"], "lines": ["춘천에 다녀왔어요"]}, {"slot": 2, "lines": ["좋았어요", "또 갈래요"]},
        {"slot": 3, "visuals": ["V2", "V3"], "layout": "slide", "lines": ["필름카메라 컷"]}, {"slot": 4, "lines": ["끝", "댓글 주세요"]}]}))
    assert [b["type"] for b in out["blocks"]] == ["image", "text", "fill", "subtitle", "gallery", "quote"], out["blocks"]
    # 사진만 놓는 text 자리는 글을 비워 두면 사진 블록만 들어갑니다. 사진·배치 설명 줄은 remove_lines로 알려 줍니다.
    bare = gemini_writer.assemble(cam, json.dumps({"title": "[여행 기록] 춘천 여행 🚞", "parts": [
        {"slot": 1, "visuals": ["V1"], "lines": []}, {"slot": 2, "lines": ["좋았어요", "또 갈래요"]},
        {"slot": 3, "visuals": ["V2", "V3"], "layout": "strip", "lines": ["필름카메라 컷"]}, {"slot": 4, "lines": ["필름 사진 2장을 나란히 두었어요", "댓글 주세요"]}]}))
    assert [b["type"] for b in bare["blocks"]] == ["image", "fill", "subtitle", "gallery", "quote"], bare["blocks"]
    assert bare["remove_lines"] == ["필름 사진 2장을 나란히 두었어요"], bare["remove_lines"]
    # 한 자리에 한도보다 많이 놓으면 같은 배치로 고르게 나눕니다(필름 사진 12장 슬라이드 → 6+6, 나란히 4장 → 2+2).
    many = writing_packet.Packet("p", "", (b"x",) * 16, (Slot(kind="text", hint="본문"), Slot(kind="fill", hint="감상"), Slot(kind="subtitle", hint="필름카메라 컷"),
                                 Slot(kind="quote", hint="끝")), (), tuple(("photo", f"/a/f{i}.jpg") for i in range(16)), 0)
    split = gemini_writer.assemble(many, json.dumps({"title": "[여행 기록] 춘천 여행 🚞", "parts": [
        {"slot": 1, "visuals": [f"V{i}" for i in range(1, 5)], "layout": "strip", "lines": ["춘천에 다녀왔어요"]}, {"slot": 2, "lines": ["좋았어요", "또 갈래요"]},
        {"slot": 3, "visuals": [f"V{i}" for i in range(5, 17)], "layout": "slide", "lines": ["필름카메라 컷"]}, {"slot": 4, "lines": ["끝", "댓글 주세요"]}]}))
    assert [(b["type"], len(b.get("paths") or [])) for b in split["blocks"]] == [
        ("gallery", 2), ("gallery", 2), ("text", 0), ("fill", 0), ("subtitle", 0), ("gallery", 6), ("gallery", 6), ("quote", 0)], split["blocks"]
    # Gemini가 콜라주를 골라도 쓰지 않습니다: 2~3장은 나란히, 4장 이상은 슬라이드로 바뀝니다(발행 전 크기 조절 불가).
    col = gemini_writer.assemble(many, json.dumps({"title": "[여행 기록] 춘천 여행 🚞", "parts": [
        {"slot": 1, "visuals": ["V1", "V2", "V3"], "layout": "collage", "lines": []}, {"slot": 2, "lines": ["좋았어요", "또 갈래요"]},
        {"slot": 3, "visuals": [f"V{i}" for i in range(4, 9)], "layout": "collage", "lines": ["필름카메라 컷"]}, {"slot": 4, "lines": ["끝", "댓글 주세요"]}]}))
    two = gemini_writer.assemble(many, json.dumps({"title": "[여행 기록] 춘천 여행 🚞", "parts": [
        {"slot": 1, "visuals": ["V1", "V2"], "layout": "slide", "lines": []}, {"slot": 2, "lines": ["좋았어요", "또 갈래요"]},
        {"slot": 3, "lines": ["필름카메라 컷"]}, {"slot": 4, "lines": ["끝", "댓글 주세요"]}]}))
    assert two["blocks"][0]["layout"] == "strip", two["blocks"][0]   # 2~3장 슬라이드는 나란히로(사용자가 발행 때 바꾼 방식)
    assert [(b["type"], b.get("layout")) for b in col["blocks"]] == [
        ("gallery", "strip"), ("fill", None), ("subtitle", None), ("gallery", "slide"), ("quote", None)], col["blocks"]
    assert brief.model_copy(update={"slots": [Slot(kind="text", hint="본문"), Slot(kind="fill", hint="감상"), Slot(kind="subtitle", hint="필름카메라 컷"),
                                              Slot(kind="quote", hint="끝")]}).sections_without_fill() == []
    # 묶음 내용 점검은 Claude 모드로 합니다(gemini 모드의 prepare는 Gemini 작성을 바로 시작하므로 아래에서 가짜 agy로 확인).
    with patch.object(place_reviews.core, "load_recent_for_overlap", side_effect=AssertionError("본문 캐시 접근")), \
            patch.object(config, "DRAFT_WRITER", "claude"):
        info = await prepare_writing_packet(brief)
        assert info["writer"] == "claude" and "generation_id" not in info
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
    names = {t.name for t in await build_server().list_tools()}
    assert "gemini_draft_status" in names and "generate_gemini_draft" not in names  # Gemini 작성은 Claude가 따로 부르지 않습니다

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

    async def rejected(packet_id, words, blocks=None):
        blocks = blocks or [Block(type="text", text="Claude가 바로 쓴 글이에요")]
        try:
            await write_post("제목", blocks, dry_run=True, packet_id=packet_id)
        except ValueError as e:
            assert words in str(e), str(e)
        else:
            raise AssertionError(f"Gemini 단계 없이 write_post가 통과했습니다({packet_id})")

    # Gemini 모드에서 Claude가 Gemini 단계를 건너뛰면 서버가 거부합니다(2026-11-25 Desktop 실행 재현).
    await rejected(None, "작성자 설정이 gemini")
    await rejected(pid, "기록이 없습니다")
    with patch.object(asyncio, "create_subprocess_exec", fake_exec):
        receipt = await prepare_writing_packet(brief)  # gemini 모드: 자료 확정과 동시에 Gemini 작성 시작
        assert receipt["packet_id"] == pid and receipt["writer"] == "gemini" and receipt["status"] == "running", receipt
        assert (await prepare_writing_packet(brief))["generation_id"] == receipt["generation_id"]
        try:
            await read_writing_packet(pid, 0)
        except ValueError as e:
            assert "Gemini가 씁니다" in str(e)
        else:
            raise AssertionError("Gemini 모드에서 Claude가 묶음을 읽고 직접 쓸 수 있습니다")
        state = await gemini_draft_status(wait_seconds=5)
        assert state["status"] == "ready", state
        draft = state["draft"]
        assert (await prepare_writing_packet(brief))["status"] == "ready" and len(calls) == 1
        kinds = [(b["type"], b.get("layout")) for b in draft["blocks"]]
        assert kinds == [("gallery", "strip"), ("text", None), ("gallery", "slide"), ("fill", None), ("quote", None)], kinds
        assert draft["blocks"][1]["text"] == "1관에 종이학이 있어요\n\n2관은 공사 중이었어요", draft["blocks"][1]
        assert draft["blocks"][0]["paths"] == [str(photo), str(others[0]), str(others[1])], draft["blocks"][0]
        assert draft["photo_layout"] == {"strip": 1, "slide": 1} and draft["unplaced_photos"] == ["V11"] \
            and draft["skipped_photos"] == [{"visual": "V10", "reason": "흐림"}], draft
        for f in (photo, *others):   # 원고를 받은 뒤 dry_run 점검을 위해 사진 파일을 다시 둡니다(묶음은 원본을 다시 읽지 않음)
            Image.new("RGB", (60, 60), "green").save(f)
        blocks = [Block(**b) for b in draft["blocks"]]
        plan = json.loads((await write_post(draft["title"], blocks, dry_run=True, packet_id=pid))[1])
        assert plan["writer"] == "gemini", plan["writer"]
        assert plan["blocks"][3]["topic"] == "전시 감상" and plan["to_fill_before_publish"]["추천_문구_줄"] == 2
        assert plan["photo_layout"] == "나란히 1블록 3장 · 슬라이드 1블록 5장" and plan["layout_preview"][0].startswith("[나란히 3장"), plan["layout_preview"]
        print("PASS  Gemini 단계 건너뛰기 거부 → 작성 자료와 문안 자리 → 접수·중복 방지 → 결과 회수(사진 배치 포함) → dry_run(writer=gemini, 배치 미리보기)")

        for case in ("잘못된자리", "권한필드", "인증실패", "no_images", "no_model", "permission", "영상자리", "중복사진", "나란히한장"):
            feature = case
            r = await prepare_writing_packet(brief.model_copy(update={"request": "시범 자료 " + case}))
            fail_id = r["packet_id"]
            expected_text = writing_packet.get(fail_id).text  # 가짜 agy가 시작되기 전(다음 await 전)에 대조값을 정합니다.
            state = await gemini_draft_status(r["generation_id"], 5)
            assert state["status"] == "failed" and "draft" not in state, (case, state)
            need = {"no_images": "이미지 입력", "no_model": "지정 모델", "permission": "권한 요청", "영상자리": "영상 장면",
                    "중복사진": "두 번", "나란히한장": "2장 이상"}.get(case)
            assert not need or need in state["message"], state
            assert state["packet_id"] == fail_id and "read_writing_packet" in state["fallback"], state
            if case == "잘못된자리":  # 실패 뒤에는 같은 묶음을 모두 읽은 Claude 원고만 받습니다.
                await rejected(fail_id, "read_writing_packet", blocks)
                assert (await prepare_writing_packet(brief.model_copy(update={"request": "시범 자료 " + case})))["status"] == "failed"
                for n in range(r["pages"]):  # 실패 뒤에는 열립니다. 같은 묶음을 다시 확정해도 Gemini에 재요청하지 않습니다.
                    await read_writing_packet(fail_id, n)
                fb = json.loads((await write_post(draft["title"], blocks, dry_run=True, packet_id=fail_id))[1])
                assert fb["writer"].startswith("claude") and "대체" in fb["writer"], fb["writer"]
            if case in ("잘못된자리", "권한필드"):
                saved = Path(state["raw_saved"]).read_text(encoding="utf-8")   # 첫 줄은 실패 이유, 그다음 원문
                assert saved.startswith(f"# 실패 이유: {state['message']}\n원고입니다."), saved[:120]
            if case == "권한필드":
                assert "Extra inputs" in state["message"] or "path" in state["message"], state["message"]
        with patch.object(config, "AGY_PATH", str(config.HOME / "no-agy")):  # agy가 없어도 failed가 기록되어 대체 작성으로 넘어감
            r = await prepare_writing_packet(brief.model_copy(update={"request": "시범 자료 agy 없음"}))
            state = await gemini_draft_status(r["generation_id"], 5)
            assert state["status"] == "failed" and "agy를 찾을 수 없습니다" in state["message"], state
            await read_writing_packet(r["packet_id"], 0)
        assert len(calls) == 10 and not config.HISTORY_PATH.exists()
        print("PASS  11개 JPEG와 텍스트 해시·순서 일치, 자리/필드 오류·CLI 종료·모델/이미지 미지원·권한 요청 시 중단")

        with patch.object(config, "DRAFT_WRITER", "claude"):
            try:
                await gemini_writer.start(pid)
            except ValueError as e:
                assert "꺼져" in str(e)
            else:
                raise AssertionError("Claude 모드에서 Gemini가 켜졌습니다")
            again = await prepare_writing_packet(brief)  # Claude 모드: Gemini를 시작하지 않고 바로 읽습니다.
            assert again["writer"] == "claude" and "generation_id" not in again
            assert (await read_writing_packet(again["packet_id"], 0))[0] == prompt
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
        assert len(calls) == 10
        print("PASS  Claude 모드, 다른 글 읽기 설정, 경로 전송 차단")

    # 장수 제한은 없습니다(모음집 93장 등). Gemini 모드에서만 요청 크기(12MiB)를 넘으면 아무것도 보내지 않고 멈춥니다.
    many_ref = (await get_style_guide(refresh=False))["writing_ref"]
    many = writing_sources.collection(many_ref)
    labels = many.previews([(media_root / f"p{n}.jpg", seen) for n in range(120)], "photo")
    assert len(many.images) == 120 and labels[-1].endswith("/V120"), labels[-1]
    big = brief.model_copy(update={"guide_ref": many_ref, "visual_count": 120, "place_refs": [], "review_refs": []})
    with patch.object(writing_packet, "GEMINI_IMAGE_BYTES", 1000):
        try:
            writing_packet.freeze(big)
        except ValueError as e:
            assert "Gemini 요청" in str(e) and "빼지 않았고" in str(e), str(e)
        else:
            raise AssertionError("Gemini 요청 크기 초과가 조용히 넘어갔습니다.")
        with patch.object(config, "DRAFT_WRITER", "claude"):
            assert len(writing_packet.freeze(big).images) == 120  # Claude 모드에는 크기 제한을 두지 않습니다
    fresh_ref = (await get_style_guide(refresh=False))["writing_ref"]
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
    with patch.object(config, "DRAFT_WRITER", "claude"):  # 묶음 모양만 확인합니다(gemini 모드면 Gemini 작성이 시작됨).
        text_info = await prepare_writing_packet(brief.model_copy(update={"guide_ref": fresh_ref, "visual_count": 0,
                                                                           "place_refs": [], "review_refs": []}))
        assert text_info["images"] == 0 and text_info["pages"] == 1
        assert len(await read_writing_packet(text_info["packet_id"])) == 1
    print("PASS  사진 장수 제한 없음(120장), Gemini 요청 크기 초과 시 부분 전송 없이 중단, 사진 없는 글은 텍스트 한 쪽만 확정")


if __name__ == "__main__":
    if sys.argv[1:2] == ["--fake-agy"]:
        fake_agy()
    else:
        with tempfile.TemporaryDirectory(prefix="nbw_gemini_") as home:
            os.environ.update(HOME=home, USERPROFILE=home, NBW_NO_DOTENV="1", NBW_HOME=home, NAVER_BLOG_ID="testblog", MEDIA_ROOTS=home,
                              READ_OTHER_BLOGS="true", DRAFT_WRITER="gemini", AGY_PATH=sys.executable, AGY_MODEL="gemini-test")
            asyncio.run(test())
