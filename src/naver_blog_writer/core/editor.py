"""네이버 SmartEditor ONE 조작.

흐름: 글쓰기 페이지 열기 -> 가드와 팝업 처리기 확인 -> 제목 -> 블록 순서대로 입력 -> 텍스트 검증(1회) -> 임시저장

원칙
- 셀렉터는 selectors.py에서만 관리합니다.
- 발행 버튼은 가드 스크립트와 safe_click 두 겹으로 막습니다. 발행 기능은 코드에 없습니다.
- 고정 좌표 클릭은 쓰지 않습니다. 요소 기준 클릭은 결과(커서 위치)를 확인할 때만 씁니다.
"""

from __future__ import annotations

import asyncio
import logging
import re
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any

from playwright.async_api import Frame, Locator, Page
from playwright.async_api import TimeoutError as PWTimeout

from .. import config
from . import selectors as S
from .browser import NotLoggedInError, publish_guard_js

log = logging.getLogger(__name__)

# 줄 끝으로 커서 이동. macOS 크롬에서 End 키는 화면 스크롤만 하므로 Cmd+→를 씁니다.
LINE_END_KEY = "Meta+ArrowRight" if sys.platform == "darwin" else "End"
BLOCKLIST_SEL = ", ".join(S.PUBLISH_BLOCKLIST)


class EditorError(RuntimeError):
    pass


class PublishBlockedError(EditorError):
    pass


class PlaceNotFoundError(EditorError):
    """장소 검색이 실행됐는데 결과가 0건이거나, 주소가 같은 항목이 없는 경우."""


# ---------------------------------------------------------------- 공통 유틸

async def first_visible(scope: Frame | Page | Locator, selectors: list[str], timeout_ms: int = 0,
                        names: list[str] | None = None) -> Locator | None:
    """셀렉터 목록, 그다음 버튼 이름(names) 순서로 처음 보이는 요소를 찾습니다."""
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        for sel in selectors:
            try:
                loc = scope.locator(sel)
                for i in range(min(await loc.count(), 10)):
                    el = loc.nth(i)
                    if await el.is_visible():
                        return el
            except Exception:
                continue
        for name in names or []:
            try:
                loc = scope.get_by_role("button", name=name)
                for i in range(min(await loc.count(), 10)):
                    el = loc.nth(i)
                    if await el.is_visible():
                        return el
            except Exception:
                continue
        if time.monotonic() >= deadline:
            return None
        await asyncio.sleep(0.3)


async def safe_click(el: Locator, **kwargs: Any) -> None:
    """발행 관련 요소는 절대 누르지 않습니다."""
    if await el.evaluate("(e, sel) => !!e.closest(sel)", BLOCKLIST_SEL):
        raise PublishBlockedError("발행 관련 버튼 클릭을 차단했습니다. 발행은 사용자가 직접 합니다.")
    await el.click(**kwargs)


# 4바이트 문자(대부분의 이모지)와 뒤따르는 변형 선택자. SmartEditor에 이모지가 섞인 문장을 한 번에 넣으면
# 이모지만 남고 나머지 글자가 사라지므로(실제 에디터에서 확인), 이모지 앞뒤로 나눠 입력합니다.
_ASTRAL = re.compile("([\U00010000-\U0010FFFF][\uFE0F\u200D]*)")
# 2바이트 기호 뒤의 변형 선택자(✔️ ❤️ ☀️의 U+FE0F). 이것이 들어가면 따로 나눠 넣어도 그다음 입력과 Enter가 사라집니다
# (실제 에디터에서 확인). 선택자 없는 기호(✔ ❤ ☀)는 정상 입력되므로 입력 전에 선택자만 지웁니다.
_BMP_VS16 = re.compile("(?<=[\u0000-\uFFFF])\uFE0F")


def editor_text(text: str) -> str:
    """에디터에 실제로 넣을 글자(2바이트 기호 뒤 변형 선택자 제거). 검증 기대값도 이 함수로 맞춥니다."""
    return _BMP_VS16.sub("", text)


async def type_text(page: Page, text: str) -> None:
    text = editor_text(text)
    if not text:
        return
    if config.TYPING_MODE == "type":
        await page.keyboard.type(text, delay=12)
        return
    for part in _ASTRAL.split(text):
        if part:
            await page.keyboard.insert_text(part)


# 줄 안의 **굵게** 표시. 에디터의 굵게 단축키(Cmd/Ctrl+B)로 켜고 끕니다(실제 에디터에서 확인).
BOLD_MARK = re.compile(r"\*\*(.+?)\*\*")
BOLD_KEY = "Meta+b" if sys.platform == "darwin" else "Control+b"
# 굵게 단축키를 누른 직후 에디터가 상태를 바꾸는 동안 들어온 글자와 Enter는 버려집니다(실제 에디터에서 24줄 중 7~15줄 손실).
# 켜고 끌 때마다 잠깐 기다립니다.
BOLD_SETTLE_S = 0.15
# 줄 사이 간격. 에디터가 잠깐 바쁜 순간(굵게 전환 직후 등)에 빠르게 넣은 줄과 Enter가 통째로 버려지는 것을 막습니다
# (실제 에디터: 간격 없이 24줄 중 1~15줄 손실, 줄마다 0.1~0.15초 간격이면 손실 없음).
LINE_PACE_S = 0.12


def strip_marks(line: str) -> str:
    """입력 결과로 화면에 보일 글자(굵게 표시 ** 제거)."""
    return BOLD_MARK.sub(r"\1", line)


async def type_line(frame: Frame, line: str) -> None:
    """한 줄을 입력합니다. **글자** 구간만 굵게 켜고 끕니다."""
    page = frame.page
    pos = 0
    for m in BOLD_MARK.finditer(line):
        await type_text(page, line[pos:m.start()])
        await page.keyboard.press(BOLD_KEY)
        await asyncio.sleep(BOLD_SETTLE_S)
        await type_text(page, m.group(1))
        await page.keyboard.press(BOLD_KEY)
        await asyncio.sleep(BOLD_SETTLE_S)
        pos = m.end()
    await type_text(page, line[pos:])


# ---------------------------------------------------------------- 에디터 열기와 팝업

