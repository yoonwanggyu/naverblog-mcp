"""모델 호출은 백그라운드에서 한 번만 합니다. 원고 회수와 네이버 저장은 별도입니다."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from .. import config
from . import agy_acp, formatting, media, writing_packet
from .writing_packet import LOCAL_OR_URL, Packet, Short

# 빈 문자열 줄은 문단 사이 빈 줄입니다(가이드의 '생각이 바뀌는 곳에 빈 줄'). 실제 Gemini 원고에서 확인했습니다.
Line = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]
MAX_TEXT_LINES = 60


Visual = Annotated[str, StringConstraints(pattern=r"^V[1-9]\d{0,3}$")]


log = logging.getLogger(__name__)


class Part(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    slot: int
    visuals: list[Visual] = Field(default_factory=list, max_length=60)   # 이 글 바로 위에 놓을 사진(V번호)
    layout: Literal["single", "strip", "collage", "slide"] | None = None
    lines: list[Line] = Field(max_length=MAX_TEXT_LINES)   # 사진만 놓는 text 자리는 빈 목록(억지 사진 설명을 막음)


class Skip(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    visual: Visual
    reason: Short


class Copy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: Short
    parts: list[Part] = Field(min_length=1, max_length=300)
    skipped: list[Skip] = Field(default_factory=list, max_length=100)


def _photo_blocks(packet: Packet, part: Part, kind: str, used: set[int]) -> list[dict]:
    """원고의 사진 배치(visuals, layout)를 검사하고 image/gallery 블록으로 바꿉니다. 경로는 서버가 V번호로 찾아 넣습니다."""
    if not part.visuals:
        if part.layout:
            raise ValueError(f"Gemini 원고의 {part.slot}번 자리에 layout만 있고 사진(visuals)이 없습니다.")
        return []
    if kind not in ("text", "fill", "subtitle"):
        raise ValueError(f"Gemini 원고의 {part.slot}번 자리({kind})에는 사진을 놓을 수 없습니다. text, fill, subtitle 자리에만 놓습니다.")
    idx = [int(v[1:]) - 1 for v in part.visuals]
    for v, i in zip(part.visuals, idx, strict=True):
        if not 0 <= i < len(packet.visuals):
            raise ValueError(f"Gemini 원고의 {part.slot}번 자리에 없는 사진 번호 {v}가 있습니다.")
        if packet.visuals[i][0] != "photo":
            raise ValueError(f"Gemini 원고의 {v}는 영상 장면입니다. 영상은 사진 배치(visuals)에 넣지 않습니다.")
        if i in used:
            raise ValueError(f"Gemini 원고에 같은 사진 {v}가 두 번 놓였습니다.")
        used.add(i)
    paths = [packet.visuals[i][1] for i in idx]
    layout = part.layout or ("single" if len(paths) == 1 else None)
    if layout is None:
        raise ValueError(f"Gemini 원고의 {part.slot}번 자리에 사진이 {len(paths)}장인데 layout이 없습니다.")
    if layout == "single":
        return [{"type": "image", "path": p} for p in paths]
    if len(paths) < 2:
        raise ValueError(f"Gemini 원고의 {part.slot}번 자리: {layout}은 사진 2장 이상입니다({len(paths)}장).")
    if layout == "collage":   # 발행 전에 크기를 조절할 수 없어 쓰지 않습니다(사용자 요청). 2~3장은 나란히, 4장 이상은 슬라이드로 바꿉니다.
        layout = "strip" if len(paths) <= media.STRIP_MAX else "slide"
    if layout == "slide" and len(paths) <= media.STRIP_MAX:   # 2~3장은 나란히(사용자가 발행 때 2~3장 묶음을 모두 나란히로 바꿈)
        layout = "strip"
    # 한 자리에 한도(나란히 3장, 콜라주·슬라이드 10장)보다 많이 놓으면 같은 배치로 고르게 나눕니다(예: 필름 사진 29장 → 슬라이드 10·10·9).
    most = media.STRIP_MAX if layout == "strip" else media.GROUP_MAX
    return [{"type": "gallery", "layout": layout, "paths": chunk} for chunk in media.split_even(paths, most)]


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
    used: set[int] = set()
    for slot, part in zip(packet.slots, copy.parts, strict=True):
        photo = _photo_blocks(packet, part, slot.kind, used)
        if slot.kind != "subtitle":   # text·fill은 사진 다음에 글, subtitle은 제목 아래에 사진('필름카메라 컷' 같은 사진 구역)
            blocks += photo
        part.lines = _clean_lines(slot.kind, part.lines)
        if not part.lines and slot.kind == "text" and photo:
            continue   # 사진만 놓는 자리: 글 블록을 만들지 않습니다
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
            if slot.kind == "subtitle":
                blocks += photo
    if LOCAL_OR_URL.search(copy.title) or "\n" in copy.title or "\r" in copy.title:
        raise ValueError("Gemini 제목에 경로·링크 또는 줄바꿈이 있습니다.")
    photos = [i for i, (kind, _p) in enumerate(packet.visuals) if kind == "photo"]
    skipped = {int(s.visual[1:]) - 1: s.reason for s in copy.skipped}
    unplaced = [f"V{i + 1}" for i in photos if i not in used and i not in skipped]
    return {"title": copy.title, "blocks": blocks,
            "photo_layout": _layout_summary(blocks),
            "skipped_photos": [{"visual": f"V{i + 1}", "reason": r} for i, r in skipped.items() if i not in used],
            "unplaced_photos": unplaced,
            # 사진·배치 설명이나 작업 과정이 새어 나온 줄. Claude가 지운 뒤 dry_run합니다(남으면 임시저장 거부).
            "remove_lines": formatting.meta_lines(blocks)}


def _layout_summary(blocks: list[dict]) -> dict[str, int]:
    """원고가 고른 배치: 배치별 블록 수."""
    out: dict[str, int] = {}
    for b in blocks:
        key = "single" if b["type"] == "image" else b.get("layout") if b["type"] == "gallery" else None
        if key:
            out[key] = out.get(key, 0) + 1
    return out


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


def _keep_failed(generation_id: str, response: str, message: str = ""):
    """실패 이유와 원문을 로컬에만 남깁니다(최근 5개). 외부로 보내지 않습니다. 첫 줄이 실패 이유입니다."""
    folder = config.HOME / "gemini_failed"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{time.strftime('%Y%m%d_%H%M%S')}_{generation_id[:8]}.txt"
    path.write_text(f"# 실패 이유: {message}\n{response}", encoding="utf-8")
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
    if _latest and not _latest.task.done():
        if _latest.packet_id != packet_id:
            raise ValueError("다른 Gemini 문안을 생성 중입니다. gemini_draft_status로 먼저 확인하세요.")
        return {"generation_id": _latest.generation_id, "status": "running"}
    if _latest and _latest.packet_id == packet_id:  # 같은 묶음의 결과(ready/failed)는 그대로 돌려줍니다. 실패도 재요청하지 않습니다.
        return {"generation_id": _latest.generation_id, "status": _latest.task.result().get("status")}

    generation_id = uuid4().hex

    async def run() -> dict:
        response: str | None = None
        try:
            exe = executable()  # agy가 없어도 failed 기록을 남겨 Claude 대체 작성으로 넘어갈 수 있게 합니다.
            response = await agy_acp.run(exe, config.AGY_MODEL, packet)
            return {"status": "ready", "draft": assemble(packet, response), "model": config.AGY_MODEL, "packet_id": packet_id}
        except asyncio.TimeoutError:
            message = "Gemini 문안 생성 시간이 초과됐습니다. 임시저장은 시작하지 않았습니다."
        except ValueError as exc:
            message = str(exc) if type(exc) is ValueError else "Gemini 응답 형식을 확인하지 못했습니다."
        except Exception:
            message = "agy 실행 또는 응답 읽기 실패. 터미널의 agy 로그인과 AGY_PATH를 확인하세요."
        out = {"status": "failed", "message": message, "packet_id": packet_id, "fallback": FALLBACK}
        log.warning("Gemini 문안 실패(%s): %s", generation_id[:8], message)   # 2탄 실패 이유가 남지 않았던 문제(2026-11-27)
        if response is not None:
            out["raw_saved"] = str(_keep_failed(generation_id, response, message))
        return out

    _latest = Generation(generation_id, packet_id, asyncio.create_task(run()))
    return {"generation_id": _latest.generation_id, "status": "running"}


def outcome(packet_id: str) -> str | None:
    """이 packet_id로 만든 Gemini 문안의 상태(running/ready/failed). 기록이 없으면 None(서버 재시작 시 사라짐)."""
    job = _latest
    if not job or job.packet_id != packet_id:
        return None
    return job.task.result()["status"] if job.task.done() else "running"


async def status(generation_id: str | None, wait_seconds: int) -> dict:
    job = _latest
    if not job or (generation_id is not None and generation_id != job.generation_id):
        raise ValueError("현재 서버에 해당 Gemini 생성 기록이 없습니다.")
    if not 0 <= wait_seconds <= 10:
        raise ValueError("wait_seconds는 0~10초입니다.")
    await asyncio.wait({job.task}, timeout=wait_seconds)
    return {"generation_id": job.generation_id, **(job.task.result() if job.task.done() else {"status": "running"})}
