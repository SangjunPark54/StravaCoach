FROM python:3.11-slim

WORKDIR /app

# HF Spaces는 uid 1000 사용자로 실행 → DB 쓰기 가능하도록 소유권 부여
RUN useradd -m -u 1000 user

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=user:user . /app

USER user
ENV HOST=0.0.0.0 \
    PORT=7860 \
    PYTHONUNBUFFERED=1 \
    LLM_PROVIDER=github

EXPOSE 7860
# uvicorn을 직접 실행(__main__의 truststore 주입/포트 env 우회 → HF에서 안정적)
CMD ["uvicorn", "strava_coach.web.app:app", "--host", "0.0.0.0", "--port", "7860"]