async def find_editor_frame(page: Page, timeout_s: float = 25) -> Frame | None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if "nid.naver.com" in page.url:  # 글쓰기 주소는 몇 초 뒤 자바스크립트로 로그인 페이지로 넘어갑니다.
            raise NotLoggedInError("로그인이 필요합니다. open_login_window 도구로 먼저 로그인하세요.")
        for f in page.frames:
            try:
                for sel in S.EDITOR_READY:
                    if await f.locator(sel).count() > 0:
                        return f
            except Exception:
                continue
        await asyncio.sleep(0.5)
    return None


async def dismiss_popups(frame: Frame) -> list[str]:
    """'작성 중인 글이 있습니다'(취소)와 도움말 패널을 닫습니다.

    이 팝업은 글쓰기 중간에도 뜰 수 있어서 블록 입력 전후와 저장 직전에 매번 호출합니다.
    (Playwright locator handler는 팝업이 사라진 뒤 30초 대기가 생겨 쓰지 않습니다.)
    """
    closed: list[str] = []
    for _ in range(3):
        clicked = False
        for group, name in ((S.POPUP_DRAFT_CANCEL, "작성 중인 글 팝업 취소"), (S.POPUP_HELP_CLOSE, "도움말 닫기")):
            el = await first_visible(frame, group)
            if el:
                try:
                    await safe_click(el, timeout=2000)
                    closed.append(name)
                    clicked = True
                    await asyncio.sleep(0.4)
                except PublishBlockedError:
                    raise
                except Exception:
                    pass
        if not clicked:
            break
    if closed:
        log.info("닫은 팝업: %s", closed)
    return closed


async def ensure_guard(frame: Frame) -> None:
    ok = False
    try:
        ok = await frame.evaluate("window.__nbwGuard === true")
        if not ok:
            await frame.evaluate(publish_guard_js())
            ok = await frame.evaluate("window.__nbwGuard === true")
    except Exception as e:
        log.error("발행 가드 확인 실패: %s", e)
    if not ok:
        raise EditorError("발행 차단 가드를 설치하지 못해 작업을 중단했습니다.")


# open_editor가 에디터를 열면서 닫은 팝업 이름. write_post가 결과에 안내합니다.
LAST_OPEN_CLOSED: list[str] = []
DRAFT_POPUP_WAIT_S = 4.0


async def settle_draft_popup(frame: Frame, timeout_s: float = DRAFT_POPUP_WAIT_S) -> list[str]:
    """에디터가 열린 뒤 '작성 중인 글이 있습니다' 창이 뜨는지 timeout_s초 동안 지켜보고, 뜨면 '취소'를 누릅니다.

    이 창은 에디터가 보이고 1~3초 뒤에 늦게 뜨며 화면 전체를 덮어 제목 칸 클릭을 막습니다(실제 에디터에서 확인).
    '취소'는 저장하지 않고 편집하다 만 내용(브라우저 자동 보관)을 버리고 새 글로 시작한다는 뜻입니다.
    네이버 '저장' 목록의 임시저장 글은 지워지지 않으므로 항상 새 글로 따로 저장됩니다.
    """
    closed: list[str] = []
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        got = await dismiss_popups(frame)
        closed += got
        if any("작성 중인" in g for g in got):
            break
        await asyncio.sleep(0.3)
    return closed


async def open_editor(page: Page) -> Frame:
    if not config.BLOG_ID:
        raise EditorError("NAVER_BLOG_ID 환경 변수가 비어 있습니다.")
    for tpl in S.WRITE_URLS:
        url = tpl.format(blog_id=config.BLOG_ID)
        log.info("글쓰기 페이지 이동: %s", url)
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        if "nid.naver.com" in page.url:
            raise NotLoggedInError("로그인이 필요합니다. open_login_window 도구로 먼저 로그인하세요.")
        frame = await find_editor_frame(page)
        if frame:
            await ensure_guard(frame)
            closed = await settle_draft_popup(frame)
            LAST_OPEN_CLOSED[:] = closed
            return frame
    raise EditorError("에디터를 찾지 못했습니다. selectors.py의 WRITE_URLS와 EDITOR_READY를 확인하세요.")


# ---------------------------------------------------------------- 커서 위치

_CARET_JS = """
() => {
  const sel = document.getSelection();
  if (!sel || !sel.anchorNode) return null;
  const node = sel.anchorNode.nodeType === 1 ? sel.anchorNode : sel.anchorNode.parentElement;
  const comp = node && node.closest('.se-component');
  if (!comp) return null;
  const all = [...document.querySelectorAll('.se-component')];
  return {index: all.indexOf(comp), cls: comp.className, total: all.length};
}
"""


async def caret_info(frame: Frame) -> dict | None:
    try:
        return await frame.evaluate(_CARET_JS)
    except Exception:
        return None


async def _last_component(frame: Frame) -> tuple[Locator, str, int]:
    comps = frame.locator(S.COMPONENT)
    n = await comps.count()
    if n == 0:
        raise EditorError("에디터 컴포넌트를 찾지 못했습니다.")
    last = comps.nth(n - 1)
    return last, (await last.get_attribute("class")) or "", n


async def _caret_in_text_after(frame: Frame, index: int) -> bool:
    info = await caret_info(frame)
    return bool(info) and info["index"] > index and S.TEXT_COMPONENT_CLASS in info["cls"].split()


IMAGE_CLASSES = ("se-image", "se-imageStrip", "se-imageGroup")


async def click_paragraph_end(para: Locator, wait_s: float = 3, frame: Frame | None = None) -> None:
    """문단 안에서 다른 요소에 가리지 않은 자리를 골라 눌러 커서를 그 줄에 넣습니다.

    가운데 정렬 문단의 한가운데는 글자 위라서 에디터의 선택 표시 레이어(.se-selection)가 클릭을 가로막고,
    사진을 넣은 직후에는 오른쪽 사이드바가 문단 오른쪽 끝을 가립니다(둘 다 실제 에디터에서 확인).
    그래서 같은 문단의 오른쪽 끝, 왼쪽 끝, 가운데를 차례로 시험 클릭(trial)해 보고 누를 수 있는 자리만 누릅니다.
    세 자리가 모두 막혀 있으면 frame의 팝업('작성 중인 글' 등)을 닫고 wait_s초까지 다시 시도합니다.
    호출한 쪽은 이어서 줄 끝 키를 누르고, 결과(블록 배치)는 verify_text가 확인합니다.
    """
    await para.scroll_into_view_if_needed()
    box = await para.bounding_box()
    if not box:
        await safe_click(para)
        return
    y = box["height"] / 2
    deadline = time.monotonic() + wait_s
    while True:
        for x in (max(box["width"] - 3, 1), 3, box["width"] / 2):
            try:
                await para.click(position={"x": x, "y": y}, trial=True, timeout=1500)
            except PWTimeout:
                continue
            await safe_click(para, position={"x": x, "y": y})
            return
        if time.monotonic() >= deadline:
            raise EditorError("문단이 다른 요소에 가려 커서를 넣지 못했습니다. 화면에 열린 창이 있는지 확인하세요.")
        if frame is not None:
            await dismiss_popups(frame)
        await asyncio.sleep(0.5)


