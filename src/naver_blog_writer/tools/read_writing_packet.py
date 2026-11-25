from mcp.server.fastmcp import Image
from mcp.types import ToolAnnotations

from .. import config
from ..core import gemini_writer, writing_packet

ANNOTATIONS = ToolAnnotations(title="확정한 작성 자료 보기", readOnlyHint=True, openWorldHint=False)


async def read_writing_packet(packet_id: str, page: int = 0) -> list:
    """Claude가 직접 작성할 때 prepare_writing_packet이 반환한 0~pages-1쪽을 순서대로 모두 봅니다.
    작성자가 gemini면 Gemini가 failed일 때만 열립니다(대체 작성).
    0쪽은 작성 텍스트, 이후는 Gemini에 보낼 것과 동일한 JPEG입니다. 새로 읽거나 축소하지 않습니다."""
    p = writing_packet.get(packet_id)
    if config.DRAFT_WRITER == "gemini" and gemini_writer.outcome(packet_id) != "failed":
        raise ValueError("작성자 설정이 gemini라 Gemini가 씁니다. gemini_draft_status로 결과를 조회하세요. "
                         "Gemini가 failed일 때만 이 묶음을 읽고 Claude가 직접 씁니다.")
    if page == 0:
        writing_packet.mark_read(packet_id, 0)
        return [p.text]
    if not 1 <= page <= len(p.pages):
        raise ValueError("작성 자료에 없는 페이지입니다.")
    start, end = p.pages[page-1]
    writing_packet.mark_read(packet_id, page)
    return [Image(data=b, format="jpeg") for b in p.images[start:end]]
