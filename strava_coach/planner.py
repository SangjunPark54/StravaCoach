from datetime import date, timedelta
from typing import Optional

from .config import GOAL_DATE, WEEKLY_TEMPLATE

PHASE_INCLUDES = {
    "base": {"vo2", "tempo", "long_run", "easy"},
    "build": {"vo2", "threshold", "tempo", "long_run", "easy"},
    "peak": {"vo2", "threshold", "long_run", "easy"},
    "taper": {"vo2", "easy"},
}

DAY_ORDER = ["easy", "threshold", "easy", "vo2", "easy", "long_run", "easy"]


def current_phase(today: date, goal_date: Optional[date] = None) -> str:
    days_left = ((goal_date or GOAL_DATE) - today).days
    if days_left <= 7:
        return "taper"
    if days_left <= 28:
        return "peak"
    if days_left <= 70:
        return "build"
    return "base"


def _days_since(summary: dict, session_type: str, today: date) -> Optional[int]:
    last = summary.get("last_session_by_type", {}).get(session_type)
    if not last:
        return None
    return (today - date.fromisoformat(last)).days


def build_next_week_plan(
    summary: dict, today: Optional[date] = None, goal_date: Optional[date] = None
) -> dict:
    today = today or date.today()
    goal_date = goal_date or GOAL_DATE
    phase = current_phase(today, goal_date)
    include = PHASE_INCLUDES[phase]

    tempo_due = "tempo" in include and (_days_since(summary, "tempo", today) or 999) >= 12

    sessions = []
    for i, kind in enumerate(DAY_ORDER):
        d = today + timedelta(days=i)
        actual_kind = kind if kind in include else "easy"
        sessions.append(
            {
                "date": d.isoformat(),
                "type": actual_kind,
                "detail": WEEKLY_TEMPLATE[actual_kind]["desc"],
            }
        )

    if tempo_due:
        for s in sessions:
            if s["type"] == "easy":
                s["type"] = "tempo"
                s["detail"] = WEEKLY_TEMPLATE["tempo"]["desc"]
                break

    return {
        "phase": phase,
        "days_to_goal": (goal_date - today).days,
        "sessions": sessions,
        "acwr_warning": summary.get("weekly_km_trend_pct", 0) > 20,
    }
