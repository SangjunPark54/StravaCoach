# Strava Coach — 온보딩 가이드

처음부터 끝까지: **① Strava API 셋팅 → ② GitHub Models(GPT-4o) 토큰 → ③ Hugging Face Spaces 배포**.

> 이 문서 하나만 따라가면 새 환경/새 계정에서도 앱을 띄울 수 있습니다.

---

## 0. 사전 준비

- Python 3.11+
- git, (배포 시) git-lfs
- Strava 계정 / GitHub 계정 / Hugging Face 계정

```bash
cd strava_coach
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # 여기에 아래에서 발급받는 값들을 채웁니다
```

`.env`는 **절대 git/HF에 올리지 않습니다** (`.gitignore`로 제외됨).

---

## 1. Strava API 셋팅

Strava는 "발급 신청/심사"가 없습니다. 본인 계정에서 앱을 등록하면 즉시 키가 나옵니다.

### 1-1. 앱 등록
1. https://www.strava.com/settings/api 접속 (로그인 상태)
2. 폼 작성:

   | 항목 | 값 |
   |------|-----|
   | Application Name | 아무거나 (예: `MyRunningCoach`) |
   | Category | `Data Importer` 등 아무거나 |
   | Website | 아무 URL (예: `http://localhost`) |
   | **Authorization Callback Domain** | **`localhost`** ⭐ (앞에 `http://` 붙이지 말고 도메인만) |

3. **Create** → **Client ID**와 **Client Secret**이 즉시 발급됩니다.

### 1-2. `.env`에 입력
```
STRAVA_CLIENT_ID=<발급받은 Client ID>
STRAVA_CLIENT_SECRET=<발급받은 Client Secret>
```

### 1-3. OAuth 인증 (토큰 받기)
> Strava가 처음 화면에서 주는 Access Token은 권한이 `read`뿐이라 **활동 데이터를 못 읽습니다.**
> 아래 `auth` 명령이 `activity:read_all` 권한으로 자동 인증합니다.

```bash
python -m strava_coach auth
```
- 브라우저가 열리면 **Authorize** 클릭 → `data/strava_tokens.json`에 토큰 저장.
- Callback Domain이 `localhost`가 아니면 여기서 에러가 납니다 (1-1 확인).

### 1-4. 데이터 동기화
```bash
python -m strava_coach sync              # 러닝 활동 동기화 (type=Run만)
python -m strava_coach refetch-streams   # latlng 포함 스트림 재수집 (지도/재생용)
```

### 1-5. (선택) Apple Health HR 백필
Strava가 Apple Health 활동을 임포트할 때 **심박(HR)을 유실**하는 경우가 있습니다.
Apple Health 내보내기 XML이 있으면 유실된 HR을 복구합니다:
```bash
python -m strava_coach backfill-hr /path/to/내보내기.xml
```

### ⚠️ 회사 노트북(프록시) SSL 오류
`CERTIFICATE_VERIFY_FAILED: self-signed certificate` 가 뜨면, 회사 TLS 검사 프록시 때문입니다.
→ 이미 `truststore`로 macOS 키체인을 쓰도록 처리돼 있어 `python -m strava_coach`(=`__main__`)로 실행하면 해결됩니다.

---

## 2. GitHub Models (GPT-4o) 토큰 — AI 코치용

AI 코치(분석·훈련계획)는 **GitHub Models**의 GPT-4o를 씁니다 (OpenAI 호환 API, GitHub PAT로 무료 티어 사용).

### 2-1. PAT 발급
1. https://github.com/settings/tokens 접속
2. **Generate new token** → 권한(scope)에 **`models:read`** 포함 (Fine-grained면 "Models" 권한 허용)
3. 생성된 `ghp_...` 토큰 복사

### 2-2. `.env`에 입력
```
LLM_PROVIDER=github
GITHUB_TOKEN=<발급받은 ghp_ 토큰>
# ANTHROPIC_API_KEY=   # provider=anthropic로 바꿀 때만 필요
```

### 2-3. 동작 방식 / 참고
- 엔드포인트: `https://models.inference.ai.azure.com/chat/completions`, 모델 `gpt-4o`
  (config의 `GITHUB_MODELS_ENDPOINT` / `LLM_MODEL`로 변경 가능)
