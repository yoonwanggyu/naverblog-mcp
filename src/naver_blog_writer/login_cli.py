"""터미널에서 실행하는 로그인 도우미.

    uv run naver-blog-login          # 브라우저 창이 뜨면 직접 로그인
    uv run naver-blog-login --check  # 로그인 상태만 확인

이 스크립트는 MCP 서버가 아니므로 print를 써도 됩니다.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from .core.browser import browser_session, check_login, interactive_login


async def _check() -> dict:
    async with browser_session(headless=True) as (_ctx, page):
        return await check_login(page)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="네이버 블로그 MCP 로그인 도우미")
    ap.add_argument("--check", action="store_true", help="로그인 상태만 확인")
    ap.add_argument("--timeout", type=int, default=600, help="로그인 대기 시간(초)")
    args = ap.parse_args()
    if args.check:
        print(asyncio.run(_check()))
        return
    print("브라우저 창에서 네이버에 로그인하세요. '로그인 상태 유지'를 체크하면 오래 유지됩니다.")
    print(asyncio.run(interactive_login(timeout_sec=args.timeout)))


if __name__ == "__main__":
    main()
