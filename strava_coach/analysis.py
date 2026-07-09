import json
import sqlite3
from datetime import date, timedelta
from typing import Optional

from . import db
from .config import GOAL_DATE, GOAL_DISTANCE_KM, GOAL_PACE_SEC_PER_KM, HR_ZONES


def pace_sec_per_km(distance_m: Optional[float], time_s: Optional[float]) -> Optional[float]:
    if not distance_m or not time_s or distance_m <= 0:
        return None
    return time_s / (distance_m / 1000)


def format_pace(sec_per_km: Optional[float]) -> str:
    if sec_per_km is None:
        return "-"
    m, s = divmod(int(round(sec_per_km)), 60)
    return f"{m}'{s:02d}\""


def hr_zone(hr: Optional[float], zones: Optional[dict] = None) -> Optional[str]:
    zones = zones or HR_ZONES
    if hr is None:
        return None
    for zone, (low, high) in zones.items():
        if low <= hr < high:
            return zone
    return None


def _zones_from_buckets(buckets: list) -> dict:
    """Strava distribution_buckets([{min,max,time}])에서 존 경계를 만든다(하드코딩 없음)."""
    out = {}
    for i, b in enumerate(buckets):
        lo = b.get("min", 0)
        hi = b.get("max")
        hi = 999 if (hi is None or hi < 0) else hi + 1  # 버킷 max는 포함값 → +1
        out[f"Z{i + 1}"] = (lo, hi)
    return out


def resolve_hr_zones(conn: sqlite3.Connection) -> dict:
    """실제 HR존을 Strava에서: 1) 계정 존(/athlete/zones) 2) 활동 zones 버킷 유도 3) 폴백."""
    raw = db.get_state(conn, "athlete_zones")
    if raw:
        az = json.loads(raw)
        hz = (az.get("heart_rate") or {}).get("zones") if isinstance(az, dict) else None
        if hz and len(hz) >= 2:
            return _zones_from_buckets(hz)
    for zlist in db.all_zones(conn):
        for zt in zlist:
            if zt.get("type") == "heartrate":
                buckets = zt.get("distribution_buckets") or []
                if len(buckets) >= 2:
                    return _zones_from_buckets(buckets)
    return dict(HR_ZONES)


_PACE_ZONE_NAMES = ["Recovery", "Endurance", "Tempo", "Threshold", "VO2 Max", "Anaerobic"]


def pace_distribution(activity_zones: list) -> list[dict]:
    """Strava /zones의 pace 버킷 → 페이스 존 분포(범위·시간). 값 전부 API 기반."""
    for zt in activity_zones or []:
        if zt.get("type") != "pace":
            continue
        buckets = zt.get("distribution_buckets") or []
        total = sum((b.get("time") or 0) for b in buckets) or 1
        rows = []
        for i, b in enumerate(buckets):
            smin, smax = b.get("min", 0), b.get("max")
            pace_fast = 1000 / smax if smax and smax > 0 else None  # 빠른쪽 경계
            pace_slow = 1000 / smin if smin and smin > 0 else None  # 느린쪽 경계
            if pace_fast is None:
                rng = f"< {format_pace(pace_slow)}"       # 최고속 존(max=-1): ~보다 빠름
            elif pace_slow is None:
                rng = f"> {format_pace(pace_fast)}"       # 최저속 존(min=0): ~보다 느림
            else:
                rng = f"{format_pace(pace_fast)}~{format_pace(pace_slow)}"
            t = b.get("time") or 0
            rows.append({
                "zone": f"Z{i + 1}",
                "name": _PACE_ZONE_NAMES[i] if i < len(_PACE_ZONE_NAMES) else "",
                "range": rng,
                "time_str": format_duration(t),
                "pct": round(100 * t / total, 1),
            })
        rows.reverse()  # 빠른 존이 위로 (Strava와 동일)
        return rows
    return []


def zone_time_from_strava(activity_zones: list) -> dict:
    """활동의 Strava zones에서 HR존별 체류시간(초)을 그대로 가져온다."""
    for zt in activity_zones or []:
        if zt.get("type") == "heartrate":
            return {f"Z{i + 1}": (b.get("time") or 0) for i, b in enumerate(zt.get("distribution_buckets") or [])}
    return {}


