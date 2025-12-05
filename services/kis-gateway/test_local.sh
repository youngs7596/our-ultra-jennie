#!/bin/bash
# services/kis-gateway/test_local.sh
# KIS Gateway 로컬 테스트 스크립트

set -e

BASE_URL="http://localhost:8080"

echo "🧪 KIS Gateway 로컬 테스트 시작..."
echo "📍 URL: $BASE_URL"
echo ""

# 1. Health Check
echo "1️⃣ Health Check 테스트"
echo "GET $BASE_URL/health"
curl -s "$BASE_URL/health" | jq .
echo ""
echo ""

# 2. Stats 조회
echo "2️⃣ Stats 조회 테스트"
echo "GET $BASE_URL/stats"
curl -s "$BASE_URL/stats" | jq .
echo ""
echo ""

# 3. 주식 현재가 조회 (삼성전자)
echo "3️⃣ 주식 현재가 조회 테스트 (삼성전자: 005930)"
echo "POST $BASE_URL/api/market-data/snapshot"
curl -s -X POST "$BASE_URL/api/market-data/snapshot" \
  -H "Content-Type: application/json" \
  -d '{"stock_code": "005930"}' | jq .
echo ""
echo ""

# 4. KOSPI 지수 조회
echo "4️⃣ KOSPI 지수 조회 테스트"
echo "POST $BASE_URL/api/market-data/snapshot"
curl -s -X POST "$BASE_URL/api/market-data/snapshot" \
  -H "Content-Type: application/json" \
  -d '{"stock_code": "0001", "is_index": true}' | jq .
echo ""
echo ""

# 5. Rate Limiting 테스트 (연속 3회 요청)
echo "5️⃣ Rate Limiting 테스트 (연속 3회 요청)"
for i in {1..3}; do
  echo "  요청 #$i ($(date +%H:%M:%S.%3N))"
  START=$(date +%s%3N)
  curl -s -X POST "$BASE_URL/api/market-data/snapshot" \
    -H "Content-Type: application/json" \
    -d '{"stock_code": "005930"}' > /dev/null
  END=$(date +%s%3N)
  ELAPSED=$((END - START))
  echo "  → 응답 시간: ${ELAPSED}ms"
  echo ""
done
echo ""

# 6. Stats 최종 확인
echo "6️⃣ 최종 Stats 확인"
echo "GET $BASE_URL/stats"
curl -s "$BASE_URL/stats" | jq .
echo ""

echo "✅ 테스트 완료!"

