"""폴더 찾기 검증: 여러 날짜 형식, 장소명 오타, EXIF 날짜, 하위 폴더, '이게 맞습니까?' 확인,
두 사람이 올린 사진의 촬영 시각 정렬. 브라우저와 네트워크가 필요 없습니다.

    uv run python tests/folders_test.py
"""

import asyncio
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

from PIL import Image

tmp = Path(tempfile.mkdtemp(prefix="nbw_folders_"))
root = tmp / "네이버블로그"


def photo(folder: Path, name: str, taken: str | None = None, original: str | None = None, offset: str | None = None,
          model: str | None = None) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGB", (64, 48), (120, 90, 60))
    exif = Image.Exif()
    if taken:
        exif[306] = taken  # DateTime
    if model:
        exif[272] = model
    if original:  # Exif IFD: DateTimeOriginal, OffsetTimeOriginal
        ifd = exif.get_ifd(0x8769)
        ifd[36867] = original
        if offset:
            ifd[36881] = offset
    im.save(folder / name, exif=exif.tobytes())


photo(root / "젠스시(26.11.14)", "IMG_0728.JPG", "2026:11:14 13:42:11")
photo(root / "2026-11-15_성수데이트" / "1_카페어니언", "a.jpg")
photo(root / "2026-11-15_성수데이트" / "2_OO국밥", "b.jpg")
photo(root / "놀숲 11.17", "c.jpg")
photo(root / "20261020 면접후기", "d.jpg")
photo(root / "웨딩홀투어", "e.jpg", "2026:11:01 11:00:00")       # 이름에 날짜 없음 -> 사진 촬영일 사용
photo(root / "2026년 10월 3일 한강피크닉", "f.jpg")
photo(root / "Nov 5 이태원", "g.jpg")
(root / "빈폴더").mkdir()
# 두 사람이 같은 폴더에 올린 사진: 파일 이름순과 촬영 시각순이 다름
both = root / "둘이같이(26.09.20)"
photo(both, "IMG_0001.JPG", original="2026:09:20 12:00:00", offset="+09:00", model="iPhone 16")
photo(both, "IMG_0002.JPG", original="2026:09:20 12:30:00", offset="+09:00", model="iPhone 16")
photo(both, "20260920_121500.jpg", original="2026:09:20 12:15:00", model="SM-S928N")
photo(both, "PXL_A.jpg", original="2026:09:20 03:20:00", offset="+00:00", model="Pixel 9")    # UTC 03:20 = 한국 12:20
photo(both, "KakaoTalk_20260920_124500123.jpg")                                                # EXIF 없음 -> 파일 이름 날짜

os.environ["NBW_NO_DOTENV"] = "1"  # 테스트는 레포의 .env를 읽지 않습니다
os.environ.update(MEDIA_ROOTS=str(root), NBW_HOME=str(tmp / "home"))

from naver_blog_writer.core import folders  # noqa: E402
from naver_blog_writer.core.folders import search_folders  # noqa: E402
from naver_blog_writer.tools.scan_media import scan_media  # noqa: E402

TODAY = date(2026, 11, 18)
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def find(q):
    return search_folders([root], q, today=TODAY)


# 1) 젠스시를 여러 날짜 형식으로
formats = ["26.11.14", "2026-11-14", "2026.11.14", "2026/11/14", "2026. 11. 14.", "20261114", "261114", "11.14", "11/14",
           "11-14", "1114", "11월14일", "11월 14일", "2026년 11월 14일", "26년 11월 14일", "Nov 14", "14 Nov 2026"]
bad = [f for f in formats if not (find(f)["match"] == "exact" and find(f)["best"]["label"] == "젠스시")]
check(f"날짜 형식 {len(formats)}가지로 같은 폴더 찾기", not bad, f"실패: {bad}" if bad else "")

r = find("11월 14일 젠스시")
check("날짜 + 이름 정확 일치는 바로 진행", r["match"] == "exact" and not r["needs_confirmation"], r["match"])

