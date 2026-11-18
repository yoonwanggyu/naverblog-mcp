"""save_research: /research 결과를 노트로 저장합니다."""

from mcp.types import ToolAnnotations

from ..core import notes

ANNOTATIONS = ToolAnnotations(title="리서치 노트 저장", readOnlyHint=False, destructiveHint=False, openWorldHint=False)


async def save_research(topic: str, content: str) -> dict:
    """웹 리서치 결과(마크다운)를 저장합니다. 모든 수치에는 값, 기준일, 출처 기관, URL이 있어야 합니다.

    저장한 노트는 load_research로 다시 불러와 글에 씁니다. 30일이 지난 노트는 다시 확인해야 합니다.
    """
    if not content.strip():
        raise ValueError("내용이 비어 있습니다.")
    if "http" not in content:
        raise ValueError("출처 URL이 하나도 없습니다. 출처 없는 정보는 저장하지 않습니다.")
    path = notes.save(topic, content)
    return {"saved": str(path)}
