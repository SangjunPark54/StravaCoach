import json
import os
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

from .. import analysis, coach_llm, db, planner, state_sync, weather
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

# HF Spaces 등 호스팅 환경 여부(HF가 SPACE_ID를 자동 주입).
IS_HOSTED = bool(os.environ.get("SPACE_ID"))
templates.env.globals["is_hosted"] = IS_HOSTED

# 동기화 가능 여부: Strava client id/secret + (로컬 토큰파일 또는 STRAVA_REFRESH_TOKEN 환경변수)
from ..auth import TOKEN_FILE  # noqa: E402
from ..config import STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET  # noqa: E402

SYNC_ENABLED = bool(
    STRAVA_CLIENT_ID
    and STRAVA_CLIENT_SECRET
    and (TOKEN_FILE.exists() or os.environ.get("STRAVA_REFRESH_TOKEN"))
)
templates.env.globals["sync_enabled"] = SYNC_ENABLED


@app.on_event("startup")
def _pull_user_state():
    """기동 시 GitHub state 브랜치에서 목표·AI계획을 가져와 반영(재시작 보존)."""
    if state_sync.enabled():
        ok = state_sync.pull_state()
        print(f"[state_sync] 기동 pull: {'반영됨' if ok else '스킵/없음'}")


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


AUTO_SYNC_INTERVAL = 1800  # 30분 이내 재동기화 안 함
_sync_lock = threading.Lock()


def _maybe_auto_sync() -> bool:
    """앱 열 때 백그라운드 자동 동기화(스로틀·중복방지). 시작하면 True."""
    if not SYNC_ENABLED:
        return False
    conn = db.get_connection()
    last = db.get_state(conn, "last_auto_sync")
    if last and (time.time() - float(last)) < AUTO_SYNC_INTERVAL:
        return False
    if not _sync_lock.acquire(blocking=False):
        return False  # 이미 동기화 중

    def _run():
        try:
            c = db.get_connection()
            db.set_settings(c, {"last_auto_sync": str(time.time())})
            c.commit()
            n = sync_all()
            db.set_settings(c, {"last_auto_sync_result": f"ok:{n}"})
            c.commit()
        except Exception as e:  # noqa: BLE001
            # 실패를 기록하고 스로틀을 되돌려 다음 페이지 로드에서 바로 재시도되게 함
            print(f"[auto-sync] 실패: {type(e).__name__}: {e}")
            try:
                c = db.get_connection()
                db.set_settings(c, {
                    "last_auto_sync_result": f"error:{type(e).__name__}: {e}"[:300],
                    "last_auto_sync": "0",
                })
                c.commit()
            except Exception:  # noqa: BLE001
                pass
        finally:
            _sync_lock.release()

    threading.Thread(target=_run, daemon=True).start()
    return True


@app.get("/")
def dashboard(request: Request, range: str = "all"):
    auto_syncing = _maybe_auto_sync()
    conn = db.get_connection()
    last_result = db.get_state(conn, "last_auto_sync_result") or ""
    sync_error = last_result[6:] if last_result.startswith("error:") else None
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
            "auto_syncing": auto_syncing,
            "sync_error": sync_error,
        },
    )