# 2) 오타와 다른 날짜 -> 확인 질문
r = find("11월24일에 다녀온 제스시")
q = r["confirm_question"] or ""
check("오타+날짜 차이는 확인 질문", r["needs_confirmation"] and r["best"]["label"] == "젠스시", r["match"])
check("질문에 폴더 날짜와 장소명, '이게 맞습니까?'", all(s in q for s in ("2026년 11월 14일", "젠스시", "제스시", "11월 24일", "이게 맞습니까?")), q[:60])
r = find("제스시")
check("이름 오타만 있어도 확인 질문", r["needs_confirmation"] and r["best"]["label"] == "젠스시")
r = find("놀숩")
check("받침 오타(놀숲/놀숩) 후보 찾기", r["needs_confirmation"] and r["best"]["label"] == "놀숲")
r = find("11월 24일")
check("날짜만 비슷해도 확인 질문", r["needs_confirmation"] and r["best"]["label"] == "젠스시")

# 3) 그 밖의 폴더 규칙
r = find("성수 국밥")
check("하위 폴더(상위 이름 + 하위 이름)", r["match"] == "exact" and r["best"]["label"] == "OO국밥", str(r["best"] and r["best"]["relative"]))
r = find("11월 15일 성수")
check("같은 조건 여러 폴더는 확인 질문", r["needs_confirmation"] and len(r["candidates"]) >= 2, r["match"])
r = find("웨딩홀 11월 1일")
check("이름에 날짜 없으면 사진 촬영일로 비교", r["match"] == "exact" and r["best"]["date_source"] == "photo_exif", str(r["best"] and r["best"]["date_source"]))
check("면접후기 10/20", find("면접후기 10/20")["match"] == "exact")
check("한국어 연월일 폴더(한강 피크닉 10월 3일)", find("한강 피크닉 10월 3일")["match"] == "exact")
r = find("이태원 11.5")
check("영어 월 이름 폴더(Nov 5)", r["match"] == "exact" and r["best"]["date"] == "2026-11-05", str(r["best"] and r["best"]["date"]))
r = find("놀숲 사진으로 블로그 써줘")
check("문장째 넣어도 장소명만 비교", r["match"] == "exact" and r["parsed"]["terms"] == ["놀숲"], str(r["parsed"]))
r = find("아무거나 없는곳")
check("없으면 최근 폴더를 보여 주며 질문", r["match"] == "none" and "맞는 폴더" in r["confirm_question"])
check("빈 폴더는 목록에서 제외", all(f["label"] != "빈폴더" for f in find("")["folders"]))


# 4) 확인 전에는 scan_media가 읽지 않음
async def gate():
    r = find("제스시")
    path = r["best"]["path"]
    try:
        await scan_media(path)
        check("확인 전 scan_media 차단", False, "통과해 버림")
    except PermissionError as e:
        check("확인 전 scan_media 차단", "이게 맞습니까?" in str(e))
    res = await scan_media(path, user_confirmed=True)
    check("확인 후 scan_media 진행", res["images"] == 1)
    check("한 번 확인한 폴더는 다시 묻지 않음", folders.pending_question(path) is None)


asyncio.run(gate())


# 5) 두 사람이 올린 사진은 촬영 시각으로 섞어 정렬
async def order():
    os.environ["TZ"] = "Asia/Seoul"
    import time as _t
    _t.tzset()
    res = await scan_media(str(both), user_confirmed=True)
    names = [i["path"].rsplit("/", 1)[-1] for i in res["items"]]
    check("두 기기 사진을 촬영 시각순으로 섞음(시간대 보정 포함)",
          names == ["IMG_0001.JPG", "20260920_121500.jpg", "PXL_A.jpg", "IMG_0002.JPG", "KakaoTalk_20260920_124500123.jpg"], str(names))
    check("순서 번호와 시각 출처", [i["order"] for i in res["items"]] == [1, 2, 3, 4, 5]
          and res["items"][-1]["time_source"] == "file_name" and res["items"][0]["time_source"] == "exif",
          str([(i["order"], i["time_source"]) for i in res["items"]]))
    check("기기별 사진 수", res["devices"].get("iPhone 16") == 2 and res["devices"].get("SM-S928N") == 1, str(res["devices"]))
    from naver_blog_writer.tools.write_post import _photo_order_warnings
    paths = [Path(i["path"]) for i in res["items"]]
    check("촬영 순서대로면 경고 없음", _photo_order_warnings(paths) == [])
    check("순서가 바뀌면 경고(첫 사진은 대표 사진이라 제외)",
          len(_photo_order_warnings([paths[0], paths[3], paths[1]])) == 1 and _photo_order_warnings([paths[3], paths[0], paths[1]]) == [])


asyncio.run(order())
sys.exit(0 if all(results) else 1)
