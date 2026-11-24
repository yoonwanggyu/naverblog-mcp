"""draft_status: 접수된 임시저장 작업의 진행 단계와 결과 조회."""

import json

from mcp.server.fastmcp import Image
from mcp.types import ToolAnnotations

from .. import config
from ..core import draft_jobs

ANNOTATIONS = ToolAnnotations(title="임시저장 작업 상태", readOnlyHint=True, openWorldHint=False)


async def draft_status(job_id: str | None = None, wait_seconds: int = 0) -> list:
    """write_post가 접수한 작업의 진행 단계, 검증·임시저장 결과를 확인합니다. ID를 생략하면 최근 작업입니다.
    running이면 같은 ID로 다시 조회합니다(wait_seconds는 0~10초). saved만 저장 성공입니다.
    조회는 브라우저를 열거나 저장을 다시 시도하지 않습니다."""
    if not 0 <= wait_seconds <= 10:
        raise ValueError("wait_seconds는 0~10초입니다.")
    state, shot = await draft_jobs.lookup(job_id, wait_seconds)
    out = [json.dumps(state, ensure_ascii=False)]
    if shot and config.RETURN_ERROR_SCREENSHOT:
        out.append(Image(data=shot, format="png"))
    return out
