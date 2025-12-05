#!/bin/bash
# test_service.sh
# 개별 서비스 테스트 스크립트
# 작업 LLM: Auto (Claude Sonnet 4.5)

set -e

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)

# 사용법
usage() {
    echo "사용법: $0 <service_name>"
    echo ""
    echo "서비스:"
    echo "  buy-scanner       - Buy Scanner 서비스"
    echo "  buy-executor      - Buy Executor 서비스"
    echo "  price-monitor     - Price Monitor 서비스"
    echo "  sell-executor     - Sell Executor 서비스"
    echo "  rag-cacher        - RAG Cacher 서비스"
    echo "  command-handler   - Command Handler 서비스"
    echo ""
    exit 1
}

# 인자 확인
if [ $# -ne 1 ]; then
    usage
fi

SERVICE_NAME=$1
SERVICE_DIR="$REPO_ROOT/services/$SERVICE_NAME"

# 서비스 디렉토리 확인
if [ ! -d "$SERVICE_DIR" ]; then
    echo "❌ 서비스를 찾을 수 없습니다: $SERVICE_NAME"
    usage
fi

echo "================================================"
echo "서비스 테스트: $SERVICE_NAME"
echo "================================================"

# .env 파일 로드
if [ -f "$REPO_ROOT/.env" ]; then
    echo "✅ 환경 변수 로드 중..."
    export $(cat "$REPO_ROOT/.env" | grep -v '^#' | sed 's/#.*//' | xargs)
else
    echo "⚠️  .env 파일이 없습니다. 기본값을 사용합니다."
fi

# 포트 설정
case $SERVICE_NAME in
    buy-scanner)
        PORT=${BUY_SCANNER_PORT:-8080}
        ;;
    buy-executor)
        PORT=${BUY_EXECUTOR_PORT:-8081}
        ;;
    price-monitor)
        PORT=${PRICE_MONITOR_PORT:-8082}
        ;;
    sell-executor)
        PORT=${SELL_EXECUTOR_PORT:-8083}
        ;;
    rag-cacher)
        PORT=${RAG_CACHER_PORT:-8084}
        ;;
    command-handler)
        PORT=${COMMAND_HANDLER_PORT:-8085}
        ;;
    *)
        PORT=8080
        ;;
esac

export PORT

echo ""
echo "설정:"
echo "  서비스: $SERVICE_NAME"
echo "  포트: $PORT"
echo "  TRADING_MODE: ${TRADING_MODE:-MOCK}"
echo "  DRY_RUN: ${DRY_RUN:-true}"
echo ""

# 가상 환경 확인 및 생성
cd "$SERVICE_DIR"

if [ ! -d ".venv" ]; then
    echo "📦 가상 환경 생성 중..."
    python3 -m venv .venv
fi

echo "✅ 가상 환경 활성화..."
source .venv/bin/activate

echo "📦 의존성 설치 중..."
pip install -q -r requirements.txt

echo ""
echo "🚀 서비스 시작 중..."
echo "   종료하려면 Ctrl+C를 누르세요."
echo ""

python main.py

