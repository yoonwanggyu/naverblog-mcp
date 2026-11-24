"""명시적으로만 실행: 가상 메뉴판 1장을 실제 Gemini에 보내 OCR → 문안 → dry_run을 확인합니다.

    uv run python tests/gemini_image_live_test.py --live
"""

import asyncio
import base64
import hashlib
import json
import secrets
import sys
import tempfile
import time
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from PIL import Image, ImageDraw, ImageFont


async def main(root: Path):
    from naver_blog_writer import config
    from naver_blog_writer.core.gemini_writer import executable

    assert config.DRAFT_WRITER == "gemini" and config.AGY_MODEL == "gemini-4-neon-high", "Gemini 4 Neon High 설정을 확인하세요."
    marker = "".join(secrets.choice("ABCDEFGHJKLMNPRSTUVWXYZ23456789") for _ in range(7))
    image = Image.new("RGB", (880, 400), "white")
    draw = ImageDraw.Draw(image)
    draw.text((40, 55), "MENU   /   SAMPLE CODE", fill="black", font=ImageFont.load_default(size=44))
    draw.text((45, 160), marker, fill="black", font=ImageFont.load_default(size=108))
    source = root / "menu.jpg"
    image.save(source, quality=94)
    params = StdioServerParameters(command=sys.executable, args=["-m", "naver_blog_writer.server"], env={
        "NBW_NO_DOTENV": "1", "NBW_HOME": str(root / "home"), "MEDIA_ROOTS": str(root), "STYLE_GUIDE_PATH": "",
        "READ_OTHER_BLOGS": "false", "NAVER_BLOG_ID": "", "DRAFT_WRITER": "gemini", "AGY_MODEL": config.AGY_MODEL,
        "AGY_PATH": executable(), "PYTHONIOENCODING": "utf-8"})
    async with stdio_client(params) as streams, ClientSession(*streams, read_timeout_seconds=timedelta(seconds=25)) as client:
        await client.initialize()

        async def call(name, **args):
            result = await client.call_tool(name, args)
            assert not result.isError, result.content[0].text[:400]
            return result.content

        async def obj(name, **args):
            return json.loads((await call(name, **args))[0].text)

        guide = await obj("get_style_guide", topic="카페", refresh=False)
        ref = guide["writing_ref"]
        await obj("scan_media", folder=str(root), guide_ref=ref)
        seen, = (b for b in await call("view_images", paths=[str(source)], guide_ref=ref) if b.type == "image")
        brief = dict(guide_ref=ref, request="가상 메뉴판의 SAMPLE CODE를 사진에서 읽어 본문에 영어·숫자 그대로 적어 줘.",
                     facts=["가상 전시용 메뉴판"], user_updates=[], web_findings=[], visual_count=1,
                     slots=[{"kind": "text", "hint": "사진 속 코드를 정확히 소개"}, {"kind": "quote", "hint": "두 줄 마무리"}])
        info = await obj("prepare_writing_packet", brief=brief)
        pid = info["packet_id"]
        text = (await call("read_writing_packet", packet_id=pid, page=0))[0].text
        assert marker not in text and str(root) not in text
        copy, = await call("read_writing_packet", packet_id=pid, page=1)
        assert copy.data == seen.data
        start = time.monotonic()
        receipt = await obj("generate_gemini_draft", packet_id=pid)
        accepted = time.monotonic() - start
        state = receipt
        while state["status"] == "running":
            state = await obj("gemini_draft_status", generation_id=receipt["generation_id"], wait_seconds=10)
        elapsed = time.monotonic() - start
        assert state["status"] == "ready", state
        assert state["packet_id"] == pid and state["model"] == config.AGY_MODEL
        d = state["draft"]
        body = d["blocks"][0]["text"]
        print(f"model={state['model']} accepted={accepted:.3f}s total={elapsed:.1f}s\n이미지 전용 표식={marker}\n반환 본문={body}")
        assert marker in body, "모델이 사진의 표식을 정확히 읽지 못했습니다."
        plan = await call("write_post", title=d["title"], blocks=d["blocks"], dry_run=True)
        assert marker in plan[1].text and not (root / "home/post_history.json").exists()
        print("PASS  실제 ACP 이미지 판독, MCP JPEG SHA256=" + hashlib.sha256(base64.b64decode(copy.data)).hexdigest() + ", dry_run, 실저장 0회")


if __name__ == "__main__":
    if sys.argv[1:] != ["--live"]:
        raise SystemExit("실제 모델을 호출하려면 --live가 필요합니다.")
    with tempfile.TemporaryDirectory(prefix="nbw_live_vision_") as tmp:
        asyncio.run(main(Path(tmp)))
