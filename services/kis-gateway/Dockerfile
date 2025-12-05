# services/kis-gateway/Dockerfile
FROM python:3.11-slim-bookworm

# 작업 디렉토리 설정
WORKDIR /app

# Oracle Instant Client 의존성 설치
RUN apt-get update && apt-get install -y \
    libaio1 \
    && rm -rf /var/lib/apt/lists/*

# 의존성 파일 복사 및 설치
COPY services/kis-gateway/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# shared 모듈 복사
COPY shared/ /app/shared/

# kis-gateway 서비스 코드 복사
COPY services/kis-gateway/ /app/kis-gateway/

# Python 경로 설정
ENV PYTHONPATH=/app

# 포트 설정
EXPOSE 8080

# Gunicorn으로 실행
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "1", "--timeout", "0", "--chdir", "/app/kis-gateway", "main:app"]

