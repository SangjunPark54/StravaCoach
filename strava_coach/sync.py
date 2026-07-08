from datetime import datetime

from . import db
from .auth import refresh_if_needed
from .config import STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET
from .strava_client import StravaClient


def sync_all() -> int:
    tokens = refresh_if_needed(STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET)
    client = StravaClient(tokens["access_token"])
    conn = db.get_connection()

    after = db.get_sync_anchor(conn)
    count = 0
    latest_epoch = after or 0

    for activity in client.list_activities(after=after):
        db.upsert_activity(conn, activity)
        db.replace_laps(conn, activity["id"], client.get_laps(activity["id"]))
        db.upsert_streams(conn, activity["id"], client.get_streams(activity["id"]))
        conn.commit()
        count += 1

        start_epoch = int(
            datetime.fromisoformat(activity["start_date"].replace("Z", "+00:00")).timestamp()
        )
        latest_epoch = max(latest_epoch, start_epoch)

    if count:
        db.set_sync_anchor(conn, latest_epoch)
        conn.commit()

    return count


def refetch_streams(only_missing_latlng: bool = True) -> int:
    """기존 활동의 스트림을 다시 받아온다(latlng 포함). 백필된 heartrate는 보존."""
    tokens = refresh_if_needed(STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET)
    client = StravaClient(tokens["access_token"])
    conn = db.get_connection()

    count = 0
    for a in db.all_activities(conn):
        old = db.streams_for(conn, a["id"])
        if only_missing_latlng and "latlng" in old:
            continue
        new = client.get_streams(a["id"])
        # Strava에 HR이 없으면(임포트 유실) 백필해둔 heartrate 보존
        if "heartrate" not in new and "heartrate" in old:
            new["heartrate"] = old["heartrate"]
        db.upsert_streams(conn, a["id"], new)
        conn.commit()
        count += 1

    return count
