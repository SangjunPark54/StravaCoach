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


def pull_state() -> bool:
    """GitHub state 브랜치의 user_state.json을 로컬에 반영. 성공 시 True."""
    if not enabled():
        return False
    try:
        remote, _ = _get_remote()
        if not remote:
            return False
        # 로컬 파일에 병합(원격 우선). 로컬에만 있는 키는 보존.
        local = db._load_user_state()
        local.update(remote)
        db.USER_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        db.USER_STATE_PATH.write_text(
            json.dumps(local, ensure_ascii=False), encoding="utf-8"
        )
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[state_sync] pull 실패: {type(e).__name__}: {e}")
        return False


def push_state() -> bool:
    """로컬 user_state.json을 GitHub state 브랜치에 커밋. 성공 시 True."""
    if not enabled():
        return False
    try:
        state = db._load_user_state()
        if not state:
            return False
        content = json.dumps(state, ensure_ascii=False, indent=2)
        _, sha = _get_remote()  # 현재 sha(업데이트면 필수), 없으면 None(신규 생성)
        payload = {
            "message": "update user_state (goal/ai_plan)",
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
