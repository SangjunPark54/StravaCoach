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
                "timezone": "Asia/Seoul",
                "forecast_days": max(1, min(days, 16)),
            },
            timeout=20,
        )
        r.raise_for_status()
        d = r.json()["daily"]
        out = []
        for i, day in enumerate(d["time"]):
            mm = d["precipitation_sum"][i]
            prob = d["precipitation_probability_max"][i]
            code = d["weathercode"][i]
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
                }
            )
        return out
    except Exception as e:  # noqa: BLE001
        print(f"[weather] 예보 조회 실패: {type(e).__name__}: {e}")
        return []
