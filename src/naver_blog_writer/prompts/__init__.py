"""MCP 프롬프트 등록. 파일 1개 = 프롬프트 1개, 파일명 = 함수명 = 프롬프트명.

Claude Desktop에서는 입력창의 + 메뉴(또는 / 명령)에서 고를 수 있습니다.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.prompts import Prompt

from . import research, write

PROMPTS = [write, research]


def register_all(mcp: FastMCP) -> None:
    for mod in PROMPTS:
        name = mod.__name__.rsplit(".", 1)[-1]
        mcp.add_prompt(Prompt.from_function(getattr(mod, name), name=name, title=getattr(mod, "TITLE", None)))
