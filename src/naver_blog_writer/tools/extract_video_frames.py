"""extract_video_frames: 영상에서 장면을 뽑아 보여 줍니다."""

import asyncio

from mcp.server.fastmcp import Image
from mcp.types import ToolAnnotations

from ..core import media, writing_sources

ANNOTATIONS = ToolAnnotations(title="영상 장면 추출", readOnlyHint=True, openWorldHint=False)


async def extract_video_frames(video_path: str, guide_ref: str, count: int = 6) -> list:
    """동영상에서 균등 간격으로 장면을 뽑아 보여 줍니다. 뽑은 프레임 경로는 write_post의 image 블록에 그대로 쓸 수 있습니다.

    guide_ref는 이번 글의 get_style_guide.writing_ref입니다. 같은 장면 미리보기를 글쓰기 자료에 연결합니다. 글에는 보통 영상을 video 블록으로 올리고,
    사용자가 영상 업로드를 원하지 않을 때만 장면 1~2장을 그 영상의 order 자리(촬영 시각 순서)에 사진처럼 넣습니다.
    """
    v = media.check_allowed(video_path)
    if v.suffix.lower() not in media.VIDEO_EXT:
        raise ValueError(f"동영상 파일이 아닙니다: {v}")
    if not 1 <= count <= 10:
        raise ValueError("영상 미리보기 장면 수는 1~10장입니다.")
    frames = await asyncio.to_thread(media.extract_frames, v, count)
    if len(frames) != count:
        raise ValueError(f"요청한 {count}장 중 {len(frames)}장만 추출됐습니다. 불완전한 장면 묶음으로 작성하지 않습니다.")
    out: list = [f"영상: {v}\n추출한 프레임 {len(frames)}장 (업로드에 사용 가능):\n" + "\n".join(str(f) for f in frames)]
    datas = [await asyncio.to_thread(media.preview_jpeg, media.check_allowed(f), 768) for f in frames]
    labels = writing_sources.collection(guide_ref).previews([(v, jpeg) for jpeg in datas], "video_frame")
    for f, jpeg, label in zip(frames, datas, labels, strict=True):
        out.append(f"{f.name} ({label})")
        out.append(Image(data=jpeg, format="jpeg"))
    return out
