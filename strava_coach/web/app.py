import json
import os
import re
from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

from .. import analysis, coach_llm, db, planner
from ..sync import sync_all


def render_commentary(text: str) -> Markup:
    """LLM이 준 **굵게**·줄바꿈만 안전하게 HTML로 변환."""
    html = str(escape(text))
    html = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html)
    html = html.replace("\n", "<br>")
    return Markup(html)

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Strava Coach")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

# HF Spaces 등 호스팅 환경 여부(HF가 SPACE_ID를 자동 주입). 호스팅에선 로컬 동기화 버튼 숨김.
IS_HOSTED = bool(os.environ.get("SPACE_ID"))
templates.env.globals["is_hosted"] = IS_HOSTED


@app.get("/healthz")
def healthz():
    from fastapi.responses import PlainTextResponse

    return PlainTextResponse("ok")


def _sessions() -> list[dict]:
    conn = db.get_connection()
    activities = db.all_activities(conn)
    return analysis.summarize_activities(conn, activities)


RANGE_DAYS = {"1w": 7, "1m": 30, "3m": 90, "6m": 180, "1y": 365, "all": None}
RANGE_LABELS = [("1w", "1주일"), ("1m", "1달"), ("3m", "3달"), ("6m", "6달"), ("1y", "1년"), ("all", "전체")]
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


def _month_labels(sessions: list[dict]) -> list[tuple[str, str]]:
    """데이터에 존재하는 월을 오름차순으로 (키, '7월') 라벨로."""
    months = sorted({s["date"][:7] for s in sessions if s["date"]})
    return [(m, f"{int(m[5:7])}월") for m in months]


def _apply_range(sessions: list[dict], rng: str) -> list[dict]:
    """rng: 1w/1m/3m/6m/1y/all(오늘 기준) 또는 'YYYY-MM'(해당 월)."""
    if rng in RANGE_DAYS:
        days = RANGE_DAYS[rng]
        if days is None:
            return sessions
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        return [s for s in sessions if s["date"] and s["date"] >= cutoff]
    if _MONTH_RE.match(rng):
        return [s for s in sessions if s["date"] and s["date"][:7] == rng]
    return sessions


@app.get("/")
def dashboard(request: Request, range: str = "all"):
    sessions = _sessions()
    filtered = _apply_range(sessions, range)

    weekly = analysis.weekly_volume(filtered, weeks=52)
    total_km = round(sum(s["distance_km"] for s in filtered), 1)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "weekly": weekly,
            "sessions": list(reversed(filtered)),
            "total_count": len(filtered),
            "total_km": total_km,
            "range": range,
            "range_labels": RANGE_LABELS,
            "month_labels": _month_labels(sessions),
        },
    )


@app.get("/sessions")
def sessions_view(request: Request, range: str = "all"):
    sessions = _sessions()
    filtered = _apply_range(sessions, range)
    total_km = round(sum(s["distance_km"] for s in filtered), 1)
    return templates.TemplateResponse(
        request,
        "sessions.html",
        {
            "sessions": list(reversed(filtered)),
            "total_count": len(filtered),
            "total_km": total_km,
            "range": range,
            "range_labels": RANGE_LABELS,
            "month_labels": _month_labels(sessions),
        },
    )


@app.get("/activity/{activity_id}")
def activity_view(request: Request, activity_id: int):
    conn = db.get_connection()
    detail = analysis.activity_detail(conn, activity_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="활동을 찾을 수 없습니다.")
    return templates.TemplateResponse(request, "activity.html", {"a": detail})


def _plan_context():
    conn = db.get_connection()
    sessions = _sessions()
    goal = analysis.resolve_goal(conn)
    today = date.today()
    summary = analysis.recent_summary(sessions)
    ta = analysis.training_analysis(sessions, goal, today)
    plan = planner.build_next_week_plan(summary, today, date.fromisoformat(goal["date"]))
    return ta, plan, goal


@app.get("/profile")
def profile_view(request: Request):
    conn = db.get_connection()
    sessions = _sessions()
    profile = analysis.hr_profile(conn)
    races = analysis.race_predictions(sessions)
    monthly = analysis.monthly_trends(sessions)
    return templates.TemplateResponse(
        request, "profile.html", {"profile": profile, "races": races, "monthly": monthly}
    )


@app.get("/plan")
def plan_view(request: Request):
    ta, plan, goal = _plan_context()
    conn = db.get_connection()
    saved_raw = db.get_state(conn, "ai_plan")
    saved_plan = json.loads(saved_raw) if saved_raw else None
    return templates.TemplateResponse(
        request,
        "plan.html",
        {"plan": plan, "analysis": ta, "goal": goal, "saved_plan": saved_plan},
    )


@app.get("/api/coach")
def api_coach():
    from fastapi.responses import JSONResponse

    conn = db.get_connection()
    sessions = _sessions()
    goal = analysis.resolve_goal(conn)
    today = date.today()
    summary = analysis.recent_summary(sessions)
    ta = analysis.training_analysis(sessions, goal, today)
    ta["days_since_last_run"] = analysis.days_since_last_run(sessions, today)
    rule_plan = planner.build_next_week_plan(summary, today, date.fromisoformat(goal["date"]))

    # 최근 14일을 날짜별(쉰 날 포함) 타임라인으로 AI에 제공
    recent_days = analysis.recent_timeline(sessions, today, days=14)
    result = coach_llm.generate_plan(ta, goal, recent_days, today.isoformat(), rule_plan["phase"])
    if "error" in result:
        return JSONResponse({"error": result["error"]})
    payload = {
        "analysis_html": str(render_commentary(result.get("analysis", ""))),
        "focus": result.get("focus", ""),
        "plan": result.get("plan", []),
        "generated": today.isoformat(),
        "goal": goal,
    }
    db.set_settings(conn, {"ai_plan": json.dumps(payload, ensure_ascii=False)})
    conn.commit()
    return JSONResponse(payload)


@app.post("/goal")
def set_goal(
    goal_distance_km: float = Form(...),
    goal_pace_min: int = Form(...),
    goal_pace_sec: int = Form(...),
    goal_date: str = Form(...),
):
    conn = db.get_connection()
    db.set_settings(
        conn,
        {
            "goal_distance_km": goal_distance_km,
            "goal_pace_sec": goal_pace_min * 60 + goal_pace_sec,
            "goal_date": goal_date,
        },
    )
    conn.commit()
    return RedirectResponse(url="/plan", status_code=303)


@app.post("/sync")
def trigger_sync():
    if IS_HOSTED:
        # 호스팅 환경엔 Strava 토큰/시크릿이 없음 → 동기화는 로컬 전용
        return RedirectResponse(url="/?sync=hosted", status_code=303)
    try:
        n = sync_all()
        return RedirectResponse(url=f"/?sync=ok&n={n}", status_code=303)
    except Exception:
        return RedirectResponse(url="/?sync=error", status_code=303)
