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
    photo_groups: 이름이 같고 끝 번호만 다른 사진 묶음(예: 만복국수집1~3). 같은 대상의 사진이라는 사실 정보입니다.
    camera_groups: 핸드폰이 아닌 카메라(파일 이름의 '필름', 'DSLR' 등 또는 EXIF 기기) 사진의 종류별 묶음. 마무리 상자 위에 '○○ 컷' 구역으로 모읍니다.
    post_kind: 파일 이름으로 본 글 유형 힌트(collection=여러 장소 모음집). 배치(개별, 나란히 2~3장, 슬라이드 4장 이상)는 이 정보와
    같은 장소 리뷰·내 글·최신 경향의 photo_layout을 보고 작성자가 가독성 있게 정합니다. 사진 수는 줄이지 않습니다.

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
    for i in items:
        if i["kind"] == "image":
            ck = media.camera_kind(i["path"], i)
            if ck:
                i["camera_kind"] = ck
    cameras = media.camera_groups(items)
    groups, kind = media.photo_groups(items)
    if guide_ref:
        source = writing_sources.collection(guide_ref)
        for i in items:
            i["asset"] = source.asset(Path(i["path"]))
        # name: 파일 이름(확장자 제외). 모음집에서는 장소 이름이 들어 있어 배치와 소제목의 근거가 됩니다. 경로는 넣지 않습니다.
        source.evidence.append({"kind": "scan", "items": [{**{k: i[k] for k in ("asset", "kind", "order", "taken_at", "time_source", "duration_sec", "width", "height") if k in i},
                                                            "name": Path(i["path"]).stem} for i in items]})
        if cameras:
            source.evidence.append({"kind": "camera_groups", "note": "핸드폰이 아닌 카메라 사진. 본문 시간 흐름에 넣지 않고 마무리 상자 위에 종류별 '○○ 컷' 구역으로 모음",
                                    "groups": [{"label": g["label"], "devices": g["devices"], "assets": [source.asset(Path(x)) for x in g["paths"]]} for g in cameras]})
        if groups or kind["post_kind_hint"] == "collection":
            source.evidence.append({"kind": "photo_groups", **kind, "groups": [
                {"name": g["name"], "assets": [source.asset(Path(x)) for x in g["paths"]]} for g in groups]})
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
        "post_kind": kind,
        "photo_groups": groups,
        "camera_groups": cameras,
        "camera_note": ("핸드폰이 아닌 카메라(필름, DSLR 등) 사진입니다. 본문 시간 흐름에 넣지 않고 마무리 상자 바로 위에 종류별 구역으로 모읍니다: "
                        "소제목 '○○ 컷'(예: 필름카메라 컷, DSLR 컷) 아래에 사진 묶음(배치는 가이드 4-6절 기준), 사진별 설명은 쓰지 않습니다."
                        if cameras else None),
        "photo_note": ("사진 수를 스스로 줄이지 않습니다. 흐리거나 거의 같은 사진, 개인정보가 보이는 사진만 빼고 이유를 보고합니다. "
                       "배치는 스타일 가이드 4-6절 기준과 photo_layout 분석으로 정합니다."),
        "items": items,
    }
