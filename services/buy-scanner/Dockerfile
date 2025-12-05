# Dockerfile
# Buy Scanner Service

FROM python:3.11-slim

# 작업 디렉토리
WORKDIR /app

# Oracle Instant Client 및 Rust 빌드 도구 설치
RUN apt-get update && apt-get install -y \
    wget \
    unzip \
    libaio1t64 \
    build-essential \
    pkg-config \
    libssl-dev \
    curl \
    cargo \
    && rm -rf /var/lib/apt/lists/*

# shared / configs / build script 복사
COPY shared/ /app/shared/
COPY prompts/ /app/prompts/
COPY schemas/ /app/schemas/
COPY strategy/ /app/strategy/
COPY configs/ /app/configs/
COPY scripts/build_strategy_core.sh /app/scripts/build_strategy_core.sh
RUN chmod +x /app/scripts/build_strategy_core.sh && /app/scripts/build_strategy_core.sh

# 서비스 의존성 설치
COPY services/buy-scanner/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# 서비스 코드 복사
COPY services/buy-scanner/*.py /app/

# 환경 변수
ENV PORT=8080
ENV PYTHONPATH=/app

# Gunicorn 실행 (로컬 테스트: worker 1개, DB Pool 경합 방지)
# Cloud Run은 PORT 환경변수를 자동으로 주입함
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT} --timeout 600 --workers 1 main:app"]

