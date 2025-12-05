# services/news-crawler/Dockerfile
FROM python:3.11-slim-bookworm

WORKDIR /app

# 시스템 패키지 설치 (Oracle Client용)
RUN apt-get update && apt-get install -y \
    wget \
    unzip \
    libaio1 \
    && rm -rf /var/lib/apt/lists/*

# shared 패키지 복사
COPY shared/ /app/shared/
COPY prompts/ /app/prompts/
COPY schemas/ /app/schemas/

# 서비스 코드 복사
COPY services/news-crawler/*.py /app/
COPY services/news-crawler/requirements.txt /app/

# 의존성 설치
RUN pip install --no-cache-dir -r requirements.txt

# 환경 변수
ENV PORT=8080
ENV PYTHONPATH=/app

# Gunicorn 실행 (PORT 환경변수 사용)
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-8080} --timeout 1200 --workers 1 main:app"]

