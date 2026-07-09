import json
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
        detail = client.get_activity_detail(activity["id"])
        db.update_activity_detail(
            conn, activity["id"], detail.get("suffer_score"),
            detail.get("best_efforts"), _gap_pace_sec(detail), detail.get("splits_metric"),
        )
        try:
            db.upsert_zones(conn, activity["id"], client.get_activity_zones(activity["id"]))
        except Exception:
            pass
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


def _gap_pace_sec(detail: dict) -> float | None:
    """splits_metric의 경사보정 속도로 활동 전체 GAP 페이스(sec/km)를 거리가중 계산."""
    splits = detail.get("splits_metric") or []
    tot_d = tot_t = 0.0
    for s in splits:
        d = s.get("distance") or 0
        gs = s.get("average_grade_adjusted_speed")
        if d and gs and gs > 0:
            tot_d += d
            tot_t += d / gs  # 시간 = 거리 / 속도
    if not tot_d:
        return None
    return round(tot_t / (tot_d / 1000))  # sec/km


def fetch_details(only_missing: bool = True) -> int:
    """상세활동(suffer_score, best_efforts, splits, GAP) + Strava HR/페이스 존을 활동별로 보강.
    계정 HR존(/athlete/zones)도 함께 가져와 저장."""
    tokens = refresh_if_needed(STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET)
    client = StravaClient(tokens["access_token"])
    conn = db.get_connection()

    # 계정 HR존 저장 (없으면 활동별 zones에서 유도)
    try:
        az = client.get_athlete_zones()
        db.set_settings(conn, {"athlete_zones": json.dumps(az)})
    except Exception:
        pass

    count = 0
    for a in db.all_activities(conn):
        if only_missing and a["splits_metric"] and db.zones_for(conn, a["id"]):
            continue
        detail = client.get_activity_detail(a["id"])
        db.update_activity_detail(
            conn, a["id"], detail.get("suffer_score"),
            detail.get("best_efforts"), _gap_pace_sec(detail), detail.get("splits_metric"),
        )
        # Strava가 계산한 존 분포(HR/페이스) 저장 — 하드코딩 없이 그대로 사용
        try:
            db.upsert_zones(conn, a["id"], client.get_activity_zones(a["id"]))
        except Exception:
            pass
        conn.commit()
        count += 1
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
