"""최근 글 기록(최근 5개 보관)과 겹침 경고를 검증합니다. 브라우저와 네이버 접속이 필요 없습니다.

    uv run python tests/archive_test.py
"""

import os
import sys
import tempfile

os.environ["NBW_NO_DOTENV"] = "1"  # 테스트는 레포의 .env를 읽지 않습니다
os.environ["NBW_HOME"] = tempfile.mkdtemp(prefix="nbw_archive_")

from naver_blog_writer.core import archive

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


BODY = ("주말 데이트 장소로 성수를 골랐어요. 카페부터 저녁까지 6시간 코스로 다녀왔거든요.\n"
        "성수 카페어니언은 토요일 12시 기준 20분 정도 기다렸어요. 오픈 직후에 가면 바로 들어갈 수 있더라고요.\n"
        "빵 종류가 많아서 고르는 데만 한참 걸렸어요. 팡도르는 두 명이 나눠 먹기 좋은 크기였어요.\n"
        "조용히 대화하고 싶은 커플에게 성수 데이트 코스로 추천해요.")
for i in range(7):
    archive.save({"title": f"테스트 글 {i}번 제목입니다", "main_keyword": f"키워드{i}", "hook_pattern": "인사형",
                  "subtitles": [f"소제목 {i}"], "tags": ["a"], "body": f"{i}번째 글의 본문입니다. 서로 다른 내용이에요."})
archive.save({"title": "성수 데이트 코스 카페어니언 하루 동선", "main_keyword": "성수 데이트 코스",
              "hook_pattern": "상황형", "subtitles": ["성수 카페어니언 웨이팅은 얼마나 되나요?"],
              "tags": ["성수"], "body": BODY})
rec = archive.recent()
check("최근 5개만 보관", len(rec) == 5, f"{len(rec)}개")
check("최신순 정렬", rec[0]["title"].startswith("성수 데이트"), rec[0]["title"])

w = archive.overlap_warnings("성수 데이트 코스 카페어니언 하루 동선 정리", "성수 데이트 코스", "상황형",
                             ["성수 카페어니언 웨이팅은 얼마나 되나요?"], BODY)
joined = " / ".join(w)
check("같은 도입 유형 경고", "도입 유형" in joined)
check("같은 메인 키워드 경고", "메인 키워드" in joined)
check("비슷한 제목 경고", "제목이" in joined)
check("같은 소제목 경고", "소제목이" in joined)
check("비슷한 도입 경고", "도입 첫 문장" in joined)
check("비슷한 마무리 경고", "마무리 문장" in joined)
check("반복 문장 경고", "거의 같은 문장" in joined)

w2 = archive.overlap_warnings("구로 웨딩홀 투어 다섯 곳 비교하고 계약한 후기", "구로 웨딩홀 투어", "반전형",
                              ["식대와 보증 인원은 어떻게 다른가요?"],
                              "결혼 날짜를 정하고 나서 웨딩홀부터 알아봤어요. 다섯 곳을 직접 둘러보고 비교했거든요.\n"
                              "저희는 식대보다 홀 분위기를 더 중요하게 봤어요. 결국 천장이 높은 홀로 계약했어요.")
check("다른 글은 경고 없음", w2 == [], str(w2))
sys.exit(0 if all(results) else 1)
