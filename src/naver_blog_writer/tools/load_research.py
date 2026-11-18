"""load_research: 저장된 리서치 노트를 찾고 읽습니다."""

from mcp.types import ToolAnnotations

from ..core import notes

ANNOTATIONS = ToolAnnotations(title="리서치 노트 불러오기", readOnlyHint=True, openWorldHint=False)


async def load_research(query: str = "", read_first: bool = True) -> dict:
    """저장된 리서치 노트를 최신순으로 찾습니다. read_first=true면 가장 최근 노트 본문도 돌려줍니다.

    stale=true(30일 초과) 노트의 수치는 웹 검색으로 다시 확인한 뒤에 씁니다.
    """
    found = notes.search(query)
    out: dict = {"matched": len(found), "notes": found}
    if read_first and found:
        out["content"] = notes.read(found[0]["path"])
    return out
