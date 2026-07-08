---
title: Strava Coach
emoji: 🏃
colorFrom: red
colorTo: yellow
sdk: docker
app_port: 7860
pinned: false
---

# Strava Coach

Strava 러닝 데이터를 분석해 심박존 프로필·구간 그래프·3D 경로 재생과
목표 기반 훈련 계획(규칙 + GPT-4o 코칭)을 보여주는 로컬/웹 대시보드.

## 기능
- 세션 목록·대시보드(기간 필터), 활동 상세(1km 스플릿, 랩, 심박·페이스·고도 연동 그래프)
- 3D 위성/일반맵 경로 재생, 심박 프로필, 5k/10k 예상 페이스
- 목표 설정, AI 코치(GPT-4o) 분석 + 7일 훈련 계획(저장·영속)

## 환경변수 (HF Space → Settings → Secrets)
- `GITHUB_TOKEN` — GitHub Models(GPT-4o) 호출용 (AI 코치 기능). 없으면 코치만 비활성, 나머지는 정상.
- `LLM_PROVIDER` — 기본 `github` (Docker에 이미 설정됨).
- (선택) `DATA_DIR` — DB 저장 경로. HF 영구 스토리지 사용 시 `/data`로 지정.

## 로컬 실행
```
pip install -r requirements.txt
python -m strava_coach serve   # http://localhost:8000
```

## 데이터 갱신 (로컬에서만)
```
python -m strava_coach auth            # 최초 Strava OAuth
python -m strava_coach sync            # 새 활동 동기화
python -m strava_coach refetch-streams # latlng 포함 스트림 재수집
python -m strava_coach backfill-hr <apple_health.xml>  # 유실 HR 백필
```
갱신된 `data/strava_coach.db`를 다시 push 하면 Space에 반영됩니다.
