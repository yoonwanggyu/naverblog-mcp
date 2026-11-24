"""scan_media: 폴더의 사진과 영상 목록과 메타데이터."""

import asyncio
from pathlib import Path

from mcp.types import ToolAnnotations

from ..core import folders, media, writing_sources

ANNOTATIONS = ToolAnnotations(title="사진 폴더 스캔", readOnlyHint=True, openWorldHint=False)


async def scan_media(folder: str, recursive: bool = True, user_confirmed: bool = False, guide_ref: str | None = None) -> dict:
    """폴더의 사진과 동영상을 촬영 시각 순으로 돌려줍니다(order 1부터). 촬영 시각, GPS, 해상도, 영상 길이, 기기를 포함합니다.

    두 사람이 같은 폴더에 각자 사진을 올리므로 파일 이름이 아니라 촬영 시각으로 섞어 정렬합니다. 글에도 이 order 순서대로 넣습니다.
    time_source: exif(촬영 시각), video_meta(영상), file_name(파일 이름 속 날짜), file_modified(정확하지 않을 수 있음).
    영상은 write_post의 video 블록으로 올립니다. video_question이 있으면(영상 2개 이상) 그대로 사용자에게 보여 주고 고른 영상만 씁니다.

    list_folders가 확인이 필요하다고 한 폴더는 사용자가 '맞다'고 답한 뒤 user_confirmed=true로만 읽을 수 있습니다.
    이미지를 인식하지는 않습니다. 내용은 view_images로 직접 봐야 합니다.
    """
    p = media.check_allowed(folder)
    if not p.is_dir():
        raise ValueError(f"폴더가 아닙니다: {p}")
    question = folders.pending_question(p)
    if question and not user_confirmed:
        raise PermissionError("이 폴더는 사용자 확인이 필요합니다. 아래 질문을 사용자에게 그대로 보여 주고 답을 받으세요.\n\n" + question)
    folders.mark_confirmed(p)
    items = await asyncio.to_thread(media.scan_folder, p, recursive)
    if guide_ref:
        if len(items) > 300:
            raise ValueError("스캔 결과가 300개를 넘습니다. 일부 자료로 원고를 만들지 말고 폴더 범위를 줄여 주세요.")
        source = writing_sources.collection(guide_ref)
        for i in items:
            i["asset"] = source.asset(Path(i["path"]))
        source.evidence.append({"kind": "scan", "items": [{k: i[k] for k in ("asset", "kind", "order", "taken_at", "time_source", "duration_sec", "width", "height") if k in i} for i in items]})
    devices: dict[str, int] = {}
    for i in items:
        key = i.get("camera") or "알 수 없음"
        devices[key] = devices.get(key, 0) + 1
    out_note = None
    guessed = sum(1 for i in items if i.get("time_source") == "file_modified")
    if guessed:
        out_note = f"{guessed}개는 촬영 시각 정보가 없어 파일 수정 시각으로 정렬했습니다. 순서가 어색하면 사용자에게 확인하세요."
    vids = [i for i in items if i["kind"] == "video"]
    video_question = None
    if len(vids) > 1:
        lines = [f"{n}) {v['path'].rsplit('/', 1)[-1]} · {round(v.get('duration_sec') or 0)}초 · {v['taken_at'][5:16].replace('T', ' ')} 촬영"
                 for n, v in enumerate(vids, 1)]
        video_question = (f"폴더에 영상이 {len(vids)}개 있습니다. 글에 넣을 영상을 골라 주세요(번호 여러 개, '전부', '넣지 않음' 가능).\n"
                          + "\n".join(lines))
    return {
        "folder": str(p),
        "devices": devices,
        "video_question": video_question,
        "video_note": "영상 1개는 촬영 시각 자리에 video 블록으로 넣습니다." if len(vids) == 1 else None,
        "order_note": out_note,
        "images": sum(1 for i in items if i["kind"] == "image"),
        "videos": sum(1 for i in items if i["kind"] == "video"),
        "items": items[:300],
        "truncated": len(items) > 300,
    }
