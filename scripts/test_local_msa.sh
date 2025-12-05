#!/bin/bash
# scripts/test_local_msa.sh
# 로컬에서 MSA 전체 흐름 테스트

set -e

PROJECT_ROOT="/home/youngs75/projects/my-supreme-jennie"
PIDS=()

echo "🚀 로컬 MSA 테스트 환경 시작"
echo "================================================"
echo ""

# 공통 환경 변수 로드
export $(cat infrastructure/env-vars-mock.yaml | grep -v '^#' | grep -v '^$' | sed 's/: /=/g' | xargs)
export PYTHONPATH="$PROJECT_ROOT"

# GCP 인증 확인
echo "🔐 GCP 인증 확인..."
gcloud auth application-default print-access-token > /dev/null 2>&1 || {
  echo "❌ GCP 인증 필요!"
  echo "실행: gcloud auth application-default login"
  exit 1
}
echo "✅ GCP 인증 완료"
echo ""

# Cleanup 함수
cleanup() {
  echo ""
  echo "🛑 모든 프로세스 종료 중..."
  for pid in "${PIDS[@]}"; do
    kill $pid 2>/dev/null || true
  done
  echo "✅ Cleanup 완료"
  exit 0
}

trap cleanup SIGINT SIGTERM

# 1. KIS Gateway 시작
echo "[1/4] KIS Gateway 시작 (포트: 8080)..."
cd $PROJECT_ROOT/services/kis-gateway
export PORT=8080
python3 main.py > /tmp/kis-gateway.log 2>&1 &
PIDS+=($!)
echo "  PID: ${PIDS[0]}"
echo "  로그: tail -f /tmp/kis-gateway.log"
sleep 5
echo ""

# 2. Buy Scanner 시작
echo "[2/4] Buy Scanner 시작 (포트: 8081)..."
cd $PROJECT_ROOT/services/buy-scanner
export PORT=8081
export USE_KIS_GATEWAY=true
export KIS_GATEWAY_URL=http://localhost:8080
export USE_GATEWAY_AUTH=false
python3 main.py > /tmp/buy-scanner.log 2>&1 &
PIDS+=($!)
echo "  PID: ${PIDS[1]}"
echo "  로그: tail -f /tmp/buy-scanner.log"
sleep 5
echo ""

# 3. Buy Executor 시작
echo "[3/4] Buy Executor 시작 (포트: 8082)..."
cd $PROJECT_ROOT/services/buy-executor
export PORT=8082
export USE_KIS_GATEWAY=true
export KIS_GATEWAY_URL=http://localhost:8080
export USE_GATEWAY_AUTH=false
python3 main.py > /tmp/buy-executor.log 2>&1 &
PIDS+=($!)
echo "  PID: ${PIDS[2]}"
echo "  로그: tail -f /tmp/buy-executor.log"
sleep 5
echo ""

# 4. Sell Executor 시작
echo "[4/4] Sell Executor 시작 (포트: 8083)..."
cd $PROJECT_ROOT/services/sell-executor
export PORT=8083
export USE_KIS_GATEWAY=true
export KIS_GATEWAY_URL=http://localhost:8080
export USE_GATEWAY_AUTH=false
python3 main.py > /tmp/sell-executor.log 2>&1 &
PIDS+=($!)
echo "  PID: ${PIDS[3]}"
echo "  로그: tail -f /tmp/sell-executor.log"
sleep 3
echo ""

echo "================================================"
echo "✅ 로컬 MSA 환경 준비 완료!"
echo "================================================"
echo ""
echo "📋 실행 중인 서비스:"
echo "  1. KIS Gateway:    http://localhost:8080/health"
echo "  2. Buy Scanner:    http://localhost:8081/health"
echo "  3. Buy Executor:   http://localhost:8082/health"
echo "  4. Sell Executor:  http://localhost:8083/health"
echo ""
echo "🧪 테스트 명령:"
echo "  # Buy Scanner 실행"
echo "  curl -X POST http://localhost:8081/scan"
echo ""
echo "  # Gateway Stats 확인"
echo "  curl http://localhost:8080/stats | jq ."
echo ""
echo "  # 로그 실시간 확인"
echo "  tail -f /tmp/kis-gateway.log"
echo "  tail -f /tmp/buy-scanner.log"
echo ""
echo "⚠️  종료: Ctrl+C"
echo ""

# 대기
wait