async def _enter_text_after(frame: Frame, index: int) -> bool:
    """index 다음에 있는 텍스트 블록의 마지막 문단을 직접 눌러 커서를 넣고, 들어갔는지 확인합니다."""
    comps = frame.locator(S.COMPONENT)
    n = await comps.count()
    for i in range(index + 1, n):
        comp = comps.nth(i)
        if S.TEXT_COMPONENT_CLASS in ((await comp.get_attribute("class")) or "").split():
            para = comp.locator(S.TEXT_PARAGRAPH).last
            await click_paragraph_end(para, frame=frame)
            await frame.page.keyboard.press(LINE_END_KEY)
            await asyncio.sleep(0.2)
            return await _caret_in_text_after(frame, index)
    return False


async def prepare_block_slot(frame: Frame) -> None:
    """사진, 소제목, 구분선, 지도를 넣기 직전에 호출합니다. 블록 아래에 들어갈 빈 문단을 미리 만들어 둡니다.

    SmartEditor는 블록 안(특히 인용구)에서 아래 문단으로 빠져나가는 키 조작이 없습니다. 인용구 안에서 Enter를 누르면
    소제목이 쪼개집니다. 대신 텍스트 문단 중간에 블록을 넣으면 텍스트 블록이 위아래로 나뉘어 블록 아래에 빈 문단이 남습니다.
    그래서 Enter로 빈 줄을 하나 더 만들고 그 위 문단에 블록을 넣습니다(실제 에디터에서 인용구, 구분선으로 확인).

    시작 전에 항상 마지막 문단을 직접 눌러 커서를 확정합니다. 글을 입력한 직후의 키보드 위치를 그대로 쓰면
    에디터 내부 커서가 그 텍스트 블록의 맨 앞에 남아 있는 경우가 있어, 블록이 문단 위에 들어갔습니다(실제 에디터에서 확인).

    위 문단으로는 ArrowUp이 아니라 그 문단을 직접 눌러 올라갑니다. 46블록 글에서 Enter 직후의 ArrowUp이 버려져
    마무리 상자가 맨 끝 빈 문단을 대체했고, 아래에 해시태그를 쓸 문단이 남지 않아 저장 전에 멈췄습니다(실제 에디터에서 확인).
    인용구와 사진은 커서가 있는 빈 문단을 대체해서 들어가므로, 그 아래 문단이 남는지는 ensure_text_after()로 확인합니다.
    """
    page = frame.page
    await move_to_end(frame)
    last, _cls, _n = await _last_component(frame)
    paras = last.locator(S.TEXT_PARAGRAPH)
    before = await paras.count()
    await asyncio.sleep(LINE_PACE_S)
    await page.keyboard.press("Enter")
    deadline = time.monotonic() + 3
    while await paras.count() <= before:
        if time.monotonic() >= deadline:
            raise EditorError("블록 아래에 둘 빈 문단을 만들지 못했습니다(Enter가 반영되지 않음).")
        await asyncio.sleep(0.2)
    await asyncio.sleep(LINE_PACE_S)
    await click_paragraph_end(paras.nth(before - 1), frame=frame)
    await page.keyboard.press(LINE_END_KEY)
    await asyncio.sleep(0.2)


async def ensure_text_after(frame: Frame, what: str) -> None:
    """방금 넣은 블록 아래에 텍스트 문단이 남았는지 확인합니다. 없으면 다음 입력을 이어 갈 수 없으므로 여기서 멈춥니다."""
    _last, cls, _n = await _last_component(frame)
    if S.TEXT_COMPONENT_CLASS not in cls.split():
        raise EditorError(f"{what} 아래에 이어 쓸 문단이 남지 않았습니다(블록이 맨 끝 빈 문단을 대체함). 저장하지 않고 멈춥니다.")


async def move_to_end(frame: Frame) -> None:
    """커서를 문서 맨 끝의 텍스트 문단으로 옮깁니다.

    - 마지막 블록이 텍스트: 그 마지막 문단을 눌러 줄 끝으로 갑니다. prepare_block_slot을 거쳐 넣은 블록 뒤에는
      항상 빈 텍스트 블록이 있으므로 보통 이 경우입니다.
    - 마지막 블록이 사진: 사진을 선택하고 아래 방향키로 아래 문단으로 갑니다(실제 에디터에서 확인한 동작).
    - 그 밖(인용구, 지도, 구분선): 블록 안에서 Enter를 누르면 내용을 망가뜨리므로 시도하지 않고 중단합니다.
    """
    page = frame.page
    last, cls, n = await _last_component(frame)
    if S.TEXT_COMPONENT_CLASS in cls.split():
        para = last.locator(S.TEXT_PARAGRAPH).last
        await click_paragraph_end(para, frame=frame)
        await page.keyboard.press(LINE_END_KEY)
        return
    if any(k in cls.split() for k in IMAGE_CLASSES):
        await safe_click(last)
        await asyncio.sleep(0.3)
        await page.keyboard.press("ArrowDown")
        await asyncio.sleep(0.4)
        if await _enter_text_after(frame, n - 1):
            return
    raise EditorError("블록 다음 문단으로 커서를 옮기지 못했습니다. 블록을 넣기 전에 prepare_block_slot을 호출했는지 확인하세요.")


# ---------------------------------------------------------------- 입력

async def set_title(frame: Frame, title: str) -> None:
    el = await first_visible(frame, S.TITLE_PARAGRAPH, timeout_ms=10000)
    if not el:
        raise EditorError("제목 입력란을 찾지 못했습니다.")
    await click_paragraph_end(el, wait_s=10, frame=frame)  # 늦게 뜬 '작성 중인 글' 창이 막으면 닫고 다시 누릅니다
    await type_text(frame.page, title)