@app.get("/sessions")
def sessions_view(request: Request, range: str = "all"):
    _maybe_auto_sync()
    conn = db.get_connection()
    sessions = _sessions()
    filtered = _apply_range(sessions, range)
    total_km = round(sum(s["distance_km"] for s in filtered), 1)
    progress_raw = db.get_user_value(conn, "plan_progress")
    progress = json.loads(progress_raw) if progress_raw else None
    has_plan = bool(db.get_user_value(conn, "ai_plan"))
    review_raw = db.get_user_value(conn, "session_review")
    review = json.loads(review_raw) if review_raw else None
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
            "progress": progress,
            "has_plan": has_plan,
            "review": review,
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
    auto_syncing = _maybe_auto_sync()
    conn = db.get_connection()
    sessions = _sessions()
    profile = analysis.hr_profile(conn)
    races = analysis.race_predictions(sessions)
    monthly = analysis.monthly_trends(sessions, analysis.rel_effort_map(conn))
    prs = analysis.best_efforts_pr(conn)
    stats = analysis.strava_stats(conn)
    fitness = analysis.fitness_freshness(conn)
    delta = analysis.latest_vs_baseline(conn)
    temp = analysis.temp_profile(conn)
    return templates.TemplateResponse(
        request,
        "profile.html",
        {"profile": profile, "races": races, "monthly": monthly, "prs": prs,
         "stats": stats, "fitness": fitness, "delta": delta, "temp": temp,
         "auto_syncing": auto_syncing},
    )


@app.get("/plan")
def plan_view(request: Request):
    ta, plan, goal = _plan_context()
    conn = db.get_connection()
    saved_raw = db.get_user_value(conn, "ai_plan")
    saved_plan = json.loads(saved_raw) if saved_raw else None
    return templates.TemplateResponse(
        request,
        "plan.html",
        {"plan": plan, "analysis": ta, "goal": goal, "saved_plan": saved_plan},
    )


@app.get("/api/coach")
def api_coach(comment: str = ""):
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
    forecast = weather.seoul_forecast(days=7)  # 실제 강수 예보로 비 안 오는 날 배치
    result = coach_llm.generate_plan(
        ta, goal, recent_days, today.isoformat(), rule_plan["phase"],
        user_comment=comment, weather=forecast,
    )
    if "error" in result:
        return JSONResponse({"error": result["error"]})
    payload = {
        "analysis_html": str(render_commentary(result.get("analysis", ""))),
        "focus": result.get("focus", ""),
        "plan": result.get("plan", []),
        "generated": today.isoformat(),
        "goal": goal,
        "comment": comment.strip(),
        "weather": forecast,
    }
    db.set_user_values({"ai_plan": json.dumps(payload, ensure_ascii=False)})
    # GitHub state 브랜치에 영속화(재시작 보존). 실패 시 UI에 경고(전송만, 저장 안 함).
    persisted = state_sync.push_state()
    resp = dict(payload)
    resp["persist_warn"] = (
        "" if persisted or not state_sync.enabled()
        else "⚠️ GitHub 저장 실패 — GITHUB_STATE_TOKEN(repo 스코프)이 없어 재시작 시 이 계획이 사라질 수 있습니다."
    )
    return JSONResponse(resp)


def _progress_days(plan: list, sessions: list[dict]) -> list[dict]:
    """AI 계획의 날짜별로 실제 세션을 짝지어 경과 비교 데이터를 만든다(코드로 확정, LLM 환각 방지)."""
    by_date: dict[str, list] = {}
    for s in sessions:
        if s["date"]:
            by_date.setdefault(s["date"], []).append(
                {
                    "distance_km": s["distance_km"],
                    "pace": s["avg_pace_str"],
                    "avg_hr": s["avg_hr"],
                    "type": s["type"],
                }
            )
    days = []
    for p in plan:
        d = p.get("date") or ""
        days.append(
            {
                "date": d,
                "planned": {"type": p.get("type"), "title": p.get("title"), "detail": p.get("detail")},
                "actual": by_date.get(d, []),
            }
        )
    return days


