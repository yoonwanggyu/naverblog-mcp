"""실제 stdio MCP 연결로 느린 임시저장 접수·조회·중복·재시작을 검증합니다. 네이버 접속 없음.

    NBW_TEST_SAVE_DELAY_S=81 BROWSER_CHANNEL=chrome uv run python tests/draft_job_test.py
"""

import asyncio
import functools
import http.server
import json
import os
import sys
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = Path(__file__).resolve()


def serve():
    from naver_blog_writer import config, server
    from naver_blog_writer.core import blog_reader, editor, selectors

    assert os.environ["NBW_NO_DOTENV"] == "1" and config.BLOG_ID == "testblog"
    config.ensure_dirs()
    selectors.WRITE_URLS[:] = [f"http://127.0.0.1:{int(sys.argv[2])}/index.html?{{blog_id}}"]
    blog_reader.save_cache({"synced_ts": time.time(), "posts": []})
    real_save = editor.save_draft

    async def slow_save(frame):
        # 지연은 이 테스트 서버에만 주입합니다. 실제 저장 클릭/완료 신호 확인 경로는 그대로입니다.
        await asyncio.sleep(float(os.environ["NBW_TEST_SAVE_DELAY_S"]))
        res = await real_save(frame)
        assert await frame.evaluate("!!window.__saved && !window.__publishClicked")
        with (config.HOME / "mock_saves").open("a", encoding="utf-8") as f:
            f.write("saved\n")
        return res

    editor.save_draft = slow_save
    server.main()


@asynccontextmanager
async def session(home: Path, port: int, delay: float):
    env = {"NBW_NO_DOTENV": "1", "NBW_HOME": str(home), "MEDIA_ROOTS": str(home), "NAVER_BLOG_ID": "testblog",
           "HEADLESS": "true", "DAILY_DRAFT_LIMIT": "2", "READ_OTHER_BLOGS": "false", "NBW_TEST_SAVE_DELAY_S": str(delay),
           "BROWSER_CHANNEL": os.environ.get("BROWSER_CHANNEL", ""), "PYTHONIOENCODING": "utf-8"}
    params = StdioServerParameters(command=sys.executable, args=[str(HERE), "--server", str(port)], env=env)
    async with stdio_client(params) as streams, ClientSession(*streams, read_timeout_seconds=timedelta(seconds=15)) as client:
        await client.initialize()
        yield client


async def call(client: ClientSession, tool: str, args: dict) -> dict:
    res = await client.call_tool(tool, args)
    assert not res.isError, res.content
    return json.loads(res.content[0].text)


async def main(home: Path, port: int):
    delay = float(os.environ.get("NBW_TEST_SAVE_DELAY_S", 2))
    args = {"title": "한식 1탄 테스트", "blocks": [{"type": "text", "text": "테스트 본문"}], "review_seconds": 0}
    async with session(home, port, delay) as client:
        start = time.monotonic()
        receipt = await call(client, "write_post", args)
        accepted = time.monotonic() - start
        job_id = receipt["job_id"]
        assert receipt["status"] == "running" and "result" not in receipt and accepted < 5
        latest = await call(client, "draft_status", {})  # 첫 응답을 놓친 경우
        assert latest["job_id"] == job_id and latest["title"] == args["title"]
        dup = await call(client, "write_post", {**args, "force": True})
        assert dup["job_id"] == job_id
        busy = await client.call_tool("write_post", {**args, "title": "다른 작업"})
        assert busy.isError and job_id in busy.content[0].text
        polls = 0
        while time.monotonic() - start < delay + 45:
            state = await call(client, "draft_status", {"job_id": job_id, "wait_seconds": 3})
            polls += 1
            if state["status"] != "running":
                break
        elapsed = time.monotonic() - start
        assert state["status"] == "saved" and state["result"]["saved_confirmed"] and state["result"]["verify"]["ok"], state
        assert elapsed >= delay and polls >= 2
        print(f"PASS  stdio: 접수 {accepted:.3f}초, 전체 {elapsed:.1f}초, 조회 {polls}회, 같은 요청은 같은 ID, 다른 요청은 차단")

    async with session(home, port, delay) as client:
        recovered = await call(client, "draft_status", {})
        assert recovered["job_id"] == job_id and recovered["status"] == "saved" and recovered["result"]["save_signal"]
        assert recovered["result"]["verify"]["ok"]
        dup = await client.call_tool("write_post", args)
        assert dup.isError and "같은 내용" in dup.content[0].text
        assert (home / "mock_saves").read_text().splitlines() == ["saved"]
        print("PASS  서버 재시작 후 최근 작업·저장 신호 복구, 중복 차단, 모의 저장은 한 번")

    async with session(home, port, 60) as client:
        other = {**args, "title": "중단된 저장 확인"}
        interrupted_id = (await call(client, "write_post", other))["job_id"]
        for _ in range(20):
            state = await call(client, "draft_status", {"job_id": interrupted_id, "wait_seconds": 1})
            if state["step"] == "임시저장":
                break
        assert state["status"] == "running" and state["step"] == "임시저장"
        # 저장 결과가 오기 전에 stdio 서버 종료. 다음 서버는 확인할 수 있는 사실만 반환해야 합니다.
    async with session(home, port, 0) as client:
        unknown_save = await call(client, "draft_status", {"job_id": interrupted_id})
        assert unknown_save["status"] == "unconfirmed" and not unknown_save["result"]["ok"]
        limited = await client.call_tool("write_post", {**other, "force": True})
        assert limited.isError and "하루 한도" in limited.content[0].text
        assert (home / "mock_saves").read_text().splitlines() == ["saved"]
        print("PASS  저장 단계에서 서버 종료 후 미확인 복구, 하루 한도 적용, 자동 재시도 없음")


if __name__ == "__main__":
    if sys.argv[1:2] == ["--server"]:
        serve()
    else:
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(HERE.parent / "mock_editor"))
        with tempfile.TemporaryDirectory(prefix="nbw_job_") as tmp, http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler) as site:
            threading.Thread(target=site.serve_forever, daemon=True).start()
            try:
                asyncio.run(main(Path(tmp), site.server_port))
            finally:
                site.shutdown()
