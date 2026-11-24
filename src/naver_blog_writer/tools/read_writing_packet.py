from mcp.server.fastmcp import Image
from mcp.types import ToolAnnotations

from ..core import writing_packet

ANNOTATIONS = ToolAnnotations(title="확정한 작성 자료 보기", readOnlyHint=True, openWorldHint=False)


async def read_writing_packet(packet_id: str, page: int = 0) -> list:
    """Claude가 직접 작성할 때 prepare_writing_packet이 반환한 0~pages-1쪽을 순서대로 모두 봅니다.
    0쪽은 작성 텍스트, 이후는 Gemini에 보낼 것과 동일한 JPEG입니다. 새로 읽거나 축소하지 않습니다."""
    p = writing_packet.get(packet_id)
    if page == 0:
        return [p.text]
    if not 1 <= page <= len(p.pages):
        raise ValueError("작성 자료에 없는 페이지입니다.")
    start, end = p.pages[page-1]
    return [Image(data=b, format="jpeg") for b in p.images[start:end]]