@app.get("/api/progress")
def api_progress():
    """저장된 AI 계획 대비 실제 세션 경과를 LLM이 평가."""
    from fastapi.responses import JSONResponse

    conn = db.get_connection()
    saved_raw = db.get_user_value(conn, "ai_plan")
    if not saved_raw:
        return JSONResponse({"error": "저장된 AI 계획이 없습니다. 먼저 '다음 훈련 계획'에서 계획을 생성하세요."})
    saved = json.loads(saved_raw)
    plan = saved.get("plan") or []
    if not plan:
        return JSONResponse({"error": "저장된 계획에 세션이 없습니다."})

    sessions = _sessions()
    goal = analysis.resolve_goal(conn)
    today = date.today()
    days = _progress_days(plan, sessions)
    result = coach_llm.plan_progress(days, goal, today.isoformat())
    if "error" in result:
        return JSONResponse({"error": result["error"]})
    payload = {
        "status": result.get("status", ""),
        "summary_html": str(render_commentary(result.get("summary", ""))),
        "advice": result.get("advice", ""),
        "days": days,
        "plan_generated": saved.get("generated"),
        "generated": today.isoformat(),
    }
    db.set_user_values({"plan_progress": json.dumps(payload, ensure_ascii=False)})
    state_sync.push_state()
    return JSONResponse(payload)


@app.get("/api/review")
def api_review():
    """세션 데이터로 기존 훈련 대비 성과 평가 + 앞으로 고려할 것 추천(AI)."""
    from fastapi.responses import JSONResponse

    conn = db.get_connection()
    sessions = _sessions()
    goal = analysis.resolve_goal(conn)
    today = date.today()
    comparison = analysis.period_comparison(sessions, today)
    if comparison["recent"]["sessions"] == 0:
        return JSONResponse({"error": f"최근 {comparison['recent_days']}일 세션이 없어 비교할 수 없습니다."})
    fitness = analysis.fitness_freshness(conn).get("current")
    result = coach_llm.session_review(comparison, fitness, goal, today.isoformat())
    if "error" in result:
        return JSONResponse({"error": result["error"]})
    payload = {
        "verdict": result.get("verdict", ""),
        "headline": result.get("headline", ""),
        "performance_html": str(render_commentary(result.get("performance", ""))),
        "strengths": result.get("strengths") or [],
        "concerns": result.get("concerns") or [],
        "recommendations": result.get("recommendations") or [],
        "comparison": {k: comparison[k] for k in ("recent_days", "base_days", "recent", "baseline", "delta")},
        "generated": today.isoformat(),
    }
    db.set_user_values({"session_review": json.dumps(payload, ensure_ascii=False)})
    state_sync.push_state()
    return JSONResponse(payload)


@app.get("/api/instant")
def api_instant(focus: str = "build_fitness", comment: str = ""):
    from fastapi.responses import JSONResponse

    conn = db.get_connection()
    sessions = _sessions()
    goal = analysis.resolve_goal(conn)
    today = date.today()
    ta = analysis.training_analysis(sessions, goal, today)
    ta["days_since_last_run"] = analysis.days_since_last_run(sessions, today)
    recent_days = analysis.recent_timeline(sessions, today, days=14)
    fitness = analysis.fitness_freshness(conn).get("current")
    result = coach_llm.instant_workout(
        ta, fitness, goal, recent_days, focus, today.isoformat(), user_comment=comment
    )
    return JSONResponse(result)


@app.post("/goal")
def set_goal(
    goal_distance_km: float = Form(...),
    goal_pace_min: int = Form(...),
    goal_pace_sec: int = Form(...),
    goal_date: str = Form(...),
):
    db.set_user_values(
        {
            "goal_distance_km": goal_distance_km,
            "goal_pace_sec": goal_pace_min * 60 + goal_pace_sec,
            "goal_date": goal_date,
            # 병합(newer-wins) 기준 — 다른 기기의 push가 이 목표를 덮지 않게
            "goal_updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    state_sync.push_state()  # GitHub state 브랜치에 영속화(재시작 보존)
    return RedirectResponse(url="/plan", status_code=303)


@app.post("/sync")
def trigger_sync():
    if not SYNC_ENABLED:
        return RedirectResponse(url="/?sync=hosted", status_code=303)
    try:
        n = sync_all()
        return RedirectResponse(url=f"/?sync=ok&n={n}", status_code=303)
    except Exception:
        return RedirectResponse(url="/?sync=error", status_code=303)