def classify_session(activity: sqlite3.Row, laps: list[sqlite3.Row], zones: Optional[dict] = None) -> str:
    """laps의 페이스 변동성으로 구조화 세션(VO2/역치)을 먼저 식별하고,
    그 외엔 평균 HR/페이스/거리로 템포·롱런·이지런을 구분한다. 템포 HR 기준은 Strava Z4."""
    zones = zones or HR_ZONES
    z4_low = zones.get("Z4", (154, 174))[0]
    distance_km = (activity["distance_m"] or 0) / 1000
    avg_hr = activity["average_heartrate"] or 0
    avg_pace = pace_sec_per_km(activity["distance_m"], activity["moving_time_s"])

    lap_paces = [
        pace_sec_per_km(l["distance_m"], l["moving_time_s"])
        for l in laps
        if l["distance_m"] and l["moving_time_s"]
    ]
    lap_paces = [p for p in lap_paces if p is not None]

    if len(lap_paces) >= 4:
        fastest, slowest = min(lap_paces), max(lap_paces)
        variance_ratio = (slowest / fastest) if fastest else 1

        short_fast = sum(
            1
            for l in laps
            if l["distance_m"]
            and l["distance_m"] <= 600
            and (pace_sec_per_km(l["distance_m"], l["moving_time_s"]) or 999) <= 300
        )
        mid_fast = sum(
            1
            for l in laps
            if l["distance_m"]
            and 600 < l["distance_m"] <= 1500
            and (pace_sec_per_km(l["distance_m"], l["moving_time_s"]) or 999) <= 320
        )

        if variance_ratio >= 1.3 and short_fast >= 3:
            return "vo2"
        if variance_ratio >= 1.15 and mid_fast >= 3:
            return "threshold"

    if avg_hr >= z4_low and avg_pace and avg_pace <= 345 and distance_km >= 3:
        return "tempo"
    if distance_km >= 9:
        return "long_run"
    return "easy"


def summarize_activities(conn: sqlite3.Connection, activities: list[sqlite3.Row]) -> list[dict]:
    zones = resolve_hr_zones(conn)
    results = []
    for a in activities:
        laps = db.laps_for(conn, a["id"])
        avg_pace = pace_sec_per_km(a["distance_m"], a["moving_time_s"])
        results.append(
            {
                "id": a["id"],
                "name": a["name"],
                "date": a["start_date"][:10] if a["start_date"] else None,
                "distance_km": round((a["distance_m"] or 0) / 1000, 2),
                "avg_pace": avg_pace,
                "avg_pace_str": format_pace(avg_pace),
                "avg_hr": a["average_heartrate"],
                "max_hr": a["max_heartrate"],
                "suffer_score": a["suffer_score"] if "suffer_score" in a.keys() else None,
                "gap_pace_sec": a["gap_pace_sec"] if "gap_pace_sec" in a.keys() else None,
                "hr_zone": hr_zone(a["average_heartrate"], zones),
                "type": classify_session(a, laps, zones),
                "lap_count": len(laps),
            }
        )
    return results


def format_duration(seconds: Optional[float]) -> str:
    if not seconds:
        return "-"
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _downsample(xs: list, ys: list, target: int = 200) -> tuple[list, list]:
    """차트용으로 (x,y) 쌍을 target개 정도로 균등 추출. y가 None인 지점은 건너뛴다."""
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) <= target:
        return [p[0] for p in pairs], [p[1] for p in pairs]
    step = len(pairs) / target
    picked = [pairs[int(i * step)] for i in range(target)]
    return [p[0] for p in picked], [p[1] for p in picked]


def hr_zone_time(hr_stream: list, time_stream: list, zones: Optional[dict] = None) -> dict[str, int]:
    """heartrate/time 스트림으로 각 HR존 체류 시간(초)을 계산(Strava zones 없을 때 폴백)."""
    zones = zones or HR_ZONES
    result = {z: 0 for z in zones}
    if not hr_stream or not time_stream or len(hr_stream) != len(time_stream):
        return result
    for i in range(1, len(time_stream)):
        dt = (time_stream[i] or 0) - (time_stream[i - 1] or 0)
        if dt <= 0 or dt > 30:  # 큰 갭(일시정지)은 무시
            continue
        z = hr_zone(hr_stream[i], zones)
        if z:
            result[z] += dt
    return result