async def focus_body_start(frame: Frame) -> None:
    el = await first_visible(frame, S.BODY_FIRST_PARAGRAPH, timeout_ms=10000)
    if not el:
        raise EditorError("본문 입력란을 찾지 못했습니다.")
    await click_paragraph_end(el, wait_s=10, frame=frame)
    await frame.page.keyboard.press(LINE_END_KEY)


_PARA_ALIGN_JS = """
() => {
  const s = document.getSelection();
  if (!s || !s.anchorNode) return null;
  const n = s.anchorNode.nodeType === 1 ? s.anchorNode : s.anchorNode.parentElement;
  const p = n && n.closest('.se-text-paragraph');
  if (!p) return null;
  const m = p.className.match(/se-text-paragraph-align-(\\w+)/);
  return m ? m[1] : 'left';
}
"""


async def set_align(frame: Frame, align: str) -> None:
    """지금 커서가 있는 문단의 정렬을 바꿉니다. 이미 그 정렬이면 아무것도 하지 않습니다.

    정렬은 Enter로 만든 다음 문단에 그대로 이어집니다(실제 에디터에서 확인). 그래서 글 묶음마다 한 번만 확인합니다.
    """
    if align not in ("left", "center", "right", "justify"):
        raise EditorError(f"알 수 없는 정렬입니다: {align}")
    try:
        if await frame.evaluate(_PARA_ALIGN_JS) == align:
            return
    except Exception:
        pass
    # 커서가 제목에서 본문으로 옮겨 가면 서식 툴바가 다시 그려집니다(제목용 'without-right' 메뉴 → 본문용 'with-justify').
    # 그래서 지금 열린 메뉴와 이름(data-name)이 같은 항목만 고르고, 그리는 도중에 요소가 사라지면 한 번 더 찾습니다.
    for _ in range(2):
        dd = await first_visible(frame, S.ALIGN_DROPDOWN, timeout_ms=5000)
        if not dd:
            raise EditorError("툴바의 '정렬' 버튼을 찾지 못했습니다.")
        name = await dd.get_attribute("data-name") or ""
        await safe_click(dd)
        opts = ([f"button[data-name='{name}'][data-value='{align}']"] if name else []) + [s.format(align=align) for s in S.ALIGN_OPTION]
        opt = await first_visible(frame, opts, timeout_ms=3000)
        if not opt:
            raise EditorError(f"정렬 메뉴에서 '{align}' 항목을 찾지 못했습니다.")
        try:
            await safe_click(opt, timeout=4000)
        except PWTimeout:
            await asyncio.sleep(0.5)
            continue
        await asyncio.sleep(0.2)
        return
    raise EditorError("정렬 메뉴가 계속 다시 그려져 정렬을 바꾸지 못했습니다.")


async def write_paragraphs(frame: Frame, paragraphs: list[list[str]], align: str | None = None) -> None:
    """문단(빈 줄로 나뉜 묶음)마다 줄을 입력하고, 묶음 뒤에 빈 줄을 하나 둡니다.

    줄 안의 **글자**는 굵게 입력합니다. align을 주면 입력 전에 정렬을 맞춥니다.
    """
    page = frame.page
    if align:
        await set_align(frame, align)
    for lines in paragraphs:
        for line in lines:
            await asyncio.sleep(LINE_PACE_S)
            await type_line(frame, line)
            await asyncio.sleep(LINE_PACE_S)
            await page.keyboard.press("Enter")
        await asyncio.sleep(LINE_PACE_S)
        await page.keyboard.press("Enter")


async def _click_toolbar(frame: Frame, selectors: list[str], names: list[str], what: str) -> None:
    await dismiss_popups(frame)  # '작성 중인 글' 창은 화면 전체를 덮어 툴바 클릭도 막습니다
    btn = await first_visible(frame, selectors, timeout_ms=5000, names=names)
    if not btn:
        raise EditorError(f"툴바의 '{what}' 버튼을 찾지 못했습니다.")
    await safe_click(btn)


async def _wait_count_increase(frame: Frame, selector: str, before: int, timeout_s: float, what: str) -> Locator:
    loc = frame.locator(selector)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if await loc.count() > before:
            return loc.nth(await loc.count() - 1)
        await asyncio.sleep(0.4)
    raise EditorError(f"{what}이(가) {timeout_s:.0f}초 안에 추가되지 않았습니다.")


QUOTE_STYLES = ("default", "quotation_line", "quotation_bubble", "quotation_underline", "quotation_postit", "quotation_corner")


async def insert_subtitle(frame: Frame, text: str, style: str = "quotation_line") -> None:
    """소제목은 인용구 컴포넌트로 넣습니다. style은 인용구 모양입니다(상위 블로그는 대부분 quotation_line)."""
    await insert_quote(frame, [text], style)


async def insert_quote(frame: Frame, lines: list[str], style: str = "quotation_bubble") -> None:
    """인용구 하나에 여러 줄을 넣습니다(마무리 상자). 인용구 안의 Enter는 같은 인용구 안에 새 줄을 만듭니다
    (말풍선, 포스트잇, 프레임에서 실제 에디터로 확인). 줄 안의 **글자**는 굵게 들어갑니다."""
    if style not in QUOTE_STYLES:
        raise EditorError(f"알 수 없는 인용구 모양입니다: {style}")
    before = await frame.locator(S.QUOTE_COMPONENT).count()
    if style == "default":
        await _click_toolbar(frame, S.QUOTE_BUTTON, S.QUOTE_BUTTON_NAMES, "인용구")
    else:
        await _click_toolbar(frame, S.QUOTE_STYLE_DROPDOWN, [], "인용구 선택")
        opt = await first_visible(frame, [s.format(style=style) for s in S.QUOTE_STYLE_OPTION], timeout_ms=3000)
        if not opt:
            raise EditorError(f"인용구 모양 메뉴에서 '{style}'을 찾지 못했습니다.")
        await safe_click(opt)
    quote = await _wait_count_increase(frame, S.QUOTE_COMPONENT, before, 8, "인용구")
    target = await first_visible(quote, S.QUOTE_TEXT, timeout_ms=2000)
    if target:
        await safe_click(target)
    for i, line in enumerate(lines):
        if i:
            await asyncio.sleep(LINE_PACE_S)
            await frame.page.keyboard.press("Enter")
            await asyncio.sleep(LINE_PACE_S)
        await type_line(frame, line)


