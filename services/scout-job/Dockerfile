# Scout Job Service Dockerfile
FROM python:3.11-slim-bookworm

WORKDIR /app

# Install system dependencies for Oracle client + Rust build
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    libaio1 \
    build-essential \
    pkg-config \
    libssl-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install latest Rust toolchain (cargo) via rustup
RUN curl https://sh.rustup.rs -sSf | sh -s -- -y && \
    . "$HOME/.cargo/env" && \
    rustup show
ENV PATH="/root/.cargo/bin:${PATH}"

# Copy requirements and install Python dependencies
COPY services/scout-job/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared modules & resources
COPY shared /app/shared
COPY prompts /app/prompts
COPY schemas /app/schemas

# Copy build script for Rust strategy_core
COPY scripts/build_strategy_core.sh /app/scripts/build_strategy_core.sh
RUN chmod +x /app/scripts/build_strategy_core.sh && /app/scripts/build_strategy_core.sh

# Copy service code
COPY services/scout-job /app/services/scout-job

# Copy utilities (for Backtester)
COPY utilities /app/utilities

# Set PYTHONPATH
ENV PYTHONPATH=/app

# Set working directory to service folder
WORKDIR /app/services/scout-job

# Run with gunicorn (PORT 환경변수 사용)
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-8080} --workers 1 --timeout 1800 --access-logfile - --error-logfile - main:app"]