def km_splits(dist_m: list, time_s: list, hr: Optional[list], unit_m: float = 1000.0,
              zones: Optional[dict] = None) -> list[dict]:
    """거리/시간/심박 스트림을 1km(unit_m) 단위로 롤업한 스플릿 목록을 만든다."""
    if not dist_m or not time_s or len(dist_m) != len(time_s):
        return []
    splits = []
    mark = unit_m
    seg_start_t = time_s[0]
    seg_start_i = 0
    n = len(dist_m)
    for i in range(1, n):
        d0, d1 = dist_m[i - 1], dist_m[i]
        # 한 스텝에 여러 km 경계를 넘을 수도 있음
        while d1 >= mark and d1 > d0:
            frac = (mark - d0) / (d1 - d0)
            t_at = time_s[i - 1] + frac * (time_s[i] - time_s[i - 1])
            seg_hr = [hr[j] for j in range(seg_start_i, i) if hr and hr[j] is not None] if hr else []
            pace = t_at - seg_start_t  # unit_m가 1000이면 이 값이 곧 sec/km
            splits.append(
                {
                    "km": round(mark / 1000, 1),
                    "distance_km": round(unit_m / 1000, 2),
                    "time_str": format_duration(pace),
                    "pace_sec": pace,
                    "pace_str": format_pace(pace),
                    "gap_pace_str": None,
                    "elev": None,
                    "avg_hr": round(sum(seg_hr) / len(seg_hr)) if seg_hr else None,
                    "zone": hr_zone(sum(seg_hr) / len(seg_hr), zones) if seg_hr else None,
                }
            )
            seg_start_t = t_at
            seg_start_i = i
            mark += unit_m
    # 마지막 자투리 구간
    total = dist_m[-1]
    prev_mark = mark - unit_m
    if total - prev_mark > 30:  # 30m 이상 남으면 표시
        part_km = (total - prev_mark) / 1000
        seg_hr = [hr[j] for j in range(seg_start_i, n) if hr and hr[j] is not None] if hr else []
        elapsed = time_s[-1] - seg_start_t
        pace = elapsed / part_km if part_km else None
        splits.append(
            {
                "km": round(total / 1000, 2),
                "distance_km": round(part_km, 2),
                "time_str": format_duration(elapsed),
                "pace_sec": pace,
                "pace_str": format_pace(pace),
                "gap_pace_str": None,
                "elev": None,
                "avg_hr": round(sum(seg_hr) / len(seg_hr)) if seg_hr else None,
                "zone": hr_zone(sum(seg_hr) / len(seg_hr), zones) if seg_hr else None,
            }
        )
    # 상대 페이스 막대(빠를수록 길게)
    paces = [s["pace_sec"] for s in splits if s["pace_sec"]]
    fast, slow = (min(paces), max(paces)) if paces else (None, None)
    for s in splits:
        if s["pace_sec"] and fast and slow and slow > fast:
            s["bar_pct"] = round(100 * (slow - s["pace_sec"]) / (slow - fast))
        else:
            s["bar_pct"] = 50
    return splits


def annotate_vs_goal(rows: list[dict], goal_pace: Optional[int], cap: int = 180) -> None:
    """각 구간에 목표 페이스 대비 차이(±)와 발산 막대 폭(0~50%)을 채운다."""
    for r in rows:
        p = r.get("pace_sec")
        if not p or not goal_pace:
            r["vs_goal_str"] = "—"
            r["vs_goal_dir"] = "none"
            r["bar_half"] = 0
            continue
        delta = p - goal_pace  # +면 목표보다 느림, -면 빠름
        r["vs_goal_dir"] = "slow" if delta > 0 else "fast"
        a = int(abs(round(delta)))
        m, s = divmod(a, 60)
        sign = "+" if delta > 0 else "-"
        r["vs_goal_str"] = (f"{sign}{m}'{s:02d}\"" if m else f"{sign}{s}\"")
        r["bar_half"] = round(min(50.0, abs(delta) / cap * 50), 1)


def decode_polyline(encoded: str) -> list[list[float]]:
    """Google encoded polyline → [[lat, lng], ...] (외부 의존성 없이 디코드)."""
    if not encoded:
        return []
    coords: list[list[float]] = []
    index = lat = lng = 0
    length = len(encoded)
    while index < length:
        for is_lng in (False, True):
            shift = result = 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if (result & 1) else (result >> 1)
            if is_lng:
                lng += delta
            else:
                lat += delta
        coords.append([lat / 1e5, lng / 1e5])
    return coords


def strava_splits(splits_metric: list, zones: Optional[dict] = None) -> list[dict]:
    """Strava splits_metric(pace-analysis 원본)에서 1km 스플릿 표를 만든다(GAP·고도 포함)."""
    out = []
    for s in splits_metric or []:
        d = s.get("distance") or 0
        mt = s.get("moving_time") or 0
        pace = mt / (d / 1000) if d else None
        gs = s.get("average_grade_adjusted_speed")
        gap = (1000 / gs) if gs and gs > 0 else None
        hr = s.get("average_heartrate")
        elev = s.get("elevation_difference")
        out.append({
            "km": s.get("split"),
            "distance_km": round(d / 1000, 2),
            "time_str": format_duration(mt),
            "pace_sec": pace,
            "pace_str": format_pace(pace),
            "gap_pace_str": format_pace(gap),
            "avg_hr": round(hr) if hr else None,
            "zone": hr_zone(hr, zones) if hr else None,
            "elev": round(elev) if elev is not None else None,
        })
    return out


