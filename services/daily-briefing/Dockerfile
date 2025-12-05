# Dockerfile
# Daily Briefing Service

FROM python:3.11-slim

# 작업 디렉토리
WORKDIR /app

# Oracle Instant Client (oracledb 필요)
RUN apt-get update && apt-get install -y \
    wget \
    unzip \
    libaio1t64 \
    && rm -rf /var/lib/apt/lists/*

# shared 패키지 복사
COPY shared/ /app/shared/
COPY prompts/ /app/prompts/
COPY schemas/ /app/schemas/

# 서비스 코드 복사
COPY services/daily-briefing/*.py services/daily-briefing/requirements.txt /app/

# 의존성 설치
RUN pip install --no-cache-dir -r requirements.txt

# 환경 변수
ENV PORT=8080
ENV PYTHONPATH=/app

# Gunicorn 실행 (PORT 환경변수 사용)
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-8080} --timeout 0 --workers 1 main:app"]
