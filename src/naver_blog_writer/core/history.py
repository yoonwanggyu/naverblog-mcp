"""작성 기록. 같은 글이 짧은 시간 안에 두 번 올라가는 것을 막습니다."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from .. import config


def _load() -> list[dict[str, Any]]:
    try:
        return json.loads(config.HISTORY_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []


def recent_duplicate(digest: str) -> dict[str, Any] | None:
    cutoff = time.time() - config.DUPLICATE_WINDOW_MIN * 60
    for rec in reversed(_load()):
        if rec.get("ts", 0) < cutoff:
            break
        if rec.get("digest") == digest and (rec.get("ok") or rec.get("unconfirmed")):
            return rec  # 저장 확인을 못 한 기록도 실제로는 저장됐을 수 있어 중복으로 봅니다.
    return None


def job_record(job_id: str | None) -> dict[str, Any] | None:
    return next((r for r in reversed(_load()) if r.get("job_id") and (job_id is None or r["job_id"] == job_id)), None)


def record_job(job_id: str, **fields: Any) -> None:
    """접수 → 저장 시도 → 결과를 같은 행에 기록합니다. 저장 직전의 미확인 기록도 한도에 셉니다."""
    config.ensure_dirs()
    data = _load()
    record = next((r for r in data if r.get("job_id") == job_id), {})
    if record:
        data.remove(record)
    record.update(job_id=job_id, ts=time.time(), time=time.strftime("%Y-%m-%d %H:%M:%S"), **fields)
    data.append(record)
    # 쓰던 중 종료돼도 직전 이력(하루 한도와 중복 검사)이 남습니다.
    tmp = tempfile.NamedTemporaryFile(dir=config.HOME, delete=False, mode="w", encoding="utf-8")
    try:
        with tmp as f:
            json.dump(data[-300:], f, ensure_ascii=False, indent=1)
        os.replace(tmp.name, config.HISTORY_PATH)
    finally:
        Path(tmp.name).unlink(missing_ok=True)


def drafts_today() -> int:
    """오늘(로컬 날짜) 임시저장 건수. 저장 확인을 못 한 시도도 실제로 저장됐을 수 있어 포함합니다."""
    today = time.strftime("%Y-%m-%d")
    return sum(1 for r in _load() if (r.get("ok") or r.get("unconfirmed")) and r.get("mode") == "draft"
               and str(r.get("time", "")).startswith(today))
