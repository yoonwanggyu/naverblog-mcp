"""작성 기록. 같은 글이 짧은 시간 안에 두 번 올라가는 것을 막습니다."""

from __future__ import annotations

import json
import time
from typing import Any

from .. import config


def _load() -> list[dict[str, Any]]:
    try:
        return json.loads(config.HISTORY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def recent_duplicate(digest: str) -> dict[str, Any] | None:
    cutoff = time.time() - config.DUPLICATE_WINDOW_MIN * 60
    for rec in reversed(_load()):
        if rec.get("ts", 0) < cutoff:
            break
        if rec.get("digest") == digest and (rec.get("ok") or rec.get("unconfirmed")):
            return rec  # 저장 확인을 못 한 기록도 실제로는 저장됐을 수 있어 중복으로 봅니다.
    return None


def append(record: dict[str, Any]) -> None:
    config.ensure_dirs()
    data = _load()
    record = {"ts": time.time(), "time": time.strftime("%Y-%m-%d %H:%M:%S"), **record}
    data.append(record)
    config.HISTORY_PATH.write_text(json.dumps(data[-300:], ensure_ascii=False, indent=1), encoding="utf-8")


def drafts_today() -> int:
    """오늘(로컬 날짜) 임시저장 건수. 저장 확인을 못 한 시도도 실제로 저장됐을 수 있어 포함합니다."""
    today = time.strftime("%Y-%m-%d")
    return sum(1 for r in _load() if (r.get("ok") or r.get("unconfirmed")) and r.get("mode") == "draft"
               and str(r.get("time", "")).startswith(today))
