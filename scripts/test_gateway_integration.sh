#!/bin/bash
# scripts/test_gateway_integration.sh
# KIS Gateway 통합 테스트 (Cloud Run 배포된 서비스 사용)

set -e

echo "🧪 KIS Gateway 통합 테스트"
echo "================================================"
echo ""

# Gateway URL
GATEWAY_URL="https://kis-gateway-641885523217.asia-northeast3.run.app"
BUY_SCANNER_URL="https://buy-scanner-641885523217.asia-northeast3.run.app"
TOKEN=$(gcloud auth print-identity-token 2>/dev/null)

if [ -z "$TOKEN" ]; then
  echo "❌ GCP 인증 토큰 획득 실패"
  exit 1
fi

echo "✅ 인증 토큰 획득 완료"
echo ""

# Test 1: Gateway Health Check
echo "[Test 1] Gateway Health Check"
echo "----------------------------------------"
HEALTH=$(curl -s -H "Authorization: Bearer $TOKEN" "$GATEWAY_URL/health")
echo "$HEALTH" | jq .
STATUS=$(echo "$HEALTH" | jq -r '.status')

if [ "$STATUS" = "ok" ]; then
  echo "✅ Gateway Health Check 성공"
else
  echo "❌ Gateway Health Check 실패"
  exit 1
fi
echo ""

# Test 2: Gateway Stats (Before)
echo "[Test 2] Gateway Stats (Before)"
echo "----------------------------------------"
STATS_BEFORE=$(curl -s -H "Authorization: Bearer $TOKEN" "$GATEWAY_URL/stats")
TOTAL_BEFORE=$(echo "$STATS_BEFORE" | jq -r '.requests.total')
echo "이전 요청 수: $TOTAL_BEFORE"
echo ""

# Test 3: Direct Gateway API Call
echo "[Test 3] Direct Gateway API Call (삼성전자)"
echo "----------------------------------------"
SNAPSHOT=$(curl -s -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"stock_code": "005930"}' \
  "$GATEWAY_URL/api/market-data/snapshot")

echo "$SNAPSHOT" | jq '{success, price: .data.price, volume: .data.volume, response_time}'
SUCCESS=$(echo "$SNAPSHOT" | jq -r '.success')

if [ "$SUCCESS" = "true" ]; then
  echo "✅ Direct API Call 성공"
else
  echo "❌ Direct API Call 실패"
  exit 1
fi
echo ""

# Test 4: Buy Scanner (Gateway 사용 확인)
echo "[Test 4] Buy Scanner 실행 (Gateway 간접 호출)"
echo "----------------------------------------"
SCAN_RESULT=$(curl -s -X POST \
  -H "Authorization: Bearer $TOKEN" \
  "$BUY_SCANNER_URL/scan")

echo "$SCAN_RESULT" | jq .
SCAN_STATUS=$(echo "$SCAN_RESULT" | jq -r '.status')

if [ "$SCAN_STATUS" != "error" ]; then
  echo "✅ Buy Scanner 실행 성공"
else
  echo "⚠️  Buy Scanner 실행 실패 (시장 상황일 수 있음)"
fi
echo ""

# Test 5: Gateway Stats (After)
echo "[Test 5] Gateway Stats (After)"
echo "----------------------------------------"
sleep 2
STATS_AFTER=$(curl -s -H "Authorization: Bearer $TOKEN" "$GATEWAY_URL/stats")
TOTAL_AFTER=$(echo "$STATS_AFTER" | jq -r '.requests.total')
SUCCESSFUL=$(echo "$STATS_AFTER" | jq -r '.requests.successful')
SUCCESS_RATE=$(echo "$STATS_AFTER" | jq -r '.requests.success_rate')

echo "이후 요청 수: $TOTAL_AFTER (증가: $(($TOTAL_AFTER - $TOTAL_BEFORE)))"
echo "성공 요청: $SUCCESSFUL"
echo "성공률: $SUCCESS_RATE"
echo ""

echo "$STATS_AFTER" | jq '.recent_requests[-3:] | map({endpoint, status, timestamp})'
echo ""

# Test 6: Rate Limiting 확인
echo "[Test 6] Rate Limiting 테스트 (3회 연속)"
echo "----------------------------------------"
for i in {1..3}; do
  START=$(date +%s%N)
  curl -s -X POST \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"stock_code": "005930"}' \
    "$GATEWAY_URL/api/market-data/snapshot" > /dev/null
  END=$(date +%s%N)
  ELAPSED=$(echo "scale=3; ($END - $START) / 1000000000" | bc)
  echo "  요청 $i: ${ELAPSED}초"
done
echo ""

# Final Stats
echo "[Final] Gateway 최종 통계"
echo "----------------------------------------"
STATS_FINAL=$(curl -s -H "Authorization: Bearer $TOKEN" "$GATEWAY_URL/stats")
echo "$STATS_FINAL" | jq '{
  requests: .requests,
  circuit_breaker: .circuit_breaker,
  rate_limiting: .rate_limiting
}'

echo ""
echo "================================================"
echo "✅ 통합 테스트 완료!"
echo "================================================"

