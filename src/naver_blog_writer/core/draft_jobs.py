"""한 서버에서 임시저장 작업을 하나만 실행합니다. 조회는 브라우저와 무관합니다."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from uuid import uuid4

from . import history

log = logging.getLogger(__name__)


@dataclass
class DraftJob:
    title: str
    digest: str
    job_id: str = field(default_factory=lambda: uuid4().hex)
    status: str = "running"
    step: str = "접수"
    done: int = 0
    total: int = 0
    result: dict | None = None
    screenshot: bytes | None = None
    save_attempted: bool = False
    finished: asyncio.Event = field(default_factory=asyncio.Event)

    def progress(self, done: int, total: int, step: str) -> None:
        self.done, self.total, self.step = done, total, step

    def before_save(self) -> None:
        history.record_job(self.job_id, status="unconfirmed", unconfirmed=True, step="임시저장")
        self.save_attempted = True

    def finish(self, status: str, result: dict) -> None:
        self.status, self.result = status, result
        self.finished.set()
        history.record_job(self.job_id, status=status, ok=status == "saved", unconfirmed=status == "unconfirmed",
                           step=self.step, message=result["message"], save_signal=result.get("save_signal"),
                           verify_ok=result.get("verify", {}).get("ok"))
        log.info("임시저장 결과: %s %s", self.job_id, status)

    def snapshot(self) -> dict:
        return {"job_id": self.job_id, "title": self.title, "status": self.status, "step": self.step,
                "done": self.done, "total": self.total, **({"result": self.result} if self.result else {})}


_jobs: dict[str, DraftJob] = {}
_active: asyncio.Task | None = None


def running(digest: str) -> DraftJob | None:
    if _active is not None and not _active.done():
        job = next(reversed(_jobs.values()))
        if job.digest == digest:
            return job
        raise RuntimeError(f"다른 임시저장 작업이 진행 중입니다. draft_status(job_id='{job.job_id}')로 확인하세요.")
    return None


def start(title: str, digest: str, work: Callable[[DraftJob], Awaitable[None]]) -> DraftJob:
    global _active
    job = DraftJob(title, digest)
    history.record_job(job.job_id, title=title, digest=digest, mode="draft", status="running", ok=False)
    _jobs[job.job_id] = job
    if len(_jobs) > 10:  # 상세 결과와 선택적 실패 스크린샷은 최근 작업만 메모리에 유지합니다.
        del _jobs[next(iter(_jobs))]

    async def run() -> None:
        try:
            await work(job)
        except (Exception, asyncio.CancelledError) as e:
            if job.finished.is_set() and isinstance(e, asyncio.CancelledError):
                return  # 결과 반환 직후 서버를 종료해 브라우저 정리만 중단된 경우
            log.exception("임시저장 작업 예외: %s (%s)", job.job_id, job.step)
            if not job.finished.is_set():
                status = "unconfirmed" if job.save_attempted else "failed"
                job.finish(status, {"ok": False, "saved_confirmed": False,
                                    "message": f"FAILED: {job.step}: {e or '작업 중단'}. 자동 재시도하지 않습니다."})

    _active = asyncio.create_task(run())  # 요청의 MCP Context를 저장하거나 나중에 알림 전송에 쓰지 않습니다.
    return job


async def lookup(job_id: str | None, wait_seconds: int) -> tuple[dict, bytes | None]:
    if job_id is None and _jobs:
        job_id = next(reversed(_jobs))
    job = _jobs.get(job_id) if job_id else None
    if job:
        if wait_seconds and not job.finished.is_set():
            try:
                await asyncio.wait_for(job.finished.wait(), timeout=wait_seconds)
            except asyncio.TimeoutError:
                pass  # 만료되는 것은 조회뿐입니다. 임시저장 작업은 계속됩니다.
        return job.snapshot(), job.screenshot
    rec = history.job_record(job_id)
    if rec is None:
        raise ValueError("임시저장 작업 기록이 없습니다. 다시 보내기 전에 네이버 '저장' 목록을 확인하세요.")
    status = rec["status"]
    if status == "running":
        status = "unknown"  # 재시작/다른 서버: 실행 중이라거나 실패했다고 단정하지 않습니다.
    return {"job_id": rec["job_id"], "title": rec["title"], "status": status, "step": rec.get("step", "접수"),
            "result": {"ok": status == "saved", "saved_confirmed": status == "saved", "save_signal": rec.get("save_signal"),
                       **({"verify": {"ok": rec["verify_ok"]}} if rec.get("verify_ok") is not None else {}),
                       "message": rec.get("message") or "실행 상태를 확인할 수 없습니다. 재요청 전에 네이버 '저장' 목록을 확인하세요."}}, None
