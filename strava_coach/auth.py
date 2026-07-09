import http.server
import json
import os
import socketserver
import time
import urllib.parse
import webbrowser

import httpx

from .config import DATA_DIR

TOKEN_FILE = DATA_DIR / "strava_tokens.json"
AUTH_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"
CALLBACK_PORT = 8722
REDIRECT_URI = f"http://localhost:{CALLBACK_PORT}/callback"
SCOPES = "activity:read_all"


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    code = None

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/callback":
            params = urllib.parse.parse_qs(parsed.query)
            _CallbackHandler.code = params.get("code", [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<h1>인증 완료. 이 창은 닫아도 됩니다.</h1>".encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


def _wait_for_code() -> str:
    with socketserver.TCPServer(("localhost", CALLBACK_PORT), _CallbackHandler) as httpd:
        while _CallbackHandler.code is None:
            httpd.handle_request()
        return _CallbackHandler.code


def authorize(client_id: str, client_secret: str) -> dict:
    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "approval_prompt": "auto",
        "scope": SCOPES,
    }
    url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"
    print(f"브라우저에서 인증하세요: {url}")
    webbrowser.open(url)
    code = _wait_for_code()

    resp = httpx.post(
        TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
        },
    )
    resp.raise_for_status()
    tokens = resp.json()
    save_tokens(tokens)
    return tokens


def save_tokens(tokens: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(tokens, indent=2))


def load_tokens() -> dict:
    if TOKEN_FILE.exists():
        return json.loads(TOKEN_FILE.read_text())
    # 토큰 파일이 없으면(HF 등 호스팅) 환경변수의 refresh token으로 시드 → 즉시 갱신됨
    env_rt = os.environ.get("STRAVA_REFRESH_TOKEN")
    if env_rt:
        return {"refresh_token": env_rt, "expires_at": 0}
    raise RuntimeError("인증 토큰이 없습니다. `python -m strava_coach auth`를 먼저 실행하세요.")


def refresh_if_needed(client_id: str, client_secret: str) -> dict:
    tokens = load_tokens()
    if tokens["expires_at"] > time.time() + 60:
        return tokens

    resp = httpx.post(
        TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
        },
    )
    resp.raise_for_status()
    new_tokens = resp.json()
    save_tokens(new_tokens)
    return new_tokens
