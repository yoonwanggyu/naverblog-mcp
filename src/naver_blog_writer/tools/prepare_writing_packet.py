from mcp.types import ToolAnnotations

from .. import config
from ..core import gemini_writer, writing_packet
from ..core.writing_packet import Brief

ANNOTATIONS = ToolAnnotations(title="블로그 작성 자료 확정", readOnlyHint=False, destructiveHint=False, openWorldHint=True)


async def prepare_writing_packet(brief: Brief) -> dict:
    """글감 수집을 마친 뒤 호출합니다. 가이드, 이번 글에 연결한 미리보기·리서치, 요청과 정정을 한 번 확정합니다.
    작성자가 gemini면 이 호출이 확정한 묶음으로 Gemini 문안 작성을 바로 시작하고 generation_id를 돌려줍니다.
    그때는 Claude가 쓰지 말고 gemini_draft_status(generation_id, wait_seconds=10)로 조회만 합니다.
    작성자가 claude면 read_writing_packet(packet_id, page=0..pages-1)로 모두 읽고 씁니다. 네이버 저장은 없습니다."""
    p = writing_packet.freeze(brief)
    out = {"packet_id": p.packet_id, "images": len(p.images), "pages": 1 + len(p.pages), "writer": config.DRAFT_WRITER}
    missing = brief.sections_without_fill()
    if missing:
        out["slot_warnings"] = (f"직접 입력 자리(fill)가 없는 소제목 구역 {len(missing)}곳: {missing[:5]}. 그 구역에 사진이 있으면 "
                                "fill 자리를 넣어 다시 확정하세요(추천 문구 자리 없이 맡기면 사진으로 경험을 추정해 씁니다).")
    if config.DRAFT_WRITER != "gemini":
        return {**out, "next": "read_writing_packet(packet_id, page=0..pages-1)로 모두 읽고 Claude가 씁니다."}
    receipt = await gemini_writer.start(p.packet_id)
    return {**out, **receipt, "next": "Gemini가 쓰는 중입니다. Claude는 쓰지 말고 gemini_draft_status(generation_id, wait_seconds=10)로 "
            "ready나 failed가 나올 때까지 조회만 반복합니다."}
