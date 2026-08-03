"""GitHub Copilot 인증.

GitHub Models 종료(2026-07-30) 이후 Copilot 구독(무료 플랜 포함)으로 모델을 호출하기 위한 경로.
- 1회: OAuth 디바이스 플로우로 gho_ 토큰 발급 (`python -m strava_coach.copilot_login`)
- 매 호출: gho_ 토큰을 단기 Copilot bearer 토큰으로 교환(만료 전까지 캐시)
"""

import time

import httpx

# GitHub Copilot 플러그인의 OAuth 클라이언트 ID — Copilot 접근 권한이 부여된 공개 클라이언트.
VSCODE_CLIENT_ID = "Iv1.b507a08c87ecfe98"

_EDITOR_HEADERS = {
    "Editor-Version": "vscode/1.99.0",
    "Editor-Plugin-Version": "copilot-chat/0.26.0",
    "User-Agent": "GitHubCopilotChat/0.26.0",
    "Copilot-Integration-Id": "vscode-chat",
}

_cached_bearer: dict = {"token": "", "expires_at": 0}


def device_login() -> str:
    """디바이스 플로우로 gho_ OAuth 토큰 발급. 사용자에게 코드/URL을 안내하고 완료까지 폴링."""
    resp = httpx.post(
        "https://github.com/login/device/code",
        data={"client_id": VSCODE_CLIENT_ID, "scope": "read:user"},
        headers={"Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    d = resp.json()
    print(f"\n브라우저에서 열기:  {d['verification_uri']}")
    print(f"입력할 코드:      {d['user_code']}\n")
    interval = int(d.get("interval", 5))
    deadline = time.time() + int(d.get("expires_in", 900))
    while time.time() < deadline:
        time.sleep(interval)
        tok = httpx.post(
            "https://github.com/login/oauth/access_token",
            data={
                "client_id": VSCODE_CLIENT_ID,
                "device_code": d["device_code"],
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
            headers={"Accept": "application/json"},
            timeout=30,
        ).json()
        if tok.get("access_token"):
            return tok["access_token"]
        err = tok.get("error", "")
        if err == "authorization_pending":
            continue
        if err == "slow_down":
            interval += 5
            continue
        raise RuntimeError(f"디바이스 로그인 실패: {tok}")
    raise TimeoutError("디바이스 로그인 시간 초과 — 다시 실행하세요.")


def get_copilot_bearer(oauth_token: str) -> str:
    """gho_ 토큰 → 단기 Copilot bearer 토큰 교환(만료 60초 전까지 캐시)."""
    if _cached_bearer["token"] and time.time() < _cached_bearer["expires_at"] - 60:
        return _cached_bearer["token"]
    resp = httpx.get(
        "https://api.github.com/copilot_internal/v2/token",
        headers={"Authorization": f"token {oauth_token}", **_EDITOR_HEADERS},
        timeout=30,
    )
    if resp.status_code == 404:
        raise RuntimeError(
            "Copilot 토큰 교환 실패(404) — 이 GitHub 계정에 Copilot 구독(무료 플랜 포함)이 없거나 "
            "토큰이 디바이스 로그인(gho_)으로 발급된 것이 아닙니다. "
            "`python -m strava_coach.copilot_login`을 다시 실행하세요."
        )
    resp.raise_for_status()
    d = resp.json()
    _cached_bearer["token"] = d["token"]
    _cached_bearer["expires_at"] = int(d.get("expires_at", time.time() + 1500))
    return d["token"]


def chat_headers(oauth_token: str) -> dict:
    return {
        "Authorization": f"Bearer {get_copilot_bearer(oauth_token)}",
        "Content-Type": "application/json",
        **_EDITOR_HEADERS,
    }
