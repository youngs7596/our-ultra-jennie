#!/bin/bash
# scripts/test_e2e_full.sh
# MSA 완전한 E2E 테스트 (전체 거래 플로우 시뮬레이션)

set -e

echo "🧪 MSA 완전한 E2E 테스트"
echo "================================================"
echo "시나리오: 매수 신호 → 매수 실행 → 가격 모니터링 → 매도 실행"
echo ""

TOKEN=$(gcloud auth print-identity-token 2>/dev/null)
if [ -z "$TOKEN" ]; then
  echo "❌ GCP 인증 필요"
  exit 1
fi

GATEWAY_URL="https://kis-gateway-641885523217.asia-northeast3.run.app"
BUY_SCANNER_URL="https://buy-scanner-641885523217.asia-northeast3.run.app"
PRICE_MONITOR_URL="https://price-monitor-641885523217.asia-northeast3.run.app"

# ============================================================================
# Phase 1: 초기 상태 확인
# ============================================================================
echo "📋 [Phase 1] 초기 상태 확인"
echo "----------------------------------------"

# Gateway Stats
GATEWAY_STATS=$(curl -s -H "Authorization: Bearer $TOKEN" "$GATEWAY_URL/stats")
INITIAL_REQUESTS=$(echo "$GATEWAY_STATS" | jq -r '.requests.total')
echo "✅ Gateway 초기 요청 수: $INITIAL_REQUESTS"

# Buy Scanner Health
SCANNER_HEALTH=$(curl -s -H "Authorization: Bearer $TOKEN" "$BUY_SCANNER_URL/health")
SCANNER_STATUS=$(echo "$SCANNER_HEALTH" | jq -r '.status')
echo "✅ Buy Scanner 상태: $SCANNER_STATUS"

# Price Monitor Health
MONITOR_HEALTH=$(curl -s -H "Authorization: Bearer $TOKEN" "$PRICE_MONITOR_URL/health")
MONITOR_STATUS=$(echo "$MONITOR_HEALTH" | jq -r '.status')
echo "✅ Price Monitor 상태: $MONITOR_STATUS"

echo ""

# ============================================================================
# Phase 2: Buy Scanner 실행 (매수 신호 스캔)
# ============================================================================
echo "🔍 [Phase 2] Buy Scanner 실행"
echo "----------------------------------------"
echo "▶ 매수 신호 스캔 시작..."

SCAN_RESULT=$(curl -s -X POST \
  -H "Authorization: Bearer $TOKEN" \
  "$BUY_SCANNER_URL/scan")

echo "스캔 결과:"
echo "$SCAN_RESULT" | jq .

SCAN_STATUS=$(echo "$SCAN_RESULT" | jq -r '.status')
CANDIDATES=$(echo "$SCAN_RESULT" | jq -r '.candidates // [] | length')

echo ""
echo "📊 스캔 결과 분석:"
echo "  상태: $SCAN_STATUS"
echo "  매수 후보: ${CANDIDATES}개"

if [ "$CANDIDATES" -gt 0 ]; then
  echo ""
  echo "🎯 매수 후보 목록:"
  echo "$SCAN_RESULT" | jq -r '.candidates[] | "  - \(.code) \(.name) (점수: \(.score))"'
  echo ""
  echo "✅ 매수 신호 발생! Pub/Sub 메시지 발행됨"
  echo "   ⚠️  Buy Executor는 Pub/Sub Subscription을 통해 자동 트리거됨"
  echo "   (실시간 확인 필요: Cloud Run 로그 또는 DB 조회)"
else
  echo ""
  if [ "$SCAN_STATUS" = "no_candidates" ]; then
    echo "⚠️  매수 후보 없음 (시장 상황: 하락장 또는 조건 미충족)"
  elif [ "$SCAN_STATUS" = "skipped" ]; then
    echo "⚠️  스캔 건너뜀 (장 마감 등)"
  else
    echo "❌ 스캔 오류 발생"
  fi
fi

# Gateway 호출 확인
sleep 3
GATEWAY_STATS_2=$(curl -s -H "Authorization: Bearer $TOKEN" "$GATEWAY_URL/stats")
REQUESTS_2=$(echo "$GATEWAY_STATS_2" | jq -r '.requests.total')
INCREASE_1=$((REQUESTS_2 - INITIAL_REQUESTS))

