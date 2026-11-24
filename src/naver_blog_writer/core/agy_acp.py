"""단일 ACP 세션에서 확정된 글쓰기 자료 한 턴만 전달합니다. 도구 권한은 제공하지 않습니다."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import tempfile
from pathlib import Path

from .writing_packet import Packet

_SETTINGS = {"toolPermission": "strict", "permissions": {"deny": [f"{a}(*)" for a in
             ("read_file", "write_file", "command", "unsandboxed", "read_url", "execute_url", "mcp")]}}


async def turn(proc: asyncio.subprocess.Process, cwd: str, model: str, packet: Packet) -> str:
    assert proc.stdin and proc.stdout
    output: list[str] = []
    size = 0
    session_id = None

    async def send(obj: dict) -> None:
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", **obj}, ensure_ascii=False).encode() + b"\n")
        await proc.stdin.drain()

    async def rpc(seq: int, method: str, **params) -> dict:
        nonlocal size
        await send({"id": seq, "method": method, "params": params})
        while line := await proc.stdout.readline():
            msg = json.loads(line)
            if "method" in msg and "id" in msg:
                if msg["method"] == "session/request_permission":
                    await send({"id": msg["id"], "result": {"outcome": {"outcome": "cancelled"}}})
                else:
                    await send({"id": msg["id"], "error": {"code": -32601, "message": "Unsupported"}})
                raise ValueError("문안 생성 중 도구·권한 요청을 거부했습니다.")
            if msg.get("method") == "session/update" and msg["params"].get("sessionId") == session_id:
                update = msg["params"]["update"]
                kind = update.get("sessionUpdate")
                if kind in ("tool_call", "tool_call_update"):
                    raise ValueError("Gemini가 글쓰기 외 도구를 요청해 중단했습니다.")
                if method == "session/prompt" and kind == "agent_message_chunk":
                    content = update["content"]
                    if content.get("type") != "text":
                        raise ValueError("Gemini가 텍스트 외의 결과를 반환했습니다.")
                    size += len(content["text"])
                    if size > 256_000:
                        raise ValueError("Gemini 응답이 원고 크기 한도를 넘었습니다.")
                    output.append(content["text"])
            if "method" not in msg and msg.get("id") == seq:
                if "error" in msg:
                    raise ValueError(f"Gemini ACP {method} 실패 (코드 {msg['error'].get('code')}). agy 로그인·모델·한도를 확인하세요.")
                return msg["result"]
        raise ValueError("Gemini가 완료 응답 전에 연결을 닫았습니다.")

    init = await rpc(1, "initialize", protocolVersion=1, clientCapabilities={})
    if init.get("protocolVersion") != 1 or (packet.images and not init.get("agentCapabilities", {}).get("promptCapabilities", {}).get("image")):
        raise ValueError("설치한 agy가 필요한 ACP/이미지 입력을 지원하지 않습니다. 이미지를 생략하지 않습니다.")
    session = await rpc(2, "session/new", cwd=cwd, mcpServers=[])
    session_id = session["sessionId"]
    if model not in {m["modelId"] for m in session.get("models", {}).get("availableModels", [])}:
        raise ValueError(f"이번 ACP 세션에서 지정 모델 {model}을 사용할 수 없습니다.")
    await rpc(3, "session/set_model", sessionId=session_id, modelId=model)
    prompt = [{"type": "text", "text": packet.text}]
    prompt.extend({"type": "image", "mimeType": "image/jpeg", "data": base64.b64encode(b).decode("ascii")} for b in packet.images)
    result = await rpc(4, "session/prompt", sessionId=session_id, prompt=prompt)
    if result.get("stopReason") != "end_turn" or not output:
        raise ValueError("Gemini 문안이 완성되지 않았습니다(중단·출력 한도 또는 빈 응답).")
    return "".join(output).strip()


async def run(exe: str, model: str, packet: Packet) -> str:
    env = {k: v for k, v in os.environ.items() if k.upper() in {
        "PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR", "TMP", "TEMP", "SYSTEMROOT", "USERPROFILE",
        "LOCALAPPDATA", "APPDATA", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"}}
    # ACP에는 --settings가 없습니다. --cli_dir_name은 CLI 데이터 폴더 아래의 전용 설정 디렉터리를 고릅니다.
    # 로그인은 기존 OAuth 사용, 사용자 settings.json을 읽거나 변경하지 않습니다.
    parent = Path.home() / ".gemini" / "antigravity-cli"
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="nbw_copy_") as cwd, tempfile.TemporaryDirectory(dir=parent, prefix="nbw-") as settings:
        policy_dir = Path(settings)
        (policy_dir / "settings.json").write_text(json.dumps(_SETTINGS), encoding="utf-8")
        proc = await asyncio.create_subprocess_exec(exe, "acp", "--stdio", "--sandbox", "--disable-telemetry",
                    "--include-default-customizations=false", "--cli_dir_name", policy_dir.name, "--log-file", str(policy_dir / "acp.log"),
                    cwd=cwd, env=env, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL, limit=2**20)
        try:
            return await asyncio.wait_for(turn(proc, cwd, model, packet), timeout=250)
        finally:
            if proc.stdin:
                proc.stdin.close()
            try:
                await asyncio.wait_for(proc.communicate(), timeout=2)
            except (asyncio.TimeoutError, BrokenPipeError, ConnectionResetError):
                if proc.returncode is None:
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                await proc.communicate()
