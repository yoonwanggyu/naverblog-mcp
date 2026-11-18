"""/research 결과 노트 저장과 조회. 노트는 NBW_HOME/research/*.md 에 저장됩니다."""

from __future__ import annotations

import re
import time
import unicodedata
from datetime import datetime
from pathlib import Path

from .. import config

STALE_DAYS = 30


def _slug(s: str) -> str:
    s = unicodedata.normalize("NFC", s).strip()
    s = re.sub(r"[\\/:*?\"<>|]+", " ", s)
    return re.sub(r"\s+", "_", s)[:60] or "note"


def save(topic: str, content: str) -> Path:
    config.ensure_dirs()
    stamp = datetime.now().strftime("%Y-%m-%d")
    path = config.RESEARCH_DIR / f"{stamp}_{_slug(topic)}.md"
    header = f"---\ntopic: {topic}\nresearched_at: {stamp}\n---\n\n"
    path.write_text(header + content.strip() + "\n", encoding="utf-8")
    return path


def search(query: str = "", limit: int = 10) -> list[dict]:
    config.ensure_dirs()
    tokens = [unicodedata.normalize("NFC", t).casefold() for t in query.split() if t]
    found = []
    for p in sorted(config.RESEARCH_DIR.glob("*.md"), reverse=True):
        name = unicodedata.normalize("NFC", p.stem).casefold()
        head = p.read_text(encoding="utf-8")[:400].casefold()
        if tokens and not all(t in name or t in head for t in tokens):
            continue
        age = (time.time() - p.stat().st_mtime) / 86400
        found.append({"path": str(p), "name": p.stem, "age_days": round(age, 1), "stale": age > STALE_DAYS})
        if len(found) >= limit:
            break
    return found


def read(path: str) -> str:
    p = Path(path).resolve()
    if config.RESEARCH_DIR.resolve() not in p.parents:
        raise PermissionError("research 폴더 밖의 파일은 읽을 수 없습니다.")
    return p.read_text(encoding="utf-8")