async def insert_video(frame: Frame, path: Path, title: str, timeout_s: float = 600) -> None:
    """동영상 업로더로 영상을 올려 본문에 영상 블록을 넣습니다(실제 에디터에서 확인한 순서).

    '동영상' 버튼 → '동영상 업로더' 창 → '동영상 추가'로 파일 선택 → '업로드 완료'가 뜰 때까지 대기 →
    제목(필수, 최대 40자) 입력 → '완료'. 실패하면 창을 닫고 EditorError를 던집니다.
    """
    page = frame.page
    before = await frame.locator(S.VIDEO_COMPONENT).count()
    await _click_toolbar(frame, S.VIDEO_BUTTON, S.VIDEO_BUTTON_NAMES, "동영상")
    popup = await first_visible(frame, S.VIDEO_POPUP, timeout_ms=6000)
    if not popup:
        raise EditorError("동영상 업로더 창이 열리지 않았습니다.")
    try:
        local = await first_visible(popup, S.VIDEO_LOCAL_BUTTON, timeout_ms=4000)
        if not local:
            raise EditorError("동영상 업로더의 '동영상 추가' 버튼을 찾지 못했습니다.")
        async with page.expect_file_chooser(timeout=8000) as fc:
            await safe_click(local)
        await (await fc.value).set_files(str(path))
        deadline = time.monotonic() + timeout_s
        while True:
            txt = await popup.inner_text()
            if S.VIDEO_DONE_TEXT in txt:
                break
            if re.search(S.VIDEO_FAIL_TEXT, txt):
                raise EditorError(f"동영상 업로드 실패: {path.name}")
            if time.monotonic() > deadline:
                raise EditorError(f"동영상 업로드가 {timeout_s:.0f}초 안에 끝나지 않았습니다: {path.name}")
            await asyncio.sleep(1)
        field = await first_visible(popup, S.VIDEO_TITLE, timeout_ms=4000)
        if not field:
            raise EditorError("동영상 제목 입력칸을 찾지 못했습니다.")
        await safe_click(field)
        await type_text(page, title[:40])
        await asyncio.sleep(0.3)
        submit = await first_visible(popup, S.VIDEO_SUBMIT, timeout_ms=3000)
        if not submit:
            raise EditorError("동영상 업로더의 '완료' 버튼을 찾지 못했습니다.")
        await safe_click(submit)
        await _wait_count_increase(frame, S.VIDEO_COMPONENT, before, 30, "동영상")
    except Exception:
        close = await first_visible(frame, S.VIDEO_CLOSE)
        if close:
            try:
                await safe_click(close, timeout=2000)
            except Exception:
                pass
        raise


async def insert_link(frame: Frame, url: str, timeout_s: float = 10) -> bool:
    """지금 커서가 있는 빈 줄에 주소를 입력하고 Enter를 눌러 링크 미리보기 카드(대표 사진, 제목, 요약)를 만듭니다.

    툴바의 '링크' 버튼은 눌러도 입력 창이 뜨지 않습니다. 대신 본문에 주소만 있는 줄에서 Enter를 누르면 에디터가 카드를 만들고,
    카드 아래에 빈 문단을 남겨 커서를 옮깁니다(실제 에디터에서 확인). 주소 줄은 카드 위에 누를 수 있는 링크로 남습니다.
    카드가 timeout_s 안에 생기지 않으면 False(주소 줄만 남음).
    """
    page = frame.page
    before = await frame.locator(S.OGLINK_COMPONENT).count()
    await asyncio.sleep(LINE_PACE_S)
    await type_text(page, url)
    await asyncio.sleep(LINE_PACE_S)
    await page.keyboard.press("Enter")
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if await frame.locator(S.OGLINK_COMPONENT).count() > before:
            await asyncio.sleep(0.5)
            return True
        await asyncio.sleep(0.4)
    return False


async def insert_divider(frame: Frame) -> None:
    before = await frame.locator(S.DIVIDER_COMPONENT).count()
    await _click_toolbar(frame, S.DIVIDER_BUTTON, S.DIVIDER_BUTTON_NAMES, "구분선")
    await _wait_count_increase(frame, S.DIVIDER_COMPONENT, before, 8, "구분선")


async def insert_image(frame: Frame, path: Path, caption: str | None = None, timeout_s: float = 90) -> None:
    page = frame.page
    before = await frame.locator(S.IMAGE_COMPONENT).count()
    btn = await first_visible(frame, S.IMAGE_BUTTON, timeout_ms=5000, names=S.IMAGE_BUTTON_NAMES)
    if not btn:
        raise EditorError("사진 버튼을 찾지 못했습니다.")
    uploaded = False
    try:
        async with page.expect_file_chooser(timeout=6000) as fc_info:
            await safe_click(btn)
        chooser = await fc_info.value
        await chooser.set_files(str(path))
        uploaded = True
    except PWTimeout:
        for sel in S.FILE_INPUT:
            loc = frame.locator(sel)
            if await loc.count() > 0:
                await loc.first.set_input_files(str(path))
                uploaded = True
                break
    if not uploaded:
        raise EditorError("파일 선택 창이나 파일 입력 요소를 찾지 못했습니다.")
    started = time.monotonic()
    image = await _wait_count_increase(frame, S.IMAGE_COMPONENT, before, timeout_s, f"사진({path.name})")
    await wait_upload_done(frame, image, timeout_s - (time.monotonic() - started), path.name)
    if caption:
        cap = await first_visible(image, S.IMAGE_CAPTION, timeout_ms=1500)
        if not cap:  # 사진 설명칸은 사진을 선택해야 보입니다(실제 에디터에서 확인).
            await safe_click(image.locator("img").first)
            await asyncio.sleep(0.4)
            cap = await first_visible(image, S.IMAGE_CAPTION, timeout_ms=3000)
        if not cap:
            raise EditorError("사진 설명 입력란을 찾지 못했습니다.")
        await safe_click(cap)
        await type_text(page, caption)


_UPLOAD_STATE_JS = """
(comp, sels) => {
  const imgs = [...comp.querySelectorAll('img')];
  const loaded = imgs.some(i => /^https?:/.test(i.currentSrc || i.src) && i.complete && i.naturalWidth > 0);
  const busy = sels.some(s => [...comp.querySelectorAll(s)].some(e => e.offsetParent !== null));
  return {loaded, busy, srcs: imgs.map(i => (i.currentSrc || i.src).slice(0, 40))};
}
"""


