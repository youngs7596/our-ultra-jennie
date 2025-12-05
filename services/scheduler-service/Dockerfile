# Scheduler Service Dockerfile
FROM python:3.11-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy shared libs
COPY shared/ /app/shared/

# Copy service code
COPY services/scheduler-service/ /app/

# Install deps
RUN pip install --no-cache-dir -r requirements.txt

ENV PORT=8095
ENV PYTHONPATH=/app

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8095"]

