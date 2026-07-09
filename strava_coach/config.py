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

# LLM 코칭 코멘트 provider. "github"(GitHub Models, GPT-4o) 또는 "anthropic".
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "github")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_MODELS_ENDPOINT = os.environ.get(
    "GITHUB_MODELS_ENDPOINT", "https://models.inference.ai.azure.com"
)
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4o")

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
