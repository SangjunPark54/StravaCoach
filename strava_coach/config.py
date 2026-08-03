import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env")

# DATA_DIR은 환경변수로 재정의 가능(예: HF Spaces 영구 스토리지 /data)
DATA_DIR = Path(os.environ.get("DATA_DIR") or (PROJECT_ROOT / "data"))

STRAVA_CLIENT_ID = os.environ.get("STRAVA_CLIENT_ID", "")
STRAVA_CLIENT_SECRET = os.environ.get("STRAVA_CLIENT_SECRET", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# LLM 코칭 provider. "copilot"(기본) / "anthropic" / 그 외(OpenAI 호환 chat/completions).
# (구) "github"(GitHub Models)는 2026-07-30 서비스 종료 → Copilot 구독(무료 플랜 포함)으로 대체.
# copilot: 1회 `python -m strava_coach.copilot_login` 실행으로 GITHUB_COPILOT_TOKEN(gho_) 발급.
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "copilot")
GITHUB_COPILOT_TOKEN = os.environ.get("GITHUB_COPILOT_TOKEN", "")
COPILOT_API_BASE = os.environ.get("COPILOT_API_BASE", "https://api.githubcopilot.com")
# copilot 외 OpenAI 호환 프로바이더용(예: Gemini/OpenAI/OpenRouter)
LLM_API_BASE = os.environ.get("LLM_API_BASE", "")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4.1")

# ── 사용자 상태(목표·AI계획) GitHub 영속화 ──────────────────────────
# user_state.json을 GitHub repo의 별도 브랜치에 저장 → HF 재시작에도 보존.
# 앱 기동 시 pull, 목표/계획 저장 시 push. 토큰은 repo(contents write) 스코프 필요.
STATE_REPO = os.environ.get("STATE_REPO", "SangjunPark54/StravaCoach")
STATE_BRANCH = os.environ.get("STATE_BRANCH", "state")
STATE_FILE = os.environ.get("STATE_FILE", "user_state.json")
# 상태 저장 전용 토큰(repo 스코프 classic PAT). GITHUB_TOKEN(Copilot/모델 전용)과 반드시 별개.
# GITHUB_CLASSIC_TOKEN 우선, (하위호환) GITHUB_STATE_TOKEN도 허용. GITHUB_TOKEN으로는 fallback하지 않음.
GITHUB_STATE_TOKEN = (
    os.environ.get("GITHUB_CLASSIC_TOKEN", "")
    or os.environ.get("GITHUB_STATE_TOKEN", "")
)

HR_MAX = 193
# 폴백 전용. 실제로는 Strava /zones에서 받아온 존을 우선 사용(analysis.resolve_hr_zones).
HR_ZONES_FALLBACK = {
    "Z1": (0, 116),
    "Z2": (116, 135),
    "Z3": (135, 154),
    "Z4": (154, 174),
    "Z5": (174, 999),
}
HR_ZONES = HR_ZONES_FALLBACK

GOAL_DISTANCE_KM = 10.0
GOAL_PACE_SEC_PER_KM = 300  # 5'00"/km
GOAL_DATE = date(2026, 10, 8)

WEEKLY_TEMPLATE = {
    "vo2": {"desc": "400m x 8, 회복 90초 조깅 (HR Z5)"},
    "threshold": {"desc": "1km x 4~5 @ 5'00~5'10/km, 회복 60~90초 (HR Z4)"},
    "tempo": {"desc": "4~5km 연속 @ 5'30~5'45/km (HR Z4)"},
    "long_run": {"desc": "장거리 주행, 막판 2~3km 페이스업"},
    "easy": {"desc": "이지런, HR 135 이하"},
}
