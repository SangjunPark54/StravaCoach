"""1회용 Copilot 디바이스 로그인: gho_ 토큰을 발급해 .env의 GITHUB_COPILOT_TOKEN에 저장.

사용: python -m strava_coach.copilot_login
"""

import re

from .config import PROJECT_ROOT
from .copilot_auth import device_login, get_copilot_bearer


def main() -> None:
    token = device_login()
    get_copilot_bearer(token)  # Copilot 구독 확인을 겸한 교환 테스트
    env_path = PROJECT_ROOT / ".env"
    text = env_path.read_text() if env_path.exists() else ""
    if re.search(r"^GITHUB_COPILOT_TOKEN=", text, flags=re.M):
        text = re.sub(
            r"^GITHUB_COPILOT_TOKEN=.*$", f"GITHUB_COPILOT_TOKEN={token}", text, flags=re.M
        )
    else:
        text = text.rstrip("\n") + f"\nGITHUB_COPILOT_TOKEN={token}\n"
    env_path.write_text(text)
    print("완료 — .env의 GITHUB_COPILOT_TOKEN에 저장했고 Copilot 토큰 교환도 성공했습니다.")


if __name__ == "__main__":
    main()