async def wait_upload_done(frame: Frame, image: Locator, timeout_s: float, name: str) -> None:
    """사진 블록이 생긴 뒤 실제 업로드가 끝날 때까지 기다립니다.

    끝났다고 보는 조건: 블록 안 이미지가 http(s) 주소로 바뀌어 다 그려졌고(blob:, data: 아님),
    블록 안의 로딩 표시와 화면의 업로드 진행 팝업이 모두 사라졌을 때.
    """
    deadline = time.monotonic() + max(timeout_s, 5)
    state: dict = {}
    while time.monotonic() < deadline:
        try:
            state = await image.evaluate(_UPLOAD_STATE_JS, S.IMAGE_UPLOADING)
        except Exception:
            state = {}
        progress = await first_visible(frame, S.UPLOAD_PROGRESS)
        if state.get("loaded") and not state.get("busy") and not progress:
            await asyncio.sleep(0.3)
            return
        await asyncio.sleep(0.4)
    raise EditorError(f"사진 업로드가 끝나지 않았습니다: {name} (상태: {state})")


async def _place_popup(frame: Frame, timeout_ms: int = 0) -> Locator | None:
    return await first_visible(frame, S.PLACE_POPUP, timeout_ms=timeout_ms)


async def close_place_layer(frame: Frame) -> None:
    """장소 검색 팝업이 열려 있으면 '팝업 닫기' 버튼으로 닫고 닫혔는지 확인합니다(Esc로는 닫히지 않음).

    닫지 못하면 이후 입력이 검색창에 들어가므로 중단합니다.
    """
    for _ in range(3):
        popup = await _place_popup(frame)
        if not popup:
            return
        btn = await first_visible(popup, S.PLACE_LAYER_CLOSE)
        if btn:
            await safe_click(btn)
        else:
            await frame.page.keyboard.press("Escape")
        await asyncio.sleep(0.5)
    if await _place_popup(frame):
        raise EditorError("장소 검색 창을 닫지 못했습니다. 이어서 입력하면 검색창에 글이 들어가므로 중단합니다.")


def _norm_addr(s: str) -> str:
    return re.sub(r"[\s,.\-()]", "", unicodedata.normalize("NFC", s or ""))


async def insert_place(frame: Frame, query: str, address: str | None = None, attempts: int = 3) -> dict[str, Any]:
    """장소(지도) 컴포넌트를 넣습니다. 실패하면 팝업을 닫고 처음부터 다시 시도합니다(최대 3번).

    브라우저를 띄운 뒤 첫 지도 첨부는 에디터의 지도 이미지 생성 요청(staticmap)이 실패해 '확인'이 먹지 않는 경우가 많고,
    같은 창에서 다시 하면 바로 성공합니다(실제 에디터에서 확인). 검색 결과가 없으면 다시 시도하지 않습니다.
    """
    queries = [query]
    words = query.split()
    if len(words) > 1:  # '젠스시 대화동'처럼 여러 단어면, 결과가 없을 때 상호명만으로 다시 검색(주소로 지점 구분)
        queries.append(" ".join(words[:-1]))
    last_error: EditorError | None = None
    for attempt in range(attempts):
        try:
            result = None
            # 1차: 검색어마다 '주소가 같은 항목'이 있을 때만 넣음. 2차: 그래도 없으면 첫 검색어의 첫 결과를 넣고 메모로 알림.
            plans = [(q, True) for q in queries] if address else [(q, False) for q in queries]
            if address:
                plans.append((queries[0], False))
            for q, need_match in plans:
                try:
                    result = await _insert_place_once(frame, q, address, require_match=need_match)
                    break
                except PlaceNotFoundError as e:
                    last_error = e
                    log.info("장소 검색: %s (%s)", e, q)
            if result is None:
                raise last_error or PlaceNotFoundError(f"장소 검색 결과가 없습니다: {query}")
            if len(queries) > 1 and result.get("query") != query:
                result["note"] = f"'{query}' 대신 '{result['query']}'로 검색했습니다."
            return result
        except PlaceNotFoundError:
            raise
        except EditorError as e:
            last_error = e
            log.info("장소 첨부 %d회차 실패, 다시 시도: %s", attempt + 1, e)
            await asyncio.sleep(1.5)
    raise last_error or EditorError("장소 지도를 넣지 못했습니다.")


async def _search_place(frame: Frame, popup: Locator, query: str) -> Locator | None:
    """팝업에서 검색해 첫 결과 항목을 돌려줍니다. 검색이 실제로 0건이면 None.

    '검색' 클릭이 가끔 무시되어 결과 영역에 '검색 전' 안내문이 그대로 남는 경우가 있습니다(실제 에디터에서 확인).
    그때는 검색창을 비우고 다시 입력한 뒤 '검색'을 다시 누르고, 마지막에는 Enter로도 시도합니다.
    """
    page = frame.page
    for attempt in range(3):
        box = await first_visible(popup, S.PLACE_SEARCH_INPUT, timeout_ms=6000)
        if not box:
            raise EditorError("장소 검색창을 찾지 못했습니다.")
        await safe_click(box)
        await box.fill("")
        await type_text(page, query)
        await asyncio.sleep(0.6)
        search = await first_visible(popup, S.PLACE_SEARCH_BUTTON)
        if search and attempt < 2:
            await safe_click(search)
        else:
            await page.keyboard.press("Enter")
        item = await first_visible(popup, S.PLACE_RESULT_ITEM, timeout_ms=6000)
        if item:
            return item
        if not await first_visible(popup, S.PLACE_BEFORE_SEARCH):
            return None  # 검색은 실행됐고 결과가 0건
        log.info("장소 검색이 실행되지 않아 다시 시도합니다(%d회차): %s", attempt + 1, query)
    raise EditorError(f"장소 검색이 실행되지 않았습니다: {query}")


