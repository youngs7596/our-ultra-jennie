#!/bin/bash
# test_local.sh
# 로컬 테스트 스크립트
# 작업 LLM: Auto (Claude Sonnet 4.5)

set -e

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)

echo "================================================"
echo "My Supreme Jennie - 로컬 테스트"
echo "================================================"

# 환경 변수 설정
export TRADING_MODE=MOCK
export DRY_RUN=true
export GCP_PROJECT_ID=${GCP_PROJECT_ID:-your-project-id}

echo ""
echo "설정:"
echo "  TRADING_MODE: ${TRADING_MODE}"
echo "  DRY_RUN: ${DRY_RUN}"
echo "  GCP_PROJECT_ID: ${GCP_PROJECT_ID}"
echo ""

# 테스트할 서비스 선택
echo "테스트할 서비스를 선택하세요:"
echo "1. Buy Scanner"
echo "2. Buy Executor"
echo "3. Price Monitor"
echo "4. Sell Executor"
echo "5. 전체 (순차 테스트)"
read -p "선택 (1-5): " choice

case $choice in
    1)
        echo ""
        echo "Buy Scanner 테스트 시작..."
        cd ${REPO_ROOT}/services/buy-scanner
        python3 -m venv .venv || true
        source .venv/bin/activate
        pip install -r requirements.txt
        python main.py
        ;;
    2)
        echo ""
        echo "Buy Executor 테스트 시작..."
        cd ${REPO_ROOT}/services/buy-executor
        python3 -m venv .venv || true
        source .venv/bin/activate
        pip install -r requirements.txt
        python main.py
        ;;
    3)
        echo ""
        echo "Price Monitor 테스트 시작..."
        cd ${REPO_ROOT}/services/price-monitor
        python3 -m venv .venv || true
        source .venv/bin/activate
        pip install -r requirements.txt
        python main.py
        ;;
    4)
        echo ""
        echo "Sell Executor 테스트 시작..."
        cd ${REPO_ROOT}/services/sell-executor
        python3 -m venv .venv || true
        source .venv/bin/activate
        pip install -r requirements.txt
        python main.py
        ;;
    5)
        echo ""
        echo "⚠️  전체 테스트는 각 서비스를 별도 터미널에서 실행해야 합니다."
        echo ""
        echo "터미널 1: Buy Scanner"
        echo "  cd ${REPO_ROOT}/services/buy-scanner && python main.py"
        echo ""
        echo "터미널 2: Buy Executor"
        echo "  cd ${REPO_ROOT}/services/buy-executor && python main.py"
        echo ""
        echo "터미널 3: Price Monitor"
        echo "  cd ${REPO_ROOT}/services/price-monitor && python main.py"
        echo ""
        echo "터미널 4: Sell Executor"
        echo "  cd ${REPO_ROOT}/services/sell-executor && python main.py"
        ;;
    *)
        echo "잘못된 선택입니다."
        exit 1
        ;;
esac