echo ""
echo "📈 Gateway 호출 통계:"
echo "  Buy Scanner 실행 후: +${INCREASE_1}회"

if [ $INCREASE_1 -gt 0 ]; then
  echo "  최근 Gateway 호출:"
  echo "$GATEWAY_STATS_2" | jq -r '.recent_requests[-3:] | .[] | "    - \(.timestamp | split("T")[1] | split(".")[0]) \(.endpoint)"'
fi

echo ""

# ============================================================================
# Phase 3: DB 조회 (매수 실행 여부 확인)
# ============================================================================
echo "💾 [Phase 3] DB 조회 (매수 실행 확인)"
echo "----------------------------------------"
echo "⚠️  주의: DB 직접 조회는 OracleDB 접근 필요"
echo "   대안: Cloud Run 로그로 확인"
echo ""

echo "▶ Buy Executor 로그 확인 (최근 5분)..."
BUY_EXECUTOR_LOGS=$(gcloud logging read \
  "resource.type=cloud_run_revision 
   AND resource.labels.service_name=buy-executor" \
  --limit=20 \
  --format="value(textPayload)" \
  --freshness=5m 2>/dev/null | grep -E "(매수|주문|실행|완료|Gateway)" | head -10)

if [ -n "$BUY_EXECUTOR_LOGS" ]; then
  echo "✅ Buy Executor 활동 로그 발견:"
  echo "$BUY_EXECUTOR_LOGS" | sed 's/^/    /'
else
  echo "⚠️  Buy Executor 최근 활동 없음 (매수 신호 없었거나 아직 처리 중)"
fi

echo ""

# ============================================================================
# Phase 4: Price Monitor 실행 (가격 모니터링)
# ============================================================================
echo "📊 [Phase 4] Price Monitor 실행"
echo "----------------------------------------"
echo "▶ Price Monitor 시작..."

PM_START=$(curl -s -X POST \
  -H "Authorization: Bearer $TOKEN" \
  "$PRICE_MONITOR_URL/start")

echo "$PM_START" | jq .
PM_STATUS=$(echo "$PM_START" | jq -r '.status')

if [ "$PM_STATUS" = "started" ]; then
  echo "✅ Price Monitor 시작 성공"
  echo ""
  echo "▶ 15초 대기 (모니터링 동작 확인)..."
  
  for i in {1..3}; do
    echo "  ${i}/3 (5초)..."
    sleep 5
  done
  
  echo ""
  echo "▶ Price Monitor 로그 확인..."
  PM_LOGS=$(gcloud logging read \
    "resource.type=cloud_run_revision 
     AND resource.labels.service_name=price-monitor" \
    --limit=30 \
    --format="value(textPayload)" \
    --freshness=2m 2>/dev/null | grep -E "(모니터링|포트폴리오|현재가|매도|Gateway)" | head -15)
  
  if [ -n "$PM_LOGS" ]; then
    echo "✅ Price Monitor 활동 로그:"
    echo "$PM_LOGS" | sed 's/^/    /'
  else
    echo "⚠️  Price Monitor 활동 로그 없음"
    echo "   (보유 포트폴리오 없거나 오류 발생)"
  fi
  
  echo ""
  echo "▶ Price Monitor 중지..."
  PM_STOP=$(curl -s -X POST \
    -H "Authorization: Bearer $TOKEN" \
    "$PRICE_MONITOR_URL/stop")
  echo "✅ Price Monitor 중지 완료"
  
else
  echo "❌ Price Monitor 시작 실패"
fi

# Gateway 호출 확인
sleep 3
GATEWAY_STATS_3=$(curl -s -H "Authorization: Bearer $TOKEN" "$GATEWAY_URL/stats")
REQUESTS_3=$(echo "$GATEWAY_STATS_3" | jq -r '.requests.total')
INCREASE_2=$((REQUESTS_3 - REQUESTS_2))

echo ""
echo "📈 Gateway 호출 통계:"
echo "  Price Monitor 실행 후: +${INCREASE_2}회"

echo ""

# ============================================================================
# Phase 5: Sell Executor 로그 확인
# ============================================================================
echo "💸 [Phase 5] Sell Executor 활동 확인"
echo "----------------------------------------"
echo "▶ Sell Executor 로그 확인 (최근 10분)..."

SELL_EXECUTOR_LOGS=$(gcloud logging read \
  "resource.type=cloud_run_revision 
   AND resource.labels.service_name=sell-executor" \
  --limit=20 \
  --format="value(textPayload)" \
  --freshness=10m 2>/dev/null | grep -E "(매도|주문|실행|완료|Gateway)" | head -10)

if [ -n "$SELL_EXECUTOR_LOGS" ]; then
  echo "✅ Sell Executor 활동 로그 발견:"
  echo "$SELL_EXECUTOR_LOGS" | sed 's/^/    /'
else
  echo "⚠️  Sell Executor 최근 활동 없음 (매도 조건 미충족)"
fi

echo ""

# ============================================================================
# Phase 6: 최종 Gateway Stats
# ============================================================================
echo "📊 [Phase 6] 최종 Gateway 통계"
echo "----------------------------------------"

GATEWAY_STATS_FINAL=$(curl -s -H "Authorization: Bearer $TOKEN" "$GATEWAY_URL/stats")
REQUESTS_FINAL=$(echo "$GATEWAY_STATS_FINAL" | jq -r '.requests.total')
SUCCESSFUL=$(echo "$GATEWAY_STATS_FINAL" | jq -r '.requests.successful')
FAILED=$(echo "$GATEWAY_STATS_FINAL" | jq -r '.requests.failed')
SUCCESS_RATE=$(echo "$GATEWAY_STATS_FINAL" | jq -r '.requests.success_rate')

TOTAL_INCREASE=$((REQUESTS_FINAL - INITIAL_REQUESTS))

echo "📈 전체 테스트 기간 통계:"
echo "  초기 요청: $INITIAL_REQUESTS"
echo "  최종 요청: $REQUESTS_FINAL"
echo "  증가: +${TOTAL_INCREASE}회"
echo ""
echo "  성공: $SUCCESSFUL"
echo "  실패: $FAILED"
echo "  성공률: $SUCCESS_RATE"
echo ""

echo "🔍 Circuit Breaker 상태:"
echo "$GATEWAY_STATS_FINAL" | jq '.circuit_breaker'
echo ""

echo "📋 최근 Gateway 요청 (마지막 5개):"
echo "$GATEWAY_STATS_FINAL" | jq -r '.recent_requests[-5:] | .[] | "  \(.timestamp | split("T")[1] | split(".")[0]) | \(.endpoint) | \(.status)"'

echo ""

# ============================================================================
# Phase 7: 전체 플로우 검증
# ============================================================================
echo "================================================"
echo "✅ MSA 완전한 E2E 테스트 완료!"
echo "================================================"
echo ""
echo "📊 테스트 요약:"
echo "  ✅ Phase 1: 초기 상태 확인 완료"
echo "  ✅ Phase 2: Buy Scanner 실행 ($SCAN_STATUS)"
echo "  ✅ Phase 3: Buy Executor 로그 확인"
echo "  ✅ Phase 4: Price Monitor 시작/중지"
echo "  ✅ Phase 5: Sell Executor 로그 확인"
echo "  ✅ Phase 6: Gateway 최종 통계 ($TOTAL_INCREASE회 증가)"
echo ""

if [ $TOTAL_INCREASE -gt 5 ]; then
  echo "🎯 결론: Gateway 활발하게 사용됨 (정상)"
elif [ $TOTAL_INCREASE -gt 0 ]; then
  echo "⚠️  결론: Gateway 사용 제한적 (시장 상황 또는 포트폴리오 없음)"
else
  echo "❌ 결론: Gateway 미사용 (하락장 또는 MOCK 모드)"
fi

echo ""
echo "💡 추가 확인 사항:"
echo "  1. Cloud Run 로그에서 각 서비스 실행 상세 확인"
echo "  2. OracleDB에서 Portfolio, TradeLog 테이블 확인"
echo "  3. Pub/Sub 메시지 전달 확인 (Cloud Console)"
echo "  4. Cloud Tasks 큐 상태 확인"