def activity_detail(conn: sqlite3.Connection, activity_id: int) -> Optional[dict]:
    a = db.get_activity(conn, activity_id)
    if a is None:
        return None
    raw = json.loads(a["raw_json"]) if a["raw_json"] else {}
    laps = db.laps_for(conn, activity_id)
    streams = db.streams_for(conn, activity_id)
    goal_pace = resolve_goal(conn)["pace_sec"]
    zones = resolve_hr_zones(conn)
    strava_zones = db.zones_for(conn, activity_id)

    def col(s, key):
        return s[key] if key in s.keys() else None

    avg_pace = pace_sec_per_km(a["distance_m"], a["moving_time_s"])

    lap_rows = []
    lap_paces = []
    for lp in laps:
        p = pace_sec_per_km(lp["distance_m"], lp["moving_time_s"])
        lap_paces.append(p)
        lap_rows.append(
            {
                "index": lp["lap_index"] + 1,
                "distance_km": round((lp["distance_m"] or 0) / 1000, 2),
                "time_str": format_duration(lp["moving_time_s"]),
                "pace_sec": p,
                "pace_str": format_pace(p),
                "avg_hr": round(lp["average_heartrate"]) if lp["average_heartrate"] else None,
                "zone": hr_zone(lp["average_heartrate"], zones),
            }
        )
    valid_paces = [p for p in lap_paces if p]
    fastest = min(valid_paces) if valid_paces else None
    slowest = max(valid_paces) if valid_paces else None
    for row in lap_rows:
        # 페이스 막대: 빠를수록 길게 (0~100%)
        if row["pace_sec"] and fastest and slowest and slowest > fastest:
            row["bar_pct"] = round(100 * (slowest - row["pace_sec"]) / (slowest - fastest))
        else:
            row["bar_pct"] = 50

    # 스트림 → 차트 데이터 (x축: 거리 km)
    def sdata(key):
        v = streams.get(key)
        return v.get("data") if isinstance(v, dict) else None

    dist_m = sdata("distance") or []
    dist_km = [round(d / 1000, 3) for d in dist_m] if dist_m else []
    hr = sdata("heartrate")
    vel = sdata("velocity_smooth")
    alt = sdata("altitude")
    time_s = sdata("time") or []

    # 랩 HR이 비어있으면(Apple Health 임포트 유실) 백필된 심박 스트림의 랩 구간 평균으로 계산
    if hr:
        for lp, row in zip(laps, lap_rows):
            if row["avg_hr"] is not None:
                continue
            lraw = json.loads(lp["raw_json"]) if lp["raw_json"] else {}
            si, ei = lraw.get("start_index"), lraw.get("end_index")
            if si is None or ei is None:
                continue
            seg = [hr[j] for j in range(si, min(ei + 1, len(hr))) if hr[j] is not None]
            if seg:
                avg = sum(seg) / len(seg)
                row["avg_hr"] = round(avg)
                row["zone"] = hr_zone(avg, zones)

    pace_series = (
        [(1000 / v) if v and v > 0.5 else None for v in vel] if vel else None
    )  # sec/km, 멈춤 구간 제외

    charts = {}
    if dist_km and hr:
        x, y = _downsample(dist_km, hr)
        charts["hr"] = {"x": x, "y": y}
    if dist_km and pace_series:
        x, y = _downsample(dist_km, pace_series)
        charts["pace"] = {"x": x, "y": [round(p) for p in y]}
    if dist_km and alt:
        x, y = _downsample(dist_km, alt)
        charts["altitude"] = {"x": x, "y": y}

    # 3개 차트 연동용: 같은 거리축(x)에 HR/페이스/고도/좌표를 index로 정렬해 다운샘플
    chart_sync = None
    if dist_km:
        cs_latlng = sdata("latlng")
        n = len(dist_km)
        step = max(1, n // 250)
        xs, cts, chr_, cpc, cal, clat, clng = [], [], [], [], [], [], []
        for i in range(0, n, step):
            xs.append(dist_km[i])
            cts.append(time_s[i] if i < len(time_s) else None)
            chr_.append(round(hr[i]) if hr and i < len(hr) and hr[i] is not None else None)
            v = vel[i] if vel and i < len(vel) else None
            cpc.append(round(1000 / v) if v and v > 0.5 else None)
            cal.append(round(alt[i], 1) if alt and i < len(alt) else None)
            if cs_latlng and i < len(cs_latlng):
                clat.append(cs_latlng[i][0])
                clng.append(cs_latlng[i][1])
            else:
                clat.append(None)
                clng.append(None)
        chart_sync = {
            "x": xs, "t": cts, "hr": chr_, "pace": cpc, "alt": cal, "lat": clat, "lng": clng,
            "has_hr": any(v is not None for v in chr_),
            "has_pace": any(v is not None for v in cpc),
            "has_alt": any(v is not None for v in cal),
            "has_latlng": any(v is not None for v in clat),
        }

    # HR존 체류시간: Strava가 계산한 값 우선, 없으면 스트림에서 폴백
    strava_zt = zone_time_from_strava(strava_zones)
    zone_time = strava_zt if strava_zt else (hr_zone_time(hr, time_s, zones) if hr else {})
    pace_dist = pace_distribution(strava_zones)

    # 스플릿: Strava splits_metric(원본) 우선, 없으면 스트림에서 계산
    sm = a["splits_metric"] if "splits_metric" in a.keys() else None
    if sm:
        splits = strava_splits(json.loads(sm), zones)
    elif dist_m and time_s:
        splits = km_splits(dist_m, time_s, hr, zones=zones)
    else:
        splits = []

    # 목표 페이스 대비(±) 주석
    annotate_vs_goal(lap_rows, goal_pace)
    annotate_vs_goal(splits, goal_pace)

    route = decode_polyline((raw.get("map") or {}).get("summary_polyline", ""))

    # 재생(replay)용 프레임: latlng 스트림 기준으로 시각·거리·HR·페이스·고도를 정렬
    latlng = sdata("latlng")
    playback = []
    if latlng and time_s:
        n = len(latlng)
        target = 300
        step = max(1, n // target)
        for i in range(0, n, step):
            ll = latlng[i]
            v = vel[i] if vel and i < len(vel) else None
            playback.append(
                {
                    "t": time_s[i] if i < len(time_s) else None,
                    "d": round(dist_m[i] / 1000, 3) if dist_m and i < len(dist_m) else None,
                    "lat": ll[0],
                    "lng": ll[1],
                    "hr": round(hr[i]) if hr and i < len(hr) and hr[i] is not None else None,
                    "pace": round(1000 / v) if v and v > 0.5 else None,
                    "alt": round(alt[i], 1) if alt and i < len(alt) else None,
                }
            )

    return {
        "id": a["id"],
        "name": a["name"],
        "date": a["start_date"][:10] if a["start_date"] else None,
        "distance_km": round((a["distance_m"] or 0) / 1000, 2),
        "moving_time_str": format_duration(a["moving_time_s"]),
        "avg_pace_str": format_pace(avg_pace),
        "avg_hr": round(a["average_heartrate"]) if a["average_heartrate"] else None,
        "max_hr": round(a["max_heartrate"]) if a["max_heartrate"] else None,
        "elevation_gain": round(raw.get("total_elevation_gain")) if raw.get("total_elevation_gain") else None,
        "device": raw.get("device_name"),
        "type": classify_session(a, laps, zones),
        "has_hr": bool(hr),
        "route": route,
        "playback": playback,
        "laps": lap_rows,
        "splits": splits,
        "charts": charts,
        "chart_sync": chart_sync,
        "zone_time": zone_time,
        "pace_dist": pace_dist,
    }


def weekly_volume(sessions: list[dict], weeks: int = 8) -> list[dict]:
    buckets: dict[str, float] = {}
    for s in sessions:
        if not s["date"]:
            continue
        d = date.fromisoformat(s["date"])
        week_start = d - timedelta(days=d.weekday())
        key = week_start.isoformat()
        buckets[key] = buckets.get(key, 0) + s["distance_km"]
    ordered = sorted(buckets.items())[-weeks:]
    return [{"week": k, "distance_km": round(v, 1)} for k, v in ordered]


_PR_ORDER = ["400m", "1/2 mile", "1K", "1 mile", "2 mile", "5K", "10K",
             "15K", "10 mile", "20K", "Half-Marathon", "Marathon"]


def best_efforts_pr(conn: sqlite3.Connection) -> list[dict]:
    """전 활동의 best_efforts를 모아 거리별 실제 최고기록(PR)을 산출."""
    best: dict[str, dict] = {}
    for a in db.all_activities(conn):
        raw = a["best_efforts"] if "best_efforts" in a.keys() else None
        if not raw:
            continue
        for e in json.loads(raw):
            name, t, dist = e.get("name"), e.get("elapsed_time"), e.get("distance")
            if not name or not t:
                continue
            if name not in best or t < best[name]["time"]:
                best[name] = {
                    "time": t,
                    "distance": dist,
                    "date": a["start_date"][:10] if a["start_date"] else None,
                    "activity_id": a["id"],
                }
    ordered = [n for n in _PR_ORDER if n in best] + [n for n in best if n not in _PR_ORDER]
    out = []
    for name in ordered:
        b = best[name]
        pace = (b["time"] / (b["distance"] / 1000)) if b["distance"] else None
        out.append({
            "name": name,
            "time_str": format_duration(b["time"]),
            "pace_str": format_pace(pace),
            "date": b["date"],
            "activity_id": b["activity_id"],
        })
    return out


def monthly_trends(sessions: list[dict]) -> list[dict]:
    """월별 누적거리·평균HR·평균페이스(거리 가중)를 정리해 비교용으로 반환(오름차순)."""
    buckets: dict[str, dict] = {}
    for s in sessions:
        if not s["date"]:
            continue
        m = s["date"][:7]
        b = buckets.setdefault(
            m,
            {"km": 0.0, "time": 0.0, "hr_wsum": 0.0, "hr_w": 0.0, "n": 0,
             "max_hr": None, "best_pace": None, "longest": 0.0,
             "effort": 0.0, "gap_wsum": 0.0, "gap_w": 0.0},
        )
        km = s["distance_km"] or 0
        b["km"] += km
        b["n"] += 1
        b["longest"] = max(b["longest"], km)
        if s.get("suffer_score"):
            b["effort"] += s["suffer_score"]
        if s.get("gap_pace_sec") and km:
            b["gap_wsum"] += s["gap_pace_sec"] * km  # 거리 가중 GAP
            b["gap_w"] += km
        if s["avg_pace"] and km:
            b["time"] += s["avg_pace"] * km  # 총 시간(초) = 페이스 * 거리
            # 최고(가장 빠른) 페이스: 2km 이상 러닝만 후보(짧은 구간 노이즈 제외)
            if km >= 2 and (b["best_pace"] is None or s["avg_pace"] < b["best_pace"]):
                b["best_pace"] = s["avg_pace"]
        if s["avg_hr"] and km:
            b["hr_wsum"] += s["avg_hr"] * km  # 거리 가중 HR
            b["hr_w"] += km
        if s.get("max_hr"):
            b["max_hr"] = max(b["max_hr"] or 0, s["max_hr"])

    out = []
    for m in sorted(buckets):
        b = buckets[m]
        avg_pace = (b["time"] / b["km"]) if b["km"] else None
        avg_hr = (b["hr_wsum"] / b["hr_w"]) if b["hr_w"] else None
        avg_gap = (b["gap_wsum"] / b["gap_w"]) if b["gap_w"] else None
        out.append(
            {
                "month": m,
                "label": f"{int(m[5:7])}월",
                "total_km": round(b["km"], 1),
                "longest_km": round(b["longest"], 1),
                "sessions": b["n"],
                "avg_pace_sec": round(avg_pace) if avg_pace else None,
                "avg_pace_str": format_pace(avg_pace),
                "best_pace_sec": round(b["best_pace"]) if b["best_pace"] else None,
                "best_pace_str": format_pace(b["best_pace"]),
                "gap_pace_sec": round(avg_gap) if avg_gap else None,
                "gap_pace_str": format_pace(avg_gap),
                "avg_hr": round(avg_hr, 1) if avg_hr else None,
                "max_hr": round(b["max_hr"]) if b["max_hr"] else None,
                "rel_effort": round(b["effort"]) if b["effort"] else 0,
            }
        )
    return out


def race_predictions(sessions: list[dict], distances_km=(5, 10)) -> dict:
    """세션 기록 중 최고 성능을 기준으로 Riegel 공식으로 목표 거리 기록/페이스를 예측."""
    # 2km 이상, 페이스 있는 러닝만 예측 근거로 사용
    cands = [s for s in sessions if s["distance_km"] >= 2 and s["avg_pace"]]
    if not cands:
        return {"predictions": [], "based_on": None}

    RIEGEL = 1.06

    def pred_time(d1_km, t1_s, d2_km):
        return t1_s * (d2_km / d1_km) ** RIEGEL

    preds = {}
    best_ref = {}
    for d2 in distances_km:
        best_t = None
        ref = None
        for s in cands:
            d1 = s["distance_km"]
            t1 = s["avg_pace"] * d1  # 총 시간(초)
            t2 = pred_time(d1, t1, d2)
            if best_t is None or t2 < best_t:
                best_t = t2
                ref = s
        preds[d2] = best_t
        best_ref[d2] = ref

    predictions = []
    for d2 in distances_km:
        t = preds[d2]
        pace = t / d2
        predictions.append(
            {
                "distance_km": d2,
                "time_str": format_duration(t),
                "pace_str": format_pace(pace),
                "based_on_date": best_ref[d2]["date"] if best_ref[d2] else None,
                "based_on_km": best_ref[d2]["distance_km"] if best_ref[d2] else None,
            }
        )
    return {"predictions": predictions}


def hr_profile(conn: sqlite3.Connection) -> dict:
    """존별 시간(=Strava 계산값)과 존별/심박별 평균페이스를 집계. HR존은 Strava에서 가져옴."""
    zone_bounds = resolve_hr_zones(conn)

    # 1) 존별 체류시간: Strava가 계산한 값을 집계(권위값, 하드코딩 없음)
    strava_zt: dict[str, float] = {z: 0.0 for z in zone_bounds}
    have_strava = False
    for zlist in db.all_zones(conn):
        zt = zone_time_from_strava(zlist)
        if zt:
            have_strava = True
            for z, t in zt.items():
                strava_zt[z] = strava_zt.get(z, 0.0) + t

    # 2) 존별/심박별 페이스: 1km 구간 평균으로 짝지어 심박지연·걷기 노이즈 제거
    #    (초 단위 순간 페이스는 심박 지연/정지로 왜곡되므로 사용하지 않음)
    zone_pace: dict[str, list] = {z: [] for z in zone_bounds}
    stream_zt: dict[str, float] = {z: 0.0 for z in zone_bounds}
    bin_pace: dict[int, list] = {}
    hr_max_obs = 0

    for a in db.all_activities(conn):
        if a["max_heartrate"]:
            hr_max_obs = max(hr_max_obs, a["max_heartrate"])
        streams = db.streams_for(conn, a["id"])
        if not streams:
            continue
        hr = (streams.get("heartrate") or {}).get("data")
        tm = (streams.get("time") or {}).get("data")
        dist = (streams.get("distance") or {}).get("data")
        if not hr or not tm:
            continue
        # Strava 존시간이 없을 때만 쓰는 폴백(스트림 기반)
        if not have_strava:
            for i in range(1, len(hr)):
                h = hr[i]
                if h is None:
                    continue
                dt = (tm[i] or 0) - (tm[i - 1] or 0)
                if 0 < dt <= 30:
                    z = hr_zone(h, zone_bounds)
                    if z:
                        stream_zt[z] += dt
        # 페이스: 1km 구간 평균(심박·페이스를 같은 구간으로 매칭)
        if dist:
            for seg in km_splits(dist, tm, hr, zones=zone_bounds):
                if seg["avg_hr"] and seg["pace_sec"] and seg["distance_km"] >= 0.8:
                    z = hr_zone(seg["avg_hr"], zone_bounds)
                    if z:
                        zone_pace[z].append(seg["pace_sec"])
                    bin_pace.setdefault(int(seg["avg_hr"] // 5 * 5), []).append(seg["pace_sec"])

    zone_time = strava_zt if have_strava else stream_zt
    total_time = sum(zone_time.values())

    zones = []
    for z, (low, high) in zone_bounds.items():
        paces = zone_pace[z]
        zones.append(
            {
                "zone": z,
                "low": low,
                "high": None if high >= 999 else high,
                "time_s": round(zone_time.get(z, 0)),
                "time_str": format_duration(zone_time.get(z, 0)),
                "pct": round(100 * zone_time.get(z, 0) / total_time, 1) if total_time else 0,
                "avg_pace": format_pace(sum(paces) / len(paces)) if paces else None,
            }
        )

    hr_pace = [
        {
            "hr": b,
            "avg_pace": format_pace(sum(v) / len(v)),
            "avg_pace_sec": round(sum(v) / len(v)),
            "n": len(v),
        }
        for b, v in sorted(bin_pace.items())
        if len(v) >= 3  # 1km 구간 표본 3개 이상
    ]

    return {
        "zones": zones,
        "hr_pace": hr_pace,
        "hr_max_observed": round(hr_max_obs) if hr_max_obs else None,
        "total_time_str": format_duration(total_time),
    }


def resolve_goal(conn: sqlite3.Connection) -> dict:
    """DB에 저장된 목표가 있으면 그것을, 없으면 config 기본값을 반환."""
    s = db.get_settings(conn)
    return {
        "distance_km": float(s.get("goal_distance_km") or GOAL_DISTANCE_KM),
        "pace_sec": int(float(s.get("goal_pace_sec") or GOAL_PACE_SEC_PER_KM)),
        "date": s.get("goal_date") or GOAL_DATE.isoformat(),
    }


def training_analysis(sessions: list[dict], goal: dict, today: Optional[date] = None) -> dict:
    """실제 세션 데이터로 훈련 상태를 분석한다: 볼륨/ACWR/롱런/페이스·존 분포/목표 격차."""
    today = today or date.today()

    def in_days(s, lo, hi):
        if not s["date"]:
            return False
        age = (today - date.fromisoformat(s["date"])).days
        return lo <= age < hi

    last_7 = [s for s in sessions if in_days(s, 0, 7)]
    last_28 = [s for s in sessions if in_days(s, 0, 28)]

    km_7 = sum(s["distance_km"] for s in last_7)
    km_28 = sum(s["distance_km"] for s in last_28)
    acute = km_7
    chronic = km_28 / 4 if km_28 else 0
    acwr = round(acute / chronic, 2) if chronic else None

    type_counts_28: dict[str, int] = {}
    for s in last_28:
        type_counts_28[s["type"]] = type_counts_28.get(s["type"], 0) + 1

    easy_paces = [s["avg_pace"] for s in last_28 if s["type"] == "easy" and s["avg_pace"]]
    quality_paces = [
        s["avg_pace"] for s in last_28 if s["type"] in ("tempo", "threshold", "vo2") and s["avg_pace"]
    ]
    hrs = [s["avg_hr"] for s in last_28 if s["avg_hr"]]

    longest = max((s for s in sessions if s["distance_km"]), key=lambda s: s["distance_km"], default=None)

    # 목표 격차: 최근 5km 이상 러닝 중 가장 빠른 평균 페이스로 근사
    fast_candidates = [s["avg_pace"] for s in sessions if s["distance_km"] >= 5 and s["avg_pace"]]
    best_recent_pace = min(fast_candidates) if fast_candidates else None
    goal_pace = goal["pace_sec"]
    pace_gap = round(best_recent_pace - goal_pace) if best_recent_pace else None
    days_to_goal = (date.fromisoformat(goal["date"]) - today).days

    return {
        "weeks_of_data": len({s["date"][:7] + str(date.fromisoformat(s["date"]).isocalendar().week) for s in sessions if s["date"]}),
        "sessions_28d": len(last_28),
        "km_7d": round(km_7, 1),
        "km_28d": round(km_28, 1),
        "avg_weekly_km": round(km_28 / 4, 1) if km_28 else 0,
        "acwr": acwr,
        "acwr_flag": "높음(부상위험)" if acwr and acwr > 1.3 else ("낮음" if acwr and acwr < 0.8 else "정상"),
        "type_counts_28d": type_counts_28,
        "avg_easy_pace": format_pace(sum(easy_paces) / len(easy_paces)) if easy_paces else None,
        "avg_quality_pace": format_pace(sum(quality_paces) / len(quality_paces)) if quality_paces else None,
        "avg_hr_28d": round(sum(hrs) / len(hrs)) if hrs else None,
        "longest_run_km": longest["distance_km"] if longest else None,
        "best_recent_pace": format_pace(best_recent_pace),
        "goal_pace": format_pace(goal_pace),
        "goal_distance_km": goal["distance_km"],
        "goal_date": goal["date"],
        "days_to_goal": days_to_goal,
        "pace_gap_sec": pace_gap,
        "goal_status": (
            "목표 도달" if pace_gap is not None and pace_gap <= 0
            else (f"목표까지 {pace_gap}초/km 단축 필요" if pace_gap is not None else "판단 데이터 부족")
        ),
    }


def recent_timeline(sessions: list[dict], today: Optional[date] = None, days: int = 14) -> list[dict]:
    """최근 N일을 날짜별로: 뛴 날은 세션 요약, 안 뛴 날은 rest로 표시."""
    today = today or date.today()
    by_date: dict[str, list[dict]] = {}
    for s in sessions:
        if s["date"]:
            by_date.setdefault(s["date"], []).append(s)
    out = []
    for i in range(days - 1, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        runs = by_date.get(d)
        if runs:
            for r in runs:
                out.append({
                    "date": d, "rest": False, "type": r["type"],
                    "distance_km": r["distance_km"], "pace": r["avg_pace_str"], "avg_hr": r["avg_hr"],
                })
        else:
            out.append({"date": d, "rest": True})
    return out


def days_since_last_run(sessions: list[dict], today: Optional[date] = None) -> Optional[int]:
    today = today or date.today()
    dated = [date.fromisoformat(s["date"]) for s in sessions if s["date"]]
    if not dated:
        return None
    return (today - max(dated)).days


def recent_summary(sessions: list[dict]) -> dict:
    today = date.today()
    last_7 = [s for s in sessions if s["date"] and (today - date.fromisoformat(s["date"])).days <= 7]
    prev_7 = [
        s for s in sessions if s["date"] and 7 < (today - date.fromisoformat(s["date"])).days <= 14
    ]

    weekly_km = sum(s["distance_km"] for s in last_7)
    prev_km = sum(s["distance_km"] for s in prev_7)
    trend_pct = ((weekly_km - prev_km) / prev_km * 100) if prev_km else 0

    last_by_type: dict[str, str] = {}
    for s in sorted(sessions, key=lambda x: x["date"] or ""):
        if s["date"]:
            last_by_type[s["type"]] = s["date"]

    return {
        "weekly_km": round(weekly_km, 1),
        "weekly_km_trend_pct": round(trend_pct, 1),
        "session_count_7d": len(last_7),
        "last_session_by_type": last_by_type,
    }