async def _insert_place_once(frame: Frame, query: str, address: str | None = None,
                             require_match: bool = False) -> dict[str, Any]:
    """장소(지도) 컴포넌트를 한 번 넣어 봅니다. 실제 에디터의 장소 팝업 구조에 맞춰 팝업 안에서만 요소를 찾습니다.

    순서: 장소 버튼 → 팝업의 검색창에 입력 → '검색' 버튼 → 결과 항목 중 주소가 맞는 항목의 '추가' → '확인'.
    address(find_place 결과의 주소)가 있으면 주소가 맞는 항목을 고르고, 없거나 맞는 항목이 없으면 첫 번째 결과를 고릅니다.
    실패하면 팝업을 닫고 예외를 던집니다.
    """
    before = await frame.locator(S.PLACE_COMPONENT).count()
    try:
        await _click_toolbar(frame, S.PLACE_BUTTON, S.PLACE_BUTTON_NAMES, "장소")
        popup = await _place_popup(frame, timeout_ms=8000)
        if not popup:
            raise EditorError("장소 검색 팝업이 열리지 않았습니다.")
        first_item = await _search_place(frame, popup, query)
        if not first_item:
            raise PlaceNotFoundError(f"장소 검색 결과가 없습니다: {query}")
        item, matched = first_item, False
        if address:
            want = _norm_addr(address)
            for sel in S.PLACE_RESULT_ITEM:
                items = popup.locator(sel)
                for i in range(min(await items.count(), 20)):
                    cand = items.nth(i)
                    txt = _norm_addr(await cand.inner_text())
                    if want and (want in txt or want[-8:] in txt):
                        item, matched = cand, True
                        break
                if matched:
                    break
        if require_match and not matched:
            raise PlaceNotFoundError(f"검색 결과에 주소가 같은 항목이 없습니다: {query}")
        picked_text = re.sub(r"\s+", " ", await item.inner_text()).strip()
        await item.hover()  # '추가' 버튼은 결과 항목에 마우스를 올려야 보입니다(실제 에디터에서 확인).
        add = await first_visible(item, S.PLACE_RESULT_ADD, timeout_ms=2000)
        if not add:
            raise EditorError("검색 결과의 '추가' 버튼을 찾지 못했습니다.")
        await safe_click(add)
        # '추가'가 적용되면 항목에 se-is-attached가 붙고 '장소 삭제' 버튼이 생깁니다. 적용 전에 누른 '확인'은 무시됩니다.
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            if "se-is-attached" in ((await item.get_attribute("class")) or "") or await first_visible(popup, S.PLACE_ATTACHED):
                break
            await asyncio.sleep(0.3)
        else:
            raise EditorError("장소 '추가'가 적용되지 않았습니다.")
        await asyncio.sleep(1.0)  # 지도 미리보기가 그려질 시간
        ok = await first_visible(popup, S.PLACE_CONFIRM, timeout_ms=4000)
        if not ok:
            raise EditorError("장소 팝업의 '확인' 버튼을 찾지 못했습니다.")
        await safe_click(ok)
        await _wait_count_increase(frame, S.PLACE_COMPONENT, before, 6, "장소 지도")
        return {"picked": picked_text.replace("추가", "").strip()[:80], "address_matched": matched, "query": query}
    except Exception:
        await close_place_layer(frame)
        raise


async def write_hashtags(frame: Frame, tags: list[str]) -> None:
    """본문 끝에 #태그를 입력합니다. SmartEditor ONE은 본문의 #태그를 태그로 인식합니다."""
    page = frame.page
    await move_to_end(frame)
    for t in tags:
        t = re.sub(r"\s+", "", t.lstrip("#"))
        if t:
            await page.keyboard.type(f"#{t} ", delay=20)  # 태그 변환은 실제 키 입력이 필요합니다.


async def _save_count(frame: Frame) -> int | None:
    el = await first_visible(frame, S.SAVE_COUNT)
    if not el:
        return None
    m = re.search(r"\d+", (await el.inner_text()) or "")
    return int(m.group()) if m else None


async def _save_toast_text(frame: Frame) -> str | None:
    pat = re.compile(S.SAVE_DONE_TEXT)
    for scope in (frame, frame.page.main_frame):
        for sel in S.SAVE_TOAST:
            try:
                loc = scope.locator(sel)
                for i in range(min(await loc.count(), 5)):
                    el = loc.nth(i)
                    if await el.is_visible():
                        txt = (await el.inner_text()).strip()
                        if pat.search(txt):
                            return txt[:40]
            except Exception:
                continue
    return None


async def save_draft(frame: Frame, timeout_s: float | None = None) -> dict[str, Any]:
    """저장 버튼을 누르고, 실제로 저장됐는지 신호를 기다립니다.

    신호(하나만 확인되면 성공): 저장 완료 알림 문구, 저장 글 개수 숫자 증가, 저장 요청의 정상 응답.
    시간 안에 아무 신호도 없으면 confirmed=false를 돌려줍니다. 이때는 저장됐다고 보고하지 않습니다.
    """
    page = frame.page
    timeout_s = config.SAVE_CONFIRM_TIMEOUT_S if timeout_s is None else timeout_s
    btn = await first_visible(frame, S.SAVE_BUTTON, timeout_ms=5000)
    if not btn:
        role_btn = frame.get_by_role("button", name=re.compile(r"^\s*저장\s*$"))
        if await role_btn.count() > 0:
            btn = role_btn.first
    if not btn:
        raise EditorError("저장 버튼을 찾지 못했습니다.")

    count_before = await _save_count(frame)
    responses: list[str] = []
    url_pat = re.compile(S.SAVE_RESPONSE_URL)

    def on_response(resp: Any) -> None:
        try:
            if resp.request.method == "POST" and url_pat.search(resp.url) and 200 <= resp.status < 300:
                responses.append(resp.url[:100])
        except Exception:
            pass

    page.on("response", on_response)
    try:
        await safe_click(btn)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            toast = await _save_toast_text(frame)
            if toast:
                return await _settle({"confirmed": True, "signal": f"알림 '{toast}'"}, page)
            after = await _save_count(frame)
            if count_before is not None and after is not None and after > count_before:
                return await _settle({"confirmed": True, "signal": f"저장 글 수 {count_before}→{after}"}, page)
            if responses:
                return await _settle({"confirmed": True, "signal": f"저장 요청 응답 {responses[0]}"}, page)
            await asyncio.sleep(0.3)
        return {"confirmed": False, "signal": f"{timeout_s:.0f}초 안에 저장 완료 신호가 없었습니다."}
    finally:
        page.remove_listener("response", on_response)


async def _settle(result: dict[str, Any], page: Page) -> dict[str, Any]:
    """저장 신호를 받은 뒤 남은 요청이 끝날 시간을 주고 브라우저를 닫게 합니다."""
    try:
        await page.wait_for_load_state("networkidle", timeout=4000)
    except Exception:
        await asyncio.sleep(1.5)
    return result


# ---------------------------------------------------------------- 검증 (텍스트 대조, 1회)

