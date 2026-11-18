"""extract_video_frames: 영상에서 장면을 뽑아 보여 줍니다."""

import asyncio

from mcp.server.fastmcp import Image
from mcp.types import ToolAnnotations

from ..core import media

ANNOTATIONS = ToolAnnotations(title="영상 장면 추출", readOnlyHint=True, openWorldHint=False)


async def extract_video_frames(video_path: str, count: int = 6) -> list:
    """동영상에서 균등 간격으로 장면을 뽑아 보여 줍니다. 뽑은 프레임 경로는 write_post의 image 블록에 그대로 쓸 수 있습니다."""
    v = media.check_allowed(video_path)
    if v.suffix.lower() not in media.VIDEO_EXT:
        raise ValueError(f"동영상 파일이 아닙니다: {v}")
    frames = await asyncio.to_thread(media.extract_frames, v, count)
    out: list = [f"영상: {v}\n추출한 프레임 {len(frames)}장 (업로드에 사용 가능):\n" + "\n".join(str(f) for f in frames)]
    for f in frames:
        out.append(f.name)
        out.append(Image(data=await asyncio.to_thread(media.preview_jpeg, f, 768), format="jpeg"))
    return out
