# Dashboard V2 Backend - FastAPI
FROM python:3.11-slim

WORKDIR /app

# 시스템 의존성 설치
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Python 의존성 설치
COPY services/dashboard-v2/backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# shared 라이브러리 복사
COPY shared /app/shared

# 애플리케이션 코드 복사
COPY services/dashboard-v2/backend/*.py /app/

# Oracle Wallet 복사 (필요 시)
COPY wallet /app/wallet

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

EXPOSE 8090

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8090"]

