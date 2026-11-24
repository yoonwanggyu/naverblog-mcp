"""모델 호출은 백그라운드에서 한 번만 합니다. 원고 회수와 네이버 저장은 별도입니다."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from dataclasses import dataclass
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .. import config
from . import agy_acp, writing_packet
from .writing_packet import LOCAL_OR_URL, Packet, Short


class Part(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    slot: int
    lines: list[Short] = Field(min_length=1, max_length=12)


class Copy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: Short
    parts: list[Part] = Field(min_length=1, max_length=100)


def assemble(packet: Packet, response: str) -> dict:
    # ACP의 출력은 텍스트입니다. 원고 한 개만 허용하고 나머지 필드/누락/잘린 JSON은 거부합니다.
    fence = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n```", response, re.S)
    try:
        copy = Copy.model_validate(json.loads(fence.group(1) if fence else response))
    except (json.JSONDecodeError, ValidationError):
        raise ValueError("Gemini 응답에 올바른 제목·문장 목록이 없습니다.") from None
    if [p.slot for p in copy.parts] != list(range(1, len(packet.slots) + 1)):
        raise ValueError("Gemini 응답의 문안 자리 개수나 순서가 다릅니다.")
    blocks = []
    for slot, part in zip(packet.slots, copy.parts, strict=True):
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

    async def run() -> dict:
        try:
            response = await agy_acp.run(exe, config.AGY_MODEL, packet)
            return {"status": "ready", "draft": assemble(packet, response), "model": config.AGY_MODEL, "packet_id": packet_id}
        except asyncio.TimeoutError:
            message = "Gemini 문안 생성 시간이 초과됐습니다. 임시저장은 시작하지 않았습니다."
        except ValueError as exc:
            message = str(exc) if type(exc) is ValueError else "Gemini 응답 형식을 확인하지 못했습니다."
        except Exception:
            message = "agy 실행 또는 응답 읽기 실패. 터미널의 agy 로그인과 AGY_PATH를 확인하세요."
        return {"status": "failed", "message": message}

    _latest = Generation(uuid4().hex, packet_id, asyncio.create_task(run()))
    return {"generation_id": _latest.generation_id, "status": "running"}


async def status(generation_id: str | None, wait_seconds: int) -> dict:
    job = _latest
    if not job or (generation_id is not None and generation_id != job.generation_id):
        raise ValueError("현재 서버에 해당 Gemini 생성 기록이 없습니다.")
    if not 0 <= wait_seconds <= 10:
        raise ValueError("wait_seconds는 0~10초입니다.")
    await asyncio.wait({job.task}, timeout=wait_seconds)
    return {"generation_id": job.generation_id, **(job.task.result() if job.task.done() else {"status": "running"})}