_COLLECT_JS = """
() => [...document.querySelectorAll('.se-component')].map(c => ({cls: c.className, text: c.innerText || ''}))
"""


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFC", s).replace("\u200b", "").replace("\u00a0", " ").replace("\ufe0f", "")
    return re.sub(r"\s+", "", s)


def _kind(cls: str, text: str) -> str | None:
    k = cls.split()
    if "se-documentTitle" in k:
        return None
    if "se-text" in k:
        return "text" if _norm(text) else None
    if "se-quotation" in k:
        return "subtitle"
    if "se-horizontalLine" in k:
        return "divider"
    if any(x in k for x in ("se-image", "se-imageStrip", "se-imageGroup")):
        return "image"
    if "se-placesMap" in k or "se-map" in k:
        return "place"
    if "se-oglink" in k:
        return "link"
    if "se-video" in k:
        return "video"
    return "other"


def collapse_layout(kinds: list[str]) -> list[str]:
    """연속된 text를 하나로 합친 블록 배치 순서."""
    out: list[str] = []
    for k in kinds:
        if k and not (k == "text" and out and out[-1] == "text"):
            out.append(k)
    return out


async def verify_text(frame: Frame, expected: list[str], counts: dict[str, int],
                      layout: list[str] | None = None) -> dict[str, Any]:
    """에디터 전체를 한 번 읽어 (1) 기대 문장이 순서대로 있는지 (2) 블록 개수 (3) 블록 배치 순서를 대조합니다.

    배치 순서는 연속된 문단을 하나로 보고 '글 → 사진 → 글 → 소제목 …'처럼 비교합니다.
    블록이 문단 위에 잘못 들어간 경우를 잡아냅니다. 스크린샷은 쓰지 않습니다.
    """
    comps = await frame.evaluate(_COLLECT_JS)
    full = _norm("".join(c["text"] for c in comps))
    missing: list[str] = []
    pos = 0
    for exp in expected:
        e = _norm(exp)
        if not e:
            continue
        i = full.find(e, pos)
        if i < 0:
            missing.append(exp[:40])
        else:
            pos = i + len(e)

    def count(cls_key: str) -> int:
        return sum(1 for c in comps if cls_key in c["cls"].split())

    actual = {
        "image": sum(1 for c in comps if any(k in c["cls"].split() for k in ("se-image", "se-imageStrip", "se-imageGroup"))),
        "subtitle": count("se-quotation"),
        "divider": count("se-horizontalLine"),
        "place": count("se-placesMap") + count("se-map"),
        "link": count("se-oglink"),
        "video": count("se-video"),
    }
    count_mismatch = {k: {"expected": v, "actual": actual.get(k, 0)} for k, v in counts.items() if actual.get(k, 0) != v}
    out: dict[str, Any] = {
        "checked_sentences": len(expected),
        "missing_or_out_of_order": missing[:10],
        "count_mismatch": count_mismatch,
    }
    layout_ok = True
    if layout is not None:
        want = collapse_layout(layout)
        got = collapse_layout([_kind(c["cls"], c["text"]) for c in comps])
        layout_ok = want == got
        if not layout_ok:
            out["layout_mismatch"] = {"expected": want, "actual": got}
    out["ok"] = not missing and not count_mismatch and layout_ok
    return out


# ---------------------------------------------------------------- 진단

async def inspect(frame: Frame) -> dict[str, Any]:
    """셀렉터가 맞는지 점검하는 진단 정보. 아무것도 클릭하지 않습니다."""
    report: dict[str, Any] = {"frame_url": frame.url, "guard": await frame.evaluate("window.__nbwGuard === true"),
                              "selectors": {}, "buttons_by_name": {}, "components": []}
    groups = {
        "TITLE_PARAGRAPH": S.TITLE_PARAGRAPH, "BODY_FIRST_PARAGRAPH": S.BODY_FIRST_PARAGRAPH,
        "IMAGE_BUTTON": S.IMAGE_BUTTON, "QUOTE_BUTTON": S.QUOTE_BUTTON, "DIVIDER_BUTTON": S.DIVIDER_BUTTON,
        "PLACE_BUTTON": S.PLACE_BUTTON, "FILE_INPUT": S.FILE_INPUT, "SAVE_BUTTON": S.SAVE_BUTTON,
        "PUBLISH_BLOCKLIST": S.PUBLISH_BLOCKLIST, "POPUP_DRAFT_CANCEL": S.POPUP_DRAFT_CANCEL,
        "POPUP_HELP_CLOSE": S.POPUP_HELP_CLOSE, "SAVE_COUNT": S.SAVE_COUNT, "SAVE_TOAST": S.SAVE_TOAST,
        "ALIGN_DROPDOWN": S.ALIGN_DROPDOWN, "QUOTE_STYLE_DROPDOWN": S.QUOTE_STYLE_DROPDOWN,
    }
    for name, sels in groups.items():
        report["selectors"][name] = {sel: await frame.locator(sel).count() for sel in sels}
    for name in S.IMAGE_BUTTON_NAMES + S.QUOTE_BUTTON_NAMES + S.DIVIDER_BUTTON_NAMES + S.PLACE_BUTTON_NAMES + S.SAVE_BUTTON_NAMES:
        report["buttons_by_name"][name] = await frame.get_by_role("button", name=name).count()
    comps = frame.locator(S.COMPONENT)
    for i in range(min(await comps.count(), 30)):
        c = comps.nth(i)
        try:
            txt = (await c.inner_text(timeout=1000)).strip().replace("\n", " ")[:60]
        except Exception:
            txt = ""
        report["components"].append({"index": i, "class": await c.get_attribute("class"), "text": txt})
    # 창이 떠 있을 때나 저장 직후에만 보이는 요소, 대체 수단은 0이어도 정상입니다.
    report["zero_is_normal"] = ["FILE_INPUT(대체 수단)", "POPUP_DRAFT_CANCEL", "POPUP_HELP_CLOSE", "SAVE_TOAST(저장 직후에만)"]
    report["toolbar_buttons"] = await frame.evaluate(
        """() => [...document.querySelectorAll('button')].filter(b => b.offsetParent).slice(0, 60)
             .map(b => ({cls: b.className.slice(0, 80), text: (b.innerText || b.getAttribute('aria-label') || '').trim().slice(0, 20),
                         testid: b.getAttribute('data-testid'), name: b.getAttribute('data-name')}))"""
    )
    return report