- **회사망 주의**: 신규 엔드포인트 `models.github.ai/inference`는 회사 SSL에서 막힐 수 있어 위 Azure 레거시 엔드포인트를 기본값으로 씀.
- 토큰이 없으면 AI 코치 버튼만 비활성화되고 **나머지 기능은 정상** 동작합니다.

---

## 3. Hugging Face Spaces 배포 (Docker)

FastAPI 앱이라 **Docker Space**로 배포합니다. (이미 `Dockerfile`, `README.md`(HF 설정), `requirements.txt`, `.dockerignore` 준비돼 있음)

### 3-1. Space 생성
1. https://huggingface.co/new-space
2. **SDK: Docker**, Visibility: **Private 권장** (⚠️ GPS 경로·집 위치·심박이 공개될 수 있음)

### 3-2. HF 쓰기 토큰 + 로그인
1. https://huggingface.co/settings/tokens → **New token (Type: Write)**
2. 로그인 (토큰은 화면에 안 남게):
   ```bash
   pip install -U "huggingface_hub[cli]"
   hf auth login --token <HF_WRITE_TOKEN> --add-to-git-credential
   ```
   > 대화형 `hf auth login`은 TTY 없는 환경에서 실패하므로 `--token` 방식 사용.

### 3-3. git + LFS 준비
> **HF는 바이너리 파일(DB)을 일반 git으로 거부합니다 → git-lfs 필수.**
```bash
# git-lfs 설치 (mac: brew install git-lfs)
git init && git branch -M main
git add -A && git commit -m "Deploy Strava Coach"
git lfs install
git lfs migrate import --include="data/strava_coach.db" --include-ref=refs/heads/main --yes
git remote add space https://huggingface.co/spaces/<USER>/<SPACE_NAME>
```

### 3-4. Push
```bash
git push space main --force   # 초기 커밋 덮어쓰기용 --force
```
push하면 HF가 `Dockerfile`로 자동 빌드(2~3분) 후 앱이 뜹니다.

### 3-5. Space 시크릿 설정
Space → **Settings → Variables and secrets**:
- `GITHUB_TOKEN` = 위 2-1의 GitHub PAT (AI 코치용)
- (선택) `DATA_DIR=/data` — HF Persistent Storage 사용 시 (목표·AI계획 영구 저장)

### 앱 주소
- Space 페이지: `https://huggingface.co/spaces/<USER>/<SPACE_NAME>`
- 앱 직접: `https://<user>-<space-name>.hf.space` (소문자, Private면 로그인된 브라우저에서만)

### ⚠️ 자주 걸리는 것
| 증상 | 원인/해결 |
|------|-----------|
| `push rejected ... binary files` | DB를 git-lfs로 (3-3의 migrate) |
| `colorFrom must be one of [red,yellow,...]` | `README.md` frontmatter의 색은 지정된 값만 (orange ❌) |
| 앱 URL 404 | 빌드 중이거나 Private Space (로그인 필요) |
| AI 코치 에러 | Space에 `GITHUB_TOKEN` 시크릿 누락 |
| 목표/계획이 재시작 후 사라짐 | 구운 DB로 리셋됨 → Persistent Storage + `DATA_DIR=/data` |

---

## 4. 데이터 갱신 후 재배포

로컬에서 새 데이터를 받은 뒤 Space에 반영:
```bash
python -m strava_coach sync
python -m strava_coach refetch-streams
python -m strava_coach backfill-hr /path/to/내보내기.xml   # HR 유실 시
git add data/strava_coach.db && git commit -m "Update data"
git push space main
```

---

## 보안 체크리스트 🔒
- [ ] `.env`, `data/strava_tokens.json`은 git/HF에 **절대 미포함** (`.gitignore` 확인)
- [ ] 토큰을 채팅·이슈·커밋 메시지에 노출했다면 **즉시 폐기 후 재발급**
  - Strava Secret: https://www.strava.com/settings/api
  - GitHub PAT: https://github.com/settings/tokens
  - HF Token: https://huggingface.co/settings/tokens
- [ ] HF Space는 **Private** (개인 러닝/위치 데이터 보호)
