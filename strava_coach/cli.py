import argparse
import sys

from . import auth as auth_module
from . import sync as sync_module
from .config import STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET


def main() -> None:
    parser = argparse.ArgumentParser(prog="strava_coach")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("auth", help="Strava OAuth 인증")
    sub.add_parser("sync", help="새 활동 동기화")
    sub.add_parser("serve", help="로컬 웹 대시보드 실행")
    sub.add_parser("refetch-streams", help="기존 활동 스트림 재수집(latlng 포함, 백필HR 보존)")
    bf = sub.add_parser("backfill-hr", help="Apple Health XML에서 유실된 HR 백필")
    bf.add_argument("xml_path", help="Apple Health 내보내기 xml 경로")
    bf.add_argument("--all", action="store_true", help="HR 있는 활동도 덮어쓰기")
    args = parser.parse_args()

    if args.command == "auth":
        if not STRAVA_CLIENT_ID or not STRAVA_CLIENT_SECRET:
            print(".env에 STRAVA_CLIENT_ID / STRAVA_CLIENT_SECRET을 먼저 설정하세요.", file=sys.stderr)
            sys.exit(1)
        auth_module.authorize(STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET)
        print("인증 완료.")
    elif args.command == "sync":
        n = sync_module.sync_all()
        print(f"{n}건 동기화 완료.")
    elif args.command == "refetch-streams":
        n = sync_module.refetch_streams()
        print(f"{n}건 스트림 재수집 완료.")
    elif args.command == "backfill-hr":
        from . import apple_health

        result = apple_health.backfill_hr(args.xml_path, only_missing=not args.all)
        print(f"HR 백필: {result['updated']}건 갱신, {result['skipped']}건 건너뜀.")
        for d in result["details"]:
            print(f"  {d['date']}: avg {d['avg_hr']} / max {d['max_hr']} (샘플 {d['n']})")
    elif args.command == "serve":
        import os

        import uvicorn

        host = os.environ.get("HOST", "127.0.0.1")
        port = int(os.environ.get("PORT", "8000"))
        uvicorn.run("strava_coach.web.app:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
