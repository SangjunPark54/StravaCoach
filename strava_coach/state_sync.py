"""사용자 상태(user_state.json)를 GitHub repo 브랜치에 영속화.

목적: HF Spaces는 영구 스토리지가 없어 컨테이너 재시작 시 런타임에 쓴 파일이
사라진다. 목표·AI계획을 GitHub(정본)에 두고, 기동 시 pull / 저장 시 push 하여
재시작에도 보존하고, 로컬 DB push로 롤백되지 않게 한다.

- pull_state(): 기동 시 GitHub state 브랜치의 파일 → 로컬 user_state.json 반영.
- push_state(): 목표/계획 저장 후 로컬 파일 → GitHub 커밋(commit).

네트워크·토큰 문제로 실패해도 앱이 죽지 않도록 모든 예외를 삼킨다(로그만).
"""

import base64
import json

import httpx

from . import db
from .config import GITHUB_STATE_TOKEN, STATE_BRANCH, STATE_FILE, STATE_REPO

_API = "https://api.github.com"


def enabled() -> bool:
    return bool(GITHUB_STATE_TOKEN and STATE_REPO and STATE_FILE)


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {GITHUB_STATE_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _contents_url() -> str:
    return f"{_API}/repos/{STATE_REPO}/contents/{STATE_FILE}"


def _get_remote() -> tuple[dict | None, str | None]:
    """(state dict, file sha) 반환. 없으면 (None, None)."""
    r = httpx.get(_contents_url(), headers=_headers(),
                  params={"ref": STATE_BRANCH}, timeout=20)
    if r.status_code == 404:
        return None, None
    r.raise_for_status()
    j = r.json()
    raw = base64.b64decode(j["content"]).decode("utf-8")
    try:
        return json.loads(raw), j["sha"]
    except json.JSONDecodeError:
        return None, j["sha"]


def _json_ts(state: dict, key: str, field: str = "generated") -> str:
    """user_state[key](JSON 문자열)의 타임스탬프 필드 추출. 없으면 ''."""
    raw = (state or {}).get(key)
    if not raw:
        return ""
    try:
        return json.loads(raw).get(field, "") or ""
    except Exception:  # noqa: BLE001
        return ""


_GOAL_KEYS = ("goal_distance_km", "goal_pace_sec", "goal_date", "goal_updated_at")


def _merge(remote: dict, local: dict) -> dict:
    """원격/로컬 user_state를 newer-wins로 병합.

    - ai_plan / plan_progress: 내부 generated 날짜가 더 최신인 쪽.
    - goal_*: goal_updated_at이 더 최신인 쪽(HF에서 바꾼 목표를 로컬 push가 덮지 않게).
    - 그 외 키: 로컬 우선, 없으면 원격.
    """
    remote = remote or {}
    local = local or {}
    merged = dict(remote)
    merged.update(local)
    for key in ("ai_plan", "plan_progress"):
        if remote.get(key) and _json_ts(remote, key) > _json_ts(local, key):
            merged[key] = remote[key]
    if remote.get("goal_updated_at", "") > local.get("goal_updated_at", ""):
        for k in _GOAL_KEYS:
            if k in remote:
                merged[k] = remote[k]
    return merged


def pull_state() -> bool:
    """GitHub state 브랜치의 user_state.json을 로컬에 병합 반영(newer-wins). 성공 시 True."""
    if not enabled():
        return False
    try:
        remote, _ = _get_remote()
        if not remote:
            return False
        merged = _merge(remote, db._load_user_state())
        db.USER_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        db.USER_STATE_PATH.write_text(
            json.dumps(merged, ensure_ascii=False), encoding="utf-8"
        )
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[state_sync] pull 실패: {type(e).__name__}: {e}")
        return False


def push_state() -> bool:
    """로컬 user_state를 원격과 병합(newer-wins) 후 GitHub state 브랜치에 커밋. 성공 시 True.

    병합 없이 로컬을 통째로 밀면 다른 곳(HF/로컬)에서 저장한 최신 값을 덮어버리므로,
    반드시 원격을 먼저 읽어 병합한 결과를 push하고 로컬에도 반영한다.
    """
    if not enabled():
        return False
    try:
        local = db._load_user_state()
        if not local:
            return False
        remote, sha = _get_remote()  # sha: 업데이트면 필수, 없으면 None(신규 생성)
        merged = _merge(remote, local)
        db.USER_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        db.USER_STATE_PATH.write_text(
            json.dumps(merged, ensure_ascii=False), encoding="utf-8"
        )
        content = json.dumps(merged, ensure_ascii=False, indent=2)
        payload = {
            "message": "update user_state (goal/ai_plan/progress)",
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "branch": STATE_BRANCH,
        }
        if sha:
            payload["sha"] = sha
        r = httpx.put(_contents_url(), headers=_headers(), json=payload, timeout=20)
        r.raise_for_status()
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[state_sync] push 실패: {type(e).__name__}: {e}")
        return False
