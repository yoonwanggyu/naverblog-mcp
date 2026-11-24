from mcp.types import ToolAnnotations

from ..core import writing_packet
from ..core.writing_packet import Brief

ANNOTATIONS = ToolAnnotations(title="블로그 작성 자료 확정", readOnlyHint=False, destructiveHint=False, openWorldHint=False)


async def prepare_writing_packet(brief: Brief) -> dict:
    """두 작성 모드 모두 수집을 마친 뒤 호출합니다. 가이드, 이번 글에 연결한 미리보기·리서치, 요청과 정정을 한 번 확정합니다.
    packet_id를 Claude는 read_writing_packet에, Gemini 모드는 generate_gemini_draft에 전달합니다. 외부 전송이나 저장은 없습니다."""
    p = writing_packet.freeze(brief)
    return {"packet_id": p.packet_id, "images": len(p.images), "pages": 1 + len(p.pages)}
