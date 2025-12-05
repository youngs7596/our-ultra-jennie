#!/bin/bash
# scripts/test_local_docker.sh
# Docker Compose 로컬 MSA 테스트

set -e

PROJECT_ROOT="/home/youngs75/projects/my-supreme-jennie"
cd $PROJECT_ROOT

echo "🐳 Docker Compose 로컬 MSA 테스트"
echo "================================================"
echo ""

# Cleanup 함수
cleanup() {
  echo ""
  echo "🛑 Docker Compose 종료 중..."
  docker compose down
  echo "✅ Cleanup 완료"
  exit 0
}

trap cleanup SIGINT SIGTERM

# 1. Docker Compose 시작
echo "[1] Docker Compose 시작..."
docker compose up -d

echo ""
echo "⏳ 서비스 초기화 대기 (30초)..."
sleep 30

echo ""
echo "[2] 서비스 상태 확인"
echo "----------------------------------------"
docker compose ps

echo ""
echo "[3] Health Check"
echo "----------------------------------------"

# KIS Mock
echo "▶ KIS Mock Server:"
curl -s http://localhost:9443/health | jq . || echo "  ❌ 실패"

# KIS Gateway
echo "▶ KIS Gateway:"
curl -s http://localhost:8080/health | jq . || echo "  ❌ 실패"

# Buy Scanner
echo "▶ Buy Scanner:"
curl -s http://localhost:8081/health | jq . || echo "  ❌ 실패"

# Buy Executor
echo "▶ Buy Executor:"
curl -s http://localhost:8082/health | jq . || echo "  ❌ 실패"

# Sell Executor
echo "▶ Sell Executor:"
curl -s http://localhost:8083/health | jq . || echo "  ❌ 실패"

echo ""
echo "[4] E2E 테스트 시작"
echo "----------------------------------------"

# Gateway 직접 호출 (삼성전자)
echo "▶ Test 1: Gateway로 삼성전자 조회..."
curl -s -X POST \
  -H "Content-Type: application/json" \
  -d '{"stock_code": "005930"}' \
  http://localhost:8080/api/market-data/snapshot | jq '{success, price: .data.price}'

sleep 2

# Buy Scanner 실행
echo ""
echo "▶ Test 2: Buy Scanner 실행..."
curl -s -X POST http://localhost:8081/scan | jq .

sleep 2

# Gateway Stats
echo ""
echo "▶ Test 3: Gateway Stats 확인..."
curl -s http://localhost:8080/stats | jq '{requests, circuit_breaker}'

echo ""
echo "================================================"
echo "✅ 로컬 테스트 완료!"
echo "================================================"
echo ""
echo "📋 서비스 로그 확인:"
echo "  docker compose logs kis-gateway"
echo "  docker compose logs buy-scanner"
echo "  docker compose logs kis-mock"
echo ""
echo "🛑 종료: Ctrl+C 또는 docker compose down"
echo ""

# 로그 실시간 확인 (선택)
read -p "로그를 실시간으로 확인하시겠습니까? (y/n): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
  docker compose logs -f
else
  cleanup
fi
