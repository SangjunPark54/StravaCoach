"""서울(기본) 일별 강수 예보 — Open-Meteo(무료·API키 불필요).

AI 코치가 '비 안 오는 날'에 야외 세션을 배치할 수 있도록 7일 예보를 제공.
네트워크 실패해도 앱이 죽지 않게 예외는 삼키고 [] 반환.
"""

import os

import httpx

WEATHER_LAT = float(os.environ.get("WEATHER_LAT", "37.5665"))
WEATHER_LON = float(os.environ.get("WEATHER_LON", "126.9780"))
WEATHER_LABEL = os.environ.get("WEATHER_LABEL", "서울")

# WMO weathercode → 한국어 요약
_WMO = {
    0: "맑음", 1: "대체로 맑음", 2: "부분 흐림", 3: "흐림",
    45: "안개", 48: "짙은 안개",
    51: "약한 이슬비", 53: "이슬비", 55: "강한 이슬비",
    56: "어는 이슬비", 57: "어는 이슬비",
    61: "약한 비", 63: "비", 65: "강한 비",
    66: "어는 비", 67: "어는 비",
    71: "약한 눈", 73: "눈", 75: "강한 눈", 77: "싸락눈",
    80: "약한 소나기", 81: "소나기", 82: "강한 소나기",
    85: "약한 눈소나기", 86: "눈소나기",
    95: "뇌우", 96: "우박 동반 뇌우", 99: "강한 우박 뇌우",
}


def _rainy(precip_mm: float, prob: float) -> bool:
    """야외 러닝에 지장 있는 '비 오는 날' 판정(강수량 우선)."""
    if precip_mm is not None and precip_mm >= 1.0:
        return True
    return bool(prob is not None and prob >= 60)


# 러닝 가능 시간대(05~22시)와 '건조' 임계값(mm/h)
RUN_HOUR_START = 5
RUN_HOUR_END = 22
DRY_MM_PER_H = 0.1


def _dry_windows(hours: list[tuple]) -> tuple[list[str], str]:
    """(hour, precip_mm) 목록에서 러닝시간대의 건조 구간을 'HH-HH'로 병합.

    반환: (구간 리스트, 폴백 문구). 건조 구간 없으면 리스트 빈값 + 가장 약한 시간 안내.
    """
    windows = []
    start = None
    for hh, mm in hours:
        if not (RUN_HOUR_START <= hh <= RUN_HOUR_END):
            continue
        dry = (mm or 0) <= DRY_MM_PER_H
        if dry and start is None:
            start = hh
        elif not dry and start is not None:
            windows.append(f"{start:02d}-{hh:02d}시")
            start = None
    if start is not None:
        windows.append(f"{start:02d}-{RUN_HOUR_END:02d}시")
    if windows:
        return windows, ""
    # 건조 구간 없음 → 러닝시간대 중 강수 가장 약한 시간
    run_hours = [(hh, mm or 0) for hh, mm in hours if RUN_HOUR_START <= hh <= RUN_HOUR_END]
    if run_hours:
        best_h, best_mm = min(run_hours, key=lambda x: x[1])
        return [], f"종일 비 — 가장 약한 {best_h:02d}시({best_mm:.1f}mm/h)"
    return [], "예보 없음"


def seoul_forecast(days: int = 7) -> list[dict]:
    """일별 예보 리스트. 각 항목: date, precip_mm, precip_prob, temp_min/max, code, summary, rainy."""
    try:
        r = httpx.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": WEATHER_LAT,
                "longitude": WEATHER_LON,
                "daily": "precipitation_probability_max,precipitation_sum,"
                "weathercode,temperature_2m_max,temperature_2m_min",
                "hourly": "precipitation",
                "timezone": "Asia/Seoul",
                "forecast_days": max(1, min(days, 16)),
            },
            timeout=20,
        )
        r.raise_for_status()
        j = r.json()
        d = j["daily"]
        # 시간별 강수를 날짜별로 그룹핑(건조 시간대 계산용)
        hourly = j.get("hourly", {})
        by_date: dict[str, list] = {}
        for ts, pmm in zip(hourly.get("time", []), hourly.get("precipitation", [])):
            date_key, hh = ts[:10], int(ts[11:13])
            by_date.setdefault(date_key, []).append((hh, pmm))
        out = []
        for i, day in enumerate(d["time"]):
            mm = d["precipitation_sum"][i]
            prob = d["precipitation_probability_max"][i]
            code = d["weathercode"][i]
            windows, fallback = _dry_windows(sorted(by_date.get(day, [])))
            out.append(
                {
                    "date": day,
                    "precip_mm": mm,
                    "precip_prob": prob,
                    "temp_min": d["temperature_2m_min"][i],
                    "temp_max": d["temperature_2m_max"][i],
                    "code": code,
                    "summary": _WMO.get(code, f"code {code}"),
                    "rainy": _rainy(mm, prob),
                    "dry_windows": windows,          # 야외 러닝 가능한 건조 시간대
                    "dry_note": fallback,            # 건조 구간 없을 때 안내
                }
            )
        return out
    except Exception as e:  # noqa: BLE001
        print(f"[weather] 예보 조회 실패: {type(e).__name__}: {e}")
        return []
