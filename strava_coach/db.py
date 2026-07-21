import json
import sqlite3
from typing import Optional

from .config import DATA_DIR

DB_PATH = DATA_DIR / "strava_coach.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS activities (
    id INTEGER PRIMARY KEY,
    name TEXT,
    start_date TEXT,
    distance_m REAL,
    moving_time_s INTEGER,
    average_heartrate REAL,
    max_heartrate REAL,
    average_speed REAL,
    raw_json TEXT
);

CREATE TABLE IF NOT EXISTS laps (
    activity_id INTEGER,
    lap_index INTEGER,
    distance_m REAL,
    moving_time_s INTEGER,
    average_speed REAL,
    average_heartrate REAL,
    raw_json TEXT,
    PRIMARY KEY (activity_id, lap_index)
);

CREATE TABLE IF NOT EXISTS streams (
    activity_id INTEGER PRIMARY KEY,
    raw_json TEXT
);

CREATE TABLE IF NOT EXISTS activity_zones (
    activity_id INTEGER PRIMARY KEY,
    raw_json TEXT
);

CREATE TABLE IF NOT EXISTS sync_state (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """상세활동에서 얻는 필드용 컬럼을 없으면 추가."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(activities)")}
    for col, decl in (("suffer_score", "REAL"), ("best_efforts", "TEXT"),
                      ("gap_pace_sec", "REAL"), ("splits_metric", "TEXT")):
        if col not in cols:
            conn.execute(f"ALTER TABLE activities ADD COLUMN {col} {decl}")


def get_connection() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def update_activity_detail(
    conn: sqlite3.Connection, activity_id: int, suffer_score, best_efforts, gap_pace_sec,
    splits_metric=None,
) -> None:
    conn.execute(
        "UPDATE activities SET suffer_score=?, best_efforts=?, gap_pace_sec=?, splits_metric=? WHERE id=?",
        (
            suffer_score,
            json.dumps(best_efforts) if best_efforts else None,
            gap_pace_sec,
            json.dumps(splits_metric) if splits_metric else None,
            activity_id,
        ),
    )


def upsert_activity(conn: sqlite3.Connection, activity: dict) -> None:
    conn.execute(
        """INSERT INTO activities (id, name, start_date, distance_m, moving_time_s,
               average_heartrate, max_heartrate, average_speed, raw_json)
           VALUES (:id, :name, :start_date, :distance_m, :moving_time_s,
               :average_heartrate, :max_heartrate, :average_speed, :raw_json)
           ON CONFLICT(id) DO UPDATE SET
               name=excluded.name, start_date=excluded.start_date,
               distance_m=excluded.distance_m, moving_time_s=excluded.moving_time_s,
               average_heartrate=excluded.average_heartrate, max_heartrate=excluded.max_heartrate,
               average_speed=excluded.average_speed, raw_json=excluded.raw_json""",
        {
            "id": activity["id"],
            "name": activity.get("name"),
            "start_date": activity.get("start_date"),
            "distance_m": activity.get("distance"),
            "moving_time_s": activity.get("moving_time"),
            "average_heartrate": activity.get("average_heartrate"),
            "max_heartrate": activity.get("max_heartrate"),
            "average_speed": activity.get("average_speed"),
            "raw_json": json.dumps(activity),
        },
    )


def replace_laps(conn: sqlite3.Connection, activity_id: int, laps: list[dict]) -> None:
    conn.execute("DELETE FROM laps WHERE activity_id = ?", (activity_id,))
    for idx, lap in enumerate(laps):
        conn.execute(
            """INSERT INTO laps (activity_id, lap_index, distance_m, moving_time_s,
                   average_speed, average_heartrate, raw_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                activity_id,
                idx,
                lap.get("distance"),
                lap.get("moving_time"),
                lap.get("average_speed"),
                lap.get("average_heartrate"),
                json.dumps(lap),
            ),
        )


def upsert_streams(conn: sqlite3.Connection, activity_id: int, streams: dict) -> None:
    conn.execute(
        "INSERT INTO streams (activity_id, raw_json) VALUES (?, ?) "
        "ON CONFLICT(activity_id) DO UPDATE SET raw_json=excluded.raw_json",
        (activity_id, json.dumps(streams)),
    )


