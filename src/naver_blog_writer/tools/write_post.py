"""write_post: 네이버 블로그 에디터에 글을 입력하고 임시저장합니다. 발행 기능은 없습니다."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Literal

from mcp.server.fastmcp import Context, Image
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field, model_validator

from .. import config
from ..core import archive, blog_reader, editor, formatting, history, media, place_reviews
from ..core.browser import NotLoggedInError, browser_session

log = logging.getLogger(__name__)

ANNOTATIONS = ToolAnnotations(
    title="네이버 블로그 임시저장",
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True,
)

NON_TEXT = {"subtitle", "quote", "divider", "image", "place"}
CLOSING_STYLES = ("quotation_bubble", "quotation_postit", "quotation_corner")
TEXTLIKE = {"text", "fill"}
QuoteStyle = Literal["quotation_line", "quotation_underline", "default", "quotation_bubble", "quotation_postit", "quotation_corner"]
OpeningType = Literal["인사형", "상황형", "질문형", "요약형", "감성형"]


class Block(BaseModel):
    """본문 블록. 위에서부터 순서대로 에디터에 들어갑니다."""

    type: Literal["text", "fill", "subtitle", "quote", "divider", "image", "place"] = Field(
        description="text: 글(한 줄 한 호흡, \\n으로 줄 나눔, 빈 줄로 묶음 구분), fill: 주관 평가 자리(직접 입력 문구 + 추천 문구), "
                    "subtitle: 소제목(인용구), quote: 마무리 상자(말풍선, 포스트잇, 프레임 인용구, 여러 줄), "
                    "divider: 구분선, image: 사진, place: 장소 지도"
    )
    text: str | None = Field(default=None, description="text, subtitle, quote의 내용. 줄 안의 **글자**는 굵게 들어갑니다. quote는 \\n으로 2~4줄")
    topic: str | None = Field(default=None, description="fill: 사용자가 채울 내용(예: '가지튀김 맛', '총평'). 보고용")
    suggestions: list[str] | None = Field(default=None, description="fill: 추천 문구 2~3개. '(추천 문구)' 줄로 들어가 사용자가 고르거나 지웁니다")
    style: QuoteStyle | None = Field(default=None, description="subtitle의 인용구 모양(비우면 SUBTITLE_STYLE). "
                                                               "quote는 quotation_bubble(말풍선, 기본), quotation_postit(포스트잇), quotation_corner(프레임) 중 하나")
    path: str | None = Field(default=None, description="image의 사진 절대경로 (scan_media 결과의 path)")
    caption: str | None = Field(default=None, description="image의 사진 설명 (선택). 전체 사진의 절반 정도에만")
    query: str | None = Field(default=None, description="place의 검색어. find_place 결과의 place_block.query")
    address: str | None = Field(default=None, description="place의 주소. find_place 결과의 place_block.address (같은 이름 지점 구분용)")
    place_id: str | None = Field(default=None, description="place의 네이버 장소 ID. find_place 결과의 place_block.place_id (같은 장소 리뷰 확인용)")

    @model_validator(mode="after")
    def _check(self) -> Block:
        if self.type in ("text", "subtitle", "quote") and not (self.text and self.text.strip()):
            raise ValueError(f"{self.type} 블록에는 text가 필요합니다. 빈 블록은 만들지 않습니다.")
        if self.type == "fill" and self.suggestions and len(self.suggestions) > 4:
            raise ValueError("fill 블록의 추천 문구는 4개까지입니다.")
        if self.type == "image" and not self.path:
            raise ValueError("image 블록에는 path가 필요합니다.")
        if self.type == "place" and not (self.query and self.query.strip()):
            raise ValueError("place 블록에는 query가 필요합니다.")
        return self


def _quote_lines(b: Block) -> list[str]:
    return [ln for grp in formatting.to_paragraphs(b.text or "") for ln in grp]


def _digest(title: str, blocks: list[Block]) -> str:
    raw = json.dumps([title] + [b.model_dump() for b in blocks], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _require_recent_style_sync() -> None:
    """글을 쓸 때마다 내 블로그 최근 게시물을 먼저 읽었는지 확인합니다(문체 기준이자 겹침 비교 대상)."""
    if not config.BLOG_ID:
        return
    age = blog_reader.sync_age_minutes()
    if age is None or age > config.STYLE_SYNC_MAX_AGE_MIN:
        raise ValueError(
            "내 블로그의 최근 게시물을 아직 읽지 않았습니다. get_style_guide를 먼저 호출해 최근 글 5개의 스타일을 반영하고, "
            "그 가이드대로 글을 쓴 뒤 다시 write_post를 호출하세요."
        )


def _require_place_reviews(blocks: list[Block]) -> list[str]:
    """장소 글은 장소마다 place_reviews로 같은 장소의 최신 리뷰를 먼저 읽어야 합니다(24시간 안).
    읽기에 실패한 기록이 있으면 막지 않고 안내만 돌려줍니다."""
    notes: list[str] = []
    missing: list[str] = []
    if not config.READ_OTHER_BLOGS:
        return notes
    for b in blocks:
        if b.type != "place":
            continue
        rec = place_reviews.find_record(b.place_id, b.query)
        if rec is None:
            missing.append(b.query or "")
        elif rec.get("error"):
            notes.append(f"장소 '{b.query}': 같은 장소 리뷰를 읽지 못해({rec['error'][:60]}) 비교 없이 저장했습니다.")
    if missing:
        raise ValueError(
            f"장소 글은 장소마다 같은 장소의 최신 리뷰를 먼저 읽어야 합니다. 아직 읽지 않은 장소: {missing}. "
            "find_place 결과의 place_id로 place_reviews를 호출한 뒤 다시 write_post를 호출하세요."
        )
    return notes


async def _progress(ctx: Context | None, done: int, total: int, message: str) -> None:
    if ctx is None:
        return
    try:
        await ctx.report_progress(done, total, message)
    except Exception:
        pass


async def write_post(
    title: str,
    blocks: list[Block],
    tags: list[str] | None = None,
    main_keyword: str | None = None,
    hook_pattern: OpeningType | None = None,
    sponsored: str | None = None,
    dry_run: bool = False,
    force: bool = False,
    review_seconds: int = 5,
    ctx: Context | None = None,
) -> list:
    """네이버 블로그 에디터에 제목과 블록을 순서대로 입력하고 임시저장합니다. 발행은 하지 않습니다(코드에서 차단).

    - 먼저 get_style_guide를 호출해야 합니다(최근 게시물을 읽지 않았으면 거부합니다).
    - 맛 평가, 외관 후기, 분위기 감상, 느낀 점, 추천 여부 같은 주관 평가는 쓰지 말고 fill 블록(topic, suggestions 2~3개)을 넣습니다.
      '[이 부분에 직접 입력하시기 바랍니다]' 아래에 '(추천 문구)' 줄이 붙어 들어갑니다.
    - text는 한 줄 한 호흡(15~28자)으로 \\n을 넣어 쓰고, 생각이 바뀌는 곳에 빈 줄을 둡니다. 32자가 넘는 줄은 자동으로 나눕니다.
      본문은 TEXT_ALIGN(기본 가운데) 정렬로 들어갑니다.
    - subtitle은 인용구로(style로 모양 선택, 기본 세로선), divider는 구분선으로, place는 지도 첨부로 들어갑니다. place는 find_place의 place_block을 씁니다.
    - quote는 글 맨 끝 마무리 상자입니다(말풍선, 포스트잇, 프레임). 추천 상황 한 줄과 댓글, 공감, 이웃추가 같은 참여 문구를 2~4줄로 씁니다.
    - sponsored: 협찬이면 표기 문구를 넣습니다. 본문 첫 블록으로 통째로 들어갑니다.
    - tags: 8~12개. 본문 끝에 #태그로 들어갑니다.
    - main_keyword, hook_pattern(도입 유형): 최근 글과 겹치는지 비교하는 데 쓰고, 기록에도 남깁니다. 항상 채웁니다.
    - dry_run=true: 브라우저 없이 정리 결과와 경고(lint, 겹침)만 돌려줍니다. 먼저 한 번 호출하세요.
    - 검증은 저장 직전 텍스트 대조 1회입니다. 불일치가 있어도 저장하고 보고만 합니다. 재시도는 사용자에게 먼저 묻습니다.
    - 브라우저 창이 화면에 보이며 사용자가 입력 과정을 지켜봅니다. review_seconds: 저장 후 창을 열어 두는 시간(초).
    """
    title = formatting.clean_line(title)
    if not title:
        raise ValueError("제목이 비어 있습니다.")
    if not blocks:
        raise ValueError("본문 블록이 비어 있습니다.")
    _require_recent_style_sync()
    place_notes = _require_place_reviews(blocks)

    # 이미지 검증과 변환 (GPS 제거, HEIC 변환은 시간이 걸리므로 별도 스레드에서)
    uploads: dict[int, Path] = {}
    for i, b in enumerate(blocks):
        if b.type != "image":
            continue
        src = media.check_allowed(b.path or "")
        ext = src.suffix.lower()
        if ext in media.VIDEO_EXT:
            raise ValueError(f"동영상은 직접 넣을 수 없습니다. extract_video_frames로 장면을 뽑아 이미지로 넣으세요: {src}")
        if ext not in media.IMAGE_EXT:
            raise ValueError(f"지원하지 않는 이미지 형식입니다: {src}")
        uploads[i] = src
    if len(uploads) > config.MAX_IMAGES_PER_POST:
        raise ValueError(f"이미지가 {len(uploads)}장입니다. 최대 {config.MAX_IMAGES_PER_POST}장까지 허용합니다.")
    if len({str(p) for p in uploads.values()}) != len(uploads):
        raise ValueError("같은 사진이 두 번 이상 들어 있습니다. 사진 재사용은 유사문서 위험이 있습니다.")
    for i, src in list(uploads.items()):
        uploads[i] = await asyncio.to_thread(media.prepare_for_upload, src)

    # 텍스트 정리
    paragraphs: dict[int, list[list[str]]] = {
        i: formatting.to_paragraphs((b.text or "") if b.type == "text" else formatting.render_fill(b.suggestions))
        for i, b in enumerate(blocks) if b.type in TEXTLIKE
    }
    sponsor_paras = formatting.to_paragraphs(sponsored, keep=True) if sponsored else []
    dumped = [b.model_dump() for b in blocks]
    warnings = formatting.lint(title, dumped, tags, sponsored)
    to_fill = formatting.count_placeholders(title, dumped)
    subtitles = [formatting.clean_line(b.text or "") for b in blocks if b.type == "subtitle"]
    body_text = "\n".join(
        [ln for p in sponsor_paras for ln in p]
        + [formatting.visible(ln) for i, b in enumerate(blocks) if b.type == "text" for p in paragraphs[i] for ln in p]
    )
    overlaps = archive.overlap_warnings(title, main_keyword, hook_pattern, subtitles, body_text)

    digest = _digest(title, blocks)
    plan = {
        "title": title,
        "sponsored": sponsored,
        "blocks": [
            {"type": b.type,
             **({"paragraphs": ["\n".join(p) for p in paragraphs[i]]} if b.type in TEXTLIKE else {}),
             **({"topic": b.topic} if b.type == "fill" else {}),
             **({"text": b.text, "style": b.style or config.SUBTITLE_STYLE} if b.type == "subtitle" else {}),
             **({"lines": _quote_lines(b), "style": b.style or CLOSING_STYLES[0]} if b.type == "quote" else {}),
             **({"file": str(uploads[i]), "caption": b.caption} if b.type == "image" else {}),
             **({"query": b.query, "address": b.address} if b.type == "place" else {})}
            for i, b in enumerate(blocks)
        ],
        "tags": tags or [],
        "align": config.TEXT_ALIGN,
        "main_keyword": main_keyword,
        "hook_pattern": hook_pattern,
        "to_fill_before_publish": to_fill,
        "compared_with": [f"{p['source']}: {p.get('title')}" for p in archive.comparison_posts()],
        "lint_warnings": warnings,
        "overlap_warnings": overlaps,
        "place_review_notes": place_notes,
    }
    if dry_run:
        return ["검증 통과 (dry_run, 브라우저는 열지 않았습니다). lint_warnings와 overlap_warnings를 확인하고 고치세요.",
                json.dumps(plan, ensure_ascii=False, indent=1)]

    used = history.drafts_today()
    if used >= config.DAILY_DRAFT_LIMIT:
        raise RuntimeError(
            f"오늘 임시저장 {used}건으로 하루 한도({config.DAILY_DRAFT_LIMIT}건)에 도달했습니다. "
            "자동화는 하루 1~2건을 권장합니다. 꼭 필요하면 사용자가 DAILY_DRAFT_LIMIT 설정을 직접 바꿔야 합니다."
        )
    dup = history.recent_duplicate(digest)
    if dup and not force:
        raise RuntimeError(f"{dup['time']}에 같은 내용을 이미 임시저장했습니다. 정말 다시 하려면 사용자 확인 후 force=true로 호출하세요.")

    notes: list[str] = list(place_notes)
    expected: list[str] = [title]
    counts = {"image": 0, "subtitle": 0, "divider": 0, "place": 0}
    layout: list[str] = []
    total = len(blocks) + 4

    async with browser_session() as (_ctx, page):
        step = "글쓰기 페이지 열기"
        try:
            await _progress(ctx, 0, total, step)
            frame = await editor.open_editor(page)
            if any("작성 중인" in c for c in editor.LAST_OPEN_CLOSED):
                notes.append("글쓰기 화면에 저장하지 않고 편집하다 만 내용이 있다는 창이 떠서 '취소'를 누르고 새 글로 시작했습니다. "
                             "네이버 '저장' 목록의 임시저장 글은 그대로 있고, 이 글은 따로 저장됩니다.")
            step = "제목 입력"
            await editor.set_title(frame, title)
            step = "본문 시작 위치 클릭"
            await editor.focus_body_start(frame)
            if sponsor_paras:
                step = "협찬 표기 입력"
                await editor.write_paragraphs(frame, sponsor_paras, align=config.TEXT_ALIGN)
                expected += [ln for p in sponsor_paras for ln in p]
                layout.append("text")
            prev: str | None = None
            for idx, b in enumerate(blocks):
                step = f"{idx + 1}번 블록({b.type})"
                log.info("write_post: %s", step)
                await _progress(ctx, idx + 1, total, f"{step} 입력 중")
                await editor.dismiss_popups(frame)
                if prev in NON_TEXT:
                    await editor.move_to_end(frame)
                if b.type in NON_TEXT:
                    await editor.prepare_block_slot(frame)  # 블록 아래에 들어갈 빈 문단을 미리 만듦
                if b.type in TEXTLIKE:
                    await editor.write_paragraphs(frame, paragraphs[idx], align=config.TEXT_ALIGN)
                    expected += [formatting.visible(ln) for p in paragraphs[idx] for ln in p]
                elif b.type == "quote":
                    qlines = _quote_lines(b)
                    await editor.insert_quote(frame, qlines, style=b.style or CLOSING_STYLES[0])
                    expected += [formatting.visible(x) for x in qlines]
                    counts["subtitle"] += 1
                elif b.type == "subtitle":
                    await editor.insert_subtitle(frame, formatting.clean_line(b.text or ""), style=b.style or config.SUBTITLE_STYLE)
                    expected.append(b.text or "")
                    counts["subtitle"] += 1
                elif b.type == "divider":
                    await editor.insert_divider(frame)
                    counts["divider"] += 1
                elif b.type == "image":
                    await editor.insert_image(frame, uploads[idx], caption=formatting.clean_line(b.caption) if b.caption else None)
                    counts["image"] += 1
                    if b.caption:
                        expected.append(b.caption)
                elif b.type == "place":
                    try:
                        picked = await editor.insert_place(frame, b.query or "", address=b.address)
                        counts["place"] += 1
                        if picked.get("note"):
                            notes.append(f"장소: {picked['note']}")
                        if b.address and not picked["address_matched"]:
                            notes.append(f"장소 '{b.query}': 주소가 같은 검색 결과가 없어 첫 결과('{picked['picked']}')를 넣었습니다. 발행 전에 확인하세요.")
                    except editor.EditorError as e:
                        notes.append(f"장소 '{b.query}' 첨부 실패: {e}. 자리 표시 문구를 넣었습니다. 발행 전에 직접 지도를 넣으세요.")
                        await editor.move_to_end(frame)
                        line = f"📍 {b.query}" + (f" ({b.address})" if b.address else "")
                        await editor.write_paragraphs(frame, [[line]], align=config.TEXT_ALIGN)
                        expected.append(line)
                        b = Block(type="text", text=line)
                prev = "text" if b.type in TEXTLIKE else "subtitle" if b.type == "quote" else b.type
                layout.append(prev)
                await editor.dismiss_popups(frame)
            if tags:
                step = "본문 해시태그 입력"
                await editor.write_hashtags(frame, tags)
                layout.append("text")  # 태그는 본문 끝 문단으로 들어갑니다(마무리 상자 뒤에 오면 별도 문단)
            step = "텍스트 검증"
            await _progress(ctx, total - 2, total, step)
            check = await editor.verify_text(frame, expected, counts, layout=layout)
            step = "임시저장"
            await _progress(ctx, total - 1, total, step)
            await editor.dismiss_popups(frame)
            saved = await editor.save_draft(frame)
            if saved["confirmed"]:
                result = {
                    "ok": True,
                    "saved_confirmed": True,
                    "save_signal": saved["signal"],
                    "message": "임시저장했습니다. 평소 쓰는 브라우저에서 글쓰기 화면의 '저장' 목록을 열어 확인하고, "
                               "직접 입력할 자리를 채운 뒤 카테고리를 고르고 직접 발행하세요.",
                }
            else:
                result = {
                    "ok": False,
                    "saved_confirmed": False,
                    "save_signal": saved["signal"],
                    "message": "저장 버튼은 눌렀지만 저장 완료를 확인하지 못했습니다. 사용자가 글쓰기 화면의 '저장' 목록에서 "
                               "글이 있는지 직접 확인해야 합니다. 확인 전에는 다시 시도하지 마세요(중복 저장 위험).",
                }
            result.update({"verify": check, "to_fill_before_publish": to_fill, "notes": notes})
            if to_fill["직접_입력"] or to_fill["확인필요"]:
                topics = [b.topic for b in blocks if b.type == "fill" and b.topic]
                result["user_todo"] = (f"임시저장 글에서 '{formatting.PLACEHOLDER}' {to_fill['직접_입력']}곳을 직접 쓰고"
                                       + (f"({', '.join(topics)})" if topics else "")
                                       + f", '{formatting.SUGGESTION_PREFIX}' 줄 {to_fill['추천_문구_줄']}개는 골라 고쳐 쓰거나 지우고, "
                                       f"[[확인필요]] {to_fill['확인필요']}곳을 확인해 고친 뒤 발행하세요.")
            if not check["ok"]:
                result["next"] = "검증에서 불일치가 있습니다. 자동으로 다시 시도하지 않습니다. 사용자에게 결과를 보여 주고 재시도 여부를 물으세요."
            history.append({"digest": digest, "title": title, "mode": "draft", "ok": saved["confirmed"],
                            "unconfirmed": not saved["confirmed"]})
            if saved["confirmed"]:
                archive.save({"title": title, "main_keyword": main_keyword, "hook_pattern": hook_pattern,
                              "subtitles": subtitles, "tags": tags or [], "body": body_text})
            if overlaps:
                result["overlap_warnings"] = overlaps
            await _progress(ctx, total, total, "완료")
            if review_seconds > 0:
                await page.wait_for_timeout(min(review_seconds, 600) * 1000)
            return [json.dumps(result, ensure_ascii=False, indent=1)]
        except NotLoggedInError as e:
            return [f"FAILED: {e}"]
        except editor.PublishBlockedError as e:
            log.error("발행 차단: %s", e)
            return [f"FAILED at step '{step}': {e} 작업을 중단했습니다. 이 동작은 우회하지 마세요."]
        except Exception as e:
            log.exception("write_post 실패 (%s)", step)
            shot_path = config.ERROR_DIR / f"error_{time.strftime('%Y%m%d_%H%M%S')}.png"
            shot: bytes | None = None
            try:
                shot = await page.screenshot(path=str(shot_path))
            except Exception:
                shot_path = None  # type: ignore[assignment]
            history.append({"digest": digest, "title": title, "mode": "draft", "ok": False, "step": step, "error": str(e)})
            out: list = [
                f"FAILED at step '{step}': {e}\n"
                f"스크린샷 파일: {shot_path}\n"
                "같은 요청을 바로 반복하지 마세요. 에디터에 작성 중인 내용이 남았을 수 있으니 사용자가 확인해야 합니다. "
                "셀렉터 문제로 보이면 inspect_editor로 진단하세요."
            ]
            if shot and config.RETURN_ERROR_SCREENSHOT:
                out.append(Image(data=shot, format="png"))
            return out
