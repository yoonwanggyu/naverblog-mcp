"""Gemini에 문안 작성 의뢰."""

from mcp.types import ToolAnnotations

from ..core import gemini_writer

ANNOTATIONS = ToolAnnotations(title="Gemini 블로그 문안 생성", readOnlyHint=False, destructiveHint=False, openWorldHint=True)


async def generate_gemini_draft(packet_id: str) -> dict:
    """Gemini 모드에서 prepare_writing_packet의 packet_id로 원고 생성을 접수합니다.
    확정한 동일 텍스트와 모든 사진·영상 장면의 JPEG를 설정된 Gemini 모델에 전달합니다.
    gemini_draft_status로 제목과 블록을 받아 Claude가 첨부를 조립합니다. 이 호출은 임시저장하지 않습니다."""
    return await gemini_writer.start(packet_id)