def get_state(conn: sqlite3.Connection, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def get_settings(conn: sqlite3.Connection) -> dict:
    rows = conn.execute("SELECT key, value FROM sync_state WHERE key LIKE 'goal_%'").fetchall()
    return {r["key"]: r["value"] for r in rows}


def set_settings(conn: sqlite3.Connection, values: dict) -> None:
    for k, v in values.items():
        conn.execute(
            "INSERT INTO sync_state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (k, str(v)),
        )


# ── 사용자 상태(목표·AI계획)는 push되는 DB 밖 파일에 저장 ──────────────
# DB(strava_coach.db)는 로컬→HF로 push되므로, 거기에 목표/AI계획을 두면
# 내 로컬 push가 HF에서 생성한 값을 덮어써(롤백) 버린다. 그래서 이 값들은
# gitignore·dockerignore된 user_state.json에 따로 저장하고, DB는 폴백(마이그레이션)으로만 읽는다.
USER_STATE_PATH = DATA_DIR / "user_state.json"


def _load_user_state() -> dict:
    try:
        return json.loads(USER_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 파일 없거나 깨졌으면 빈 상태
        return {}


def set_user_values(values: dict) -> None:
    """목표/AI계획 등 사용자 상태를 user_state.json에 병합 저장."""
    state = _load_user_state()
    state.update({k: str(v) for k, v in values.items()})
    USER_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    USER_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def get_user_value(conn: sqlite3.Connection, key: str) -> Optional[str]:
    """user_state.json 우선, 없으면 DB sync_state 폴백(기존 값 마이그레이션)."""
    state = _load_user_state()
    if key in state:
        return state[key]
    return get_state(conn, key)


def get_user_settings(conn: sqlite3.Connection) -> dict:
    """goal_* 설정: user_state.json 우선, 없으면 DB 폴백."""
    state = _load_user_state()
    goals = {k: v for k, v in state.items() if k.startswith("goal_")}
    if goals:
        return goals
    return get_settings(conn)


def get_sync_anchor(conn: sqlite3.Connection) -> Optional[int]:
    row = conn.execute("SELECT value FROM sync_state WHERE key = 'last_sync_after'").fetchone()
    return int(row["value"]) if row else None


def set_sync_anchor(conn: sqlite3.Connection, epoch: int) -> None:
    conn.execute(
        "INSERT INTO sync_state (key, value) VALUES ('last_sync_after', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(epoch),),
    )


def all_activities(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM activities ORDER BY start_date").fetchall()


def laps_for(conn: sqlite3.Connection, activity_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM laps WHERE activity_id = ? ORDER BY lap_index", (activity_id,)
    ).fetchall()


def get_activity(conn: sqlite3.Connection, activity_id: int) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM activities WHERE id = ?", (activity_id,)).fetchone()


def upsert_zones(conn: sqlite3.Connection, activity_id: int, zones: list) -> None:
    conn.execute(
        "INSERT INTO activity_zones (activity_id, raw_json) VALUES (?, ?) "
        "ON CONFLICT(activity_id) DO UPDATE SET raw_json=excluded.raw_json",
        (activity_id, json.dumps(zones)),
    )


def zones_for(conn: sqlite3.Connection, activity_id: int) -> list:
    row = conn.execute(
        "SELECT raw_json FROM activity_zones WHERE activity_id = ?", (activity_id,)
    ).fetchone()
    return json.loads(row["raw_json"]) if row else []


def all_zones(conn: sqlite3.Connection) -> list:
    return [json.loads(r["raw_json"]) for r in conn.execute("SELECT raw_json FROM activity_zones")]


def streams_for(conn: sqlite3.Connection, activity_id: int) -> dict:
    row = conn.execute(
        "SELECT raw_json FROM streams WHERE activity_id = ?", (activity_id,)
    ).fetchone()
    if not row:
        return {}
    return json.loads(row["raw_json"])
