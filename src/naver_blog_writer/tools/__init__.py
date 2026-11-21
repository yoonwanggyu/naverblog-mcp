"""MCP 도구 등록. 파일 1개 = 도구 1개, 파일명 = 함수명 = 도구명.

새 도구를 만들면 모듈을 TOOLS에 추가합니다. 순서는 Claude에게 보이는 목록 순서입니다.
"""

from __future__ import annotations

from types import ModuleType

from mcp.server.fastmcp import FastMCP

from . import (
    check_setup,
    extract_video_frames,
    find_place,
    get_style_guide,
    inspect_editor,
    list_folders,
    load_research,
    login_status,
    my_post_links,
    open_login_window,
    place_reviews,
    recent_posts,
    refresh_trends,
    save_research,
    scan_media,
    view_images,
    write_post,
)

TOOLS: list[ModuleType] = [
    get_style_guide,
    recent_posts,
    list_folders,
    scan_media,
    view_images,
    extract_video_frames,
    find_place,
    place_reviews,
    load_research,
    save_research,
    write_post,
    my_post_links,
    refresh_trends,
    check_setup,
    login_status,
    open_login_window,
    inspect_editor,
]


def register_all(mcp: FastMCP) -> None:
    for mod in TOOLS:
        name = mod.__name__.rsplit(".", 1)[-1]
        fn = getattr(mod, name)
        mcp.add_tool(fn, name=name, annotations=getattr(mod, "ANNOTATIONS", None))
