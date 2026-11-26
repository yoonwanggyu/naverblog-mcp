"""이번 글에서 Claude가 실제로 받은 작성용 텍스트. 참조는 서버 메모리에서만 유효합니다."""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from uuid import uuid4

Kind = Literal["guide", "place", "reviews"]
_sources: dict[str, tuple[float, str]] = {}
_URL = re.compile(r"(?:https?|file)://[^\s<>\"'`)\]]+", re.I)
# 내 글/개인 규칙에 들어 있는 로컬 경로는 공백을 포함할 수 있습니다.
_PATH = re.compile(r"(?:~[/\\]|/(?:Users|Volumes|home|private|mnt|tmp)/|[A-Za-z]:[\\/]|\\\\[^\\\s]+\\)[^\n\r\"'`<>]*")


@dataclass
class Collection:
    ids: dict[Path, str] = field(default_factory=dict)
    evidence: list[dict] = field(default_factory=list)
    images: list[bytes] = field(default_factory=list)
    # 같은 사진(경로)과 같은 영상 장면(경로+JPEG 해시)은 한 번만 담습니다. 값은 images의 위치입니다.
    seen: dict[tuple[str, Path, str], int] = field(default_factory=dict)

    def visual_sources(self) -> list[tuple[str, Path]]:
        """V번호 순서(V1부터)의 (종류, 파일). photo는 사진 파일, video_frame은 영상 파일입니다. 서버 안에서만 씁니다."""
        out: list[tuple[str, Path] | None] = [None] * len(self.images)
        for (kind, path, _h), i in self.seen.items():
            out[i] = (kind, path)
        return [x for x in out if x]

    def asset(self, path: Path) -> str:
        return self.ids.setdefault(path, f"A{len(self.ids) + 1}")

    def previews(self, items: list[tuple[Path, bytes]], kind: str) -> list[str]:
        """미리보기를 이 글의 자료에 담고 라벨(A번호/V번호)을 돌려줍니다.

        다시 본 사진은 새로 담지 않고 처음 자리를 그대로 씁니다. 더 크게 다시 봤으면(글자 확인 등) 그 자리의 JPEG만 큰 것으로 바꿉니다.
        """
        images, seen, added = list(self.images), dict(self.seen), []
        slots = []
        for path, jpeg in items:
            key = (kind, path, "" if kind == "photo" else hashlib.sha256(jpeg).hexdigest())
            i = seen.get(key)
            if i is None:
                images.append(jpeg)
                i = seen[key] = len(images) - 1
                added.append((path, i))
            elif len(jpeg) > len(images[i]):
                images[i] = jpeg
            slots.append((path, i))
        self.images, self.seen = images, seen
        for path, i in added:
            self.evidence.append({"kind": kind, "asset": self.asset(path), "visual": f"V{i + 1}"})
        return [f"{self.asset(path)}/V{i + 1}" for path, i in slots]


_collections: dict[str, Collection] = {}


def collection(guide_ref: str) -> Collection:
    if guide_ref not in _collections or _sources[guide_ref][0] <= time.monotonic():
        raise ValueError("이 글의 가이드 참조가 없거나 만료됐습니다. get_style_guide의 writing_ref를 사용하세요.")
    return _collections[guide_ref]


def without_locations(value):
    if isinstance(value, str):
        return _PATH.sub("[로컬 경로 생략]", _URL.sub("[URL 생략]", value))
    if isinstance(value, list):
        return [without_locations(v) for v in value]
    if isinstance(value, dict):
        return {k: without_locations(v) for k, v in value.items() if k != "url"}
    return value


def remember(kind: Kind, value: dict) -> str:
    # 최신 파일을 나중에 다시 읽으면 동시 대화/다른 주제의 자료가 섞일 수 있어 반환 시점의 사본을 고정합니다.
    now = time.monotonic()
    for ref, (expires, _) in list(_sources.items()):
        if expires <= now:
            del _sources[ref]
            _collections.pop(ref, None)
    if len(_sources) >= 128:
        oldest = next(iter(_sources))
        del _sources[oldest]
        _collections.pop(oldest, None)
    ref = f"{kind}_{uuid4().hex}"
    _sources[ref] = now + 86400, json.dumps(without_locations(value), ensure_ascii=False)
    if kind == "guide":
        if len(_collections) >= 8:
            del _collections[next(iter(_collections))]
        _collections[ref] = Collection()
    return ref


def recall(kind: Kind, ref: str) -> dict:
    entry = _sources.get(ref)
    if not ref.startswith(kind + "_") or not entry or entry[0] <= time.monotonic():
        raise ValueError(f"이번 글의 {kind} 작성 자료 참조가 없거나 만료됐습니다. 해당 수집 도구 결과를 다시 확인하세요.")
    return json.loads(entry[1])


def attach_places(result: dict) -> None:
    """Claude가 확인한 후보의 참조만 선택합니다. 사진 GPS·거리·지도 URL은 글감이 아닙니다."""
    refs = {}
    for p in [*result["candidates"], result["best"]]:
        if not p:
            continue
        pid = p["place_id"]
        if pid not in refs:
            refs[pid] = remember("place", {k: p.get(k) for k in
                                 ("place_id", "name", "category", "address", "road_address", "full_address", "phone")})
        p["writing_ref"] = refs[pid]
