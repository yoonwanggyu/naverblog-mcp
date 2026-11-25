"""모델 호출은 백그라운드에서 한 번만 합니다. 원고 회수와 네이버 저장은 별도입니다."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
from dataclasses import dataclass
from typing import Annotated
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from .. import config
from . import agy_acp, writing_packet
from .writing_packet import LOCAL_OR_URL, Packet, Short

# 빈 문자열 줄은 문단 사이 빈 줄입니다(가이드의 '생각이 바뀌는 곳에 빈 줄'). 실제 Gemini 원고에서 확인했습니다.
Line = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]
MAX_TEXT_LINES = 60


class Part(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    slot: int
    lines: list[Line] = Field(min_length=1, max_length=MAX_TEXT_LINES)


class Copy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: Short
    parts: list[Part] = Field(min_length=1, max_length=100)


def _json_object(response: str) -> object:
    """응답에서 JSON 객체 하나를 꺼냅니다. 앞뒤 설명 문장이나 ```json 울타리는 허용하고, 객체가 둘 이상이면 거부합니다."""
    text = response.strip()
    fence = re.search(r"```(?:json)?\s*\n(.*?)\n?```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    if start < 0:
        raise ValueError("Gemini 응답에 JSON 원고가 없습니다.")
    try:
        data, end = json.JSONDecoder().raw_decode(text, start)
    except json.JSONDecodeError as e:
        raise ValueError(f"Gemini 응답의 JSON이 깨졌습니다({e.msg}, {e.pos}번째 글자). 응답이 잘렸을 수 있습니다.") from None
    if "{" in text[end:]:
        raise ValueError("Gemini 응답에 원고가 둘 이상 있습니다.")
    return data


def _clean_lines(kind: str, lines: list[str]) -> list[str]:
    """text는 문단 사이 빈 줄을 하나로 모으고 앞뒤 빈 줄을 지웁니다. 나머지 자리는 빈 줄을 쓰지 않으므로 뺍니다."""
    if kind != "text":
        return [ln for ln in lines if ln]
    out: list[str] = []
    for ln in lines:
        if ln or (out and out[-1]):
            out.append(ln)
    while out and not out[-1]:
        out.pop()
    return out


def assemble(packet: Packet, response: str) -> dict:
    # ACP의 출력은 텍스트입니다. 원고 한 개만 허용하고 나머지 필드/누락/잘린 JSON은 거부합니다.
    data = _json_object(response)
    try:
        copy = Copy.model_validate(data)
    except ValidationError as e:
        where = "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()[:3])
        raise ValueError(f"Gemini 원고 형식이 규칙과 다릅니다({where}).") from None
    if [p.slot for p in copy.parts] != list(range(1, len(packet.slots) + 1)):
        raise ValueError("Gemini 응답의 문안 자리 개수나 순서가 다릅니다.")
    blocks = []
    for slot, part in zip(packet.slots, copy.parts, strict=True):
        part.lines = _clean_lines(slot.kind, part.lines)
        if not part.lines:
            raise ValueError(f"Gemini 원고의 {part.slot}번 자리가 비어 있습니다.")
        if any("\n" in line or "\r" in line for line in part.lines) or LOCAL_OR_URL.search(" ".join(part.lines)):
            raise ValueError("Gemini 문안에는 한 줄씩, 경로·URL 없이 작성해야 합니다.")
        if slot.kind == "fill":
            if not 2 <= len(part.lines) <= 3:
                raise ValueError("직접 입력 자리에는 추천 문구 2~3개가 필요합니다.")
            blocks.append({"type": "fill", "topic": slot.hint, "suggestions": part.lines})
        else:
            if (slot.kind == "subtitle" and len(part.lines) != 1) or (slot.kind == "quote" and not 2 <= len(part.lines) <= 4):
                raise ValueError("Gemini 소제목은 한 줄, 마무리 상자는 2~4줄이어야 합니다.")
            blocks.append({"type": slot.kind, "text": "\n".join(part.lines)})
    if LOCAL_OR_URL.search(copy.title) or "\n" in copy.title or "\r" in copy.title:
        raise ValueError("Gemini 제목에 경로·링크 또는 줄바꿈이 있습니다.")
    return {"title": copy.title, "blocks": blocks}


def executable() -> str:
    exe = shutil.which(config.AGY_PATH)
    if not exe:
        raise ValueError("agy를 찾을 수 없습니다. AGY_PATH에 실행 파일의 절대경로를 설정하세요.")
    if not re.fullmatch(r"gemini-[a-zA-Z0-9.-]+", config.AGY_MODEL):
        raise ValueError("AGY_MODEL에는 agy models에 표시되는 Gemini 모델 ID를 설정하세요.")
    return os.path.abspath(exe)


FALLBACK = ("Gemini에 다시 요청하지 않습니다. 사용자에게 실패 이유를 한 줄로 알린 뒤, 같은 packet_id를 "
            "read_writing_packet(packet_id, page=0..pages-1)로 모두 읽고 Claude가 직접 씁니다. 이후 점검과 임시저장 단계는 같습니다.")
FAILED_DIR_KEEP = 5


def _keep_failed(generation_id: str, response: str):
    """형식 오류 원인을 확인할 수 있게 실패한 원문을 로컬에만 남깁니다(최근 5개). 외부로 보내지 않습니다."""
    folder = config.HOME / "gemini_failed"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{time.strftime('%Y%m%d_%H%M%S')}_{generation_id[:8]}.txt"
    path.write_text(response, encoding="utf-8")
    for old in sorted(folder.glob("*.txt"))[:-FAILED_DIR_KEEP]:
        old.unlink(missing_ok=True)
    return path


@dataclass
class Generation:
    generation_id: str
    packet_id: str
    task: asyncio.Task


_latest: Generation | None = None


async def start(packet_id: str) -> dict:
    global _latest
    if config.DRAFT_WRITER != "gemini":
        raise ValueError("Gemini 글쓰기가 꺼져 있습니다(DRAFT_WRITER=claude).")
    packet = writing_packet.get(packet_id)
    exe = executable()
    if _latest and not _latest.task.done():
        if _latest.packet_id != packet_id:
            raise ValueError("다른 Gemini 문안을 생성 중입니다. gemini_draft_status로 먼저 확인하세요.")
        return {"generation_id": _latest.generation_id, "status": "running"}
    if _latest and _latest.packet_id == packet_id and _latest.task.result().get("status") == "ready":
        return {"generation_id": _latest.generation_id, "status": "ready"}

    generation_id = uuid4().hex

    async def run() -> dict:
        response: str | None = None
        try:
            response = await agy_acp.run(exe, config.AGY_MODEL, packet)
            return {"status": "ready", "draft": assemble(packet, response), "model": config.AGY_MODEL, "packet_id": packet_id}
        except asyncio.TimeoutError:
            message = "Gemini 문안 생성 시간이 초과됐습니다. 임시저장은 시작하지 않았습니다."
        except ValueError as exc:
            message = str(exc) if type(exc) is ValueError else "Gemini 응답 형식을 확인하지 못했습니다."
        except Exception:
            message = "agy 실행 또는 응답 읽기 실패. 터미널의 agy 로그인과 AGY_PATH를 확인하세요."
        out = {"status": "failed", "message": message, "packet_id": packet_id, "fallback": FALLBACK}
        if response is not None:
            out["raw_saved"] = str(_keep_failed(generation_id, response))
        return out

    _latest = Generation(generation_id, packet_id, asyncio.create_task(run()))
    return {"generation_id": _latest.generation_id, "status": "running"}


async def status(generation_id: str | None, wait_seconds: int) -> dict:
    job = _latest
    if not job or (generation_id is not None and generation_id != job.generation_id):
        raise ValueError("현재 서버에 해당 Gemini 생성 기록이 없습니다.")
    if not 0 <= wait_seconds <= 10:
        raise ValueError("wait_seconds는 0~10초입니다.")
    await asyncio.wait({job.task}, timeout=wait_seconds)
    return {"generation_id": job.generation_id, **(job.task.result() if job.task.done() else {"status": "running"})}
