"""Apple Health 내보내기(export.xml)에서 심박(HR)을 뽑아, Strava 임포트 때
유실된 러닝 활동의 HR을 백필한다.

Strava가 Apple Health 활동을 임포트할 때 HR 스트림을 못 가져오는 경우가 있어
활동의 average/max HR이 비고 상세 HR 차트도 그려지지 않는다. Apple Health 원본
XML에는 HKQuantityTypeIdentifierHeartRate 레코드가 남아 있으므로, 각 러닝의
시간대에 해당하는 HR 레코드를 모아 요약치와 시계열을 재구성한다.
"""

import bisect
import json
import xml.etree.ElementTree as ET
from datetime import datetime

from . import db


def _parse_dt(s: str) -> float:
    # 예: "2026-06-26 20:43:40 +0900"
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S %z").timestamp()


def _activity_window(activity) -> tuple[float, float]:
    raw = json.loads(activity["raw_json"]) if activity["raw_json"] else {}
    start = datetime.fromisoformat(activity["start_date"].replace("Z", "+00:00")).timestamp()
    dur = raw.get("elapsed_time") or activity["moving_time_s"] or 0
    return start, start + dur


def _load_hr_records(xml_path: str) -> list[tuple[float, float]]:
    """(epoch, bpm) 리스트를 시간순으로 반환."""
    records = []
    for _, el in ET.iterparse(xml_path, events=("end",)):
        if el.tag == "Record" and el.get("type") == "HKQuantityTypeIdentifierHeartRate":
            try:
                records.append((_parse_dt(el.get("startDate")), float(el.get("value"))))
            except (TypeError, ValueError):
                pass
        el.clear()
    records.sort()
    return records


def _hr_in_window(records, epochs, start, end) -> list[tuple[float, float]]:
    lo = bisect.bisect_left(epochs, start)
    hi = bisect.bisect_right(epochs, end)
    return records[lo:hi]


def backfill_hr(xml_path: str, only_missing: bool = True) -> dict:
    """HR이 비어있는 러닝 활동에 Apple Health HR을 백필.

    - activities.average_heartrate / max_heartrate 갱신
    - streams에 heartrate 배열을 기존 time 배열에 맞춰 재구성(있으면)
    반환: {"updated": n, "skipped": n, "details": [...]}
    """
    conn = db.get_connection()
    activities = db.all_activities(conn)
    targets = [a for a in activities if not only_missing or a["average_heartrate"] is None]
    if not targets:
        return {"updated": 0, "skipped": len(activities), "details": []}

    records = _load_hr_records(xml_path)
    epochs = [r[0] for r in records]

    updated = 0
    details = []
    for a in targets:
        start, end = _activity_window(a)
        window = _hr_in_window(records, epochs, start, end)
        if not window:
            continue
        vals = [v for _, v in window]
        avg = round(sum(vals) / len(vals), 1)
        hr_max = round(max(vals), 1)

        conn.execute(
            "UPDATE activities SET average_heartrate=?, max_heartrate=? WHERE id=?",
            (avg, hr_max, a["id"]),
        )

        # 상세 HR 차트/존 계산용: 기존 time 스트림에 맞춰 heartrate 배열 재구성
        streams = db.streams_for(conn, a["id"])
        time_stream = (streams.get("time") or {}).get("data") if streams else None
        if time_stream:
            win_epochs = [e for e, _ in window]
            hr_series = []
            for t in time_stream:
                target_epoch = start + t
                i = bisect.bisect_left(win_epochs, target_epoch)
                cand = []
                if i < len(window):
                    cand.append(window[i])
                if i > 0:
                    cand.append(window[i - 1])
                if cand:
                    nearest = min(cand, key=lambda r: abs(r[0] - target_epoch))
                    # 30초 이상 벌어지면 결측으로 간주하지 않고 가장 가까운 값 사용
                    hr_series.append(round(nearest[1]))
                else:
                    hr_series.append(None)
            streams["heartrate"] = {"type": "heartrate", "data": hr_series}
            db.upsert_streams(conn, a["id"], streams)

        updated += 1
        details.append({"date": a["start_date"][:10], "avg_hr": avg, "max_hr": hr_max, "n": len(vals)})

    conn.commit()
    return {"updated": updated, "skipped": len(activities) - updated, "details": details}
