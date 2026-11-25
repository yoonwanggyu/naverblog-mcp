"""Gemini 문안 생성 상태·결과 조회."""

from mcp.types import ToolAnnotations

from ..core import gemini_writer

ANNOTATIONS = ToolAnnotations(title="Gemini 문안 조회", readOnlyHint=True, openWorldHint=False)


async def gemini_draft_status(generation_id: str | None = None, wait_seconds: int = 10) -> dict:
    """prepare_writing_packet(gemini 모드)이 시작한 Gemini 문안을 generation_id로 회수합니다. ID 생략 시 현재 서버의 최근 생성입니다.
    running이면 같은 ID로 조회만 반복합니다. ready는 문안 완성이며, 네이버 저장 상태는 draft_status로 확인합니다.
    failed면 다시 요청하지 말고 fallback 안내대로 같은 packet_id를 read_writing_packet으로 읽어 Claude가 직접 씁니다."""
    return await gemini_writer.status(generation_id, wait_seconds)
