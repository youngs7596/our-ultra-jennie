#!/bin/bash
# infrastructure/scheduler/setup_kis_gateway_scheduler.sh
# KIS Gateway 자동 시작/종료 스케줄러
# 평일 07:00-17:00 운영 (Scout 07:10 시작 대비)

set -e

PROJECT_ID="gen-lang-client-0561302275"
REGION="asia-northeast3"
SERVICE_ACCOUNT="jennie-cloud-run-account@${PROJECT_ID}.iam.gserviceaccount.com"
GATEWAY_URL="https://kis-gateway-641885523217.${REGION}.run.app"

echo "🚀 KIS Gateway 스케줄러 설정 시작..."
echo ""

# 1. Gateway 워밍업 스케줄러 (평일 06:50 KST)
echo "📅 1. Gateway 워밍업 스케줄러 생성 중..."
gcloud scheduler jobs create http kis-gateway-warmup \
  --project="${PROJECT_ID}" \
  --location="${REGION}" \
  --schedule="50 6 * * 1-5" \
  --time-zone="Asia/Seoul" \
  --uri="${GATEWAY_URL}/health" \
  --http-method=GET \
  --oidc-service-account-email="${SERVICE_ACCOUNT}" \
  --oidc-token-audience="${GATEWAY_URL}" \
  --description="KIS Gateway 워밍업 (평일 06:50 - Scout 시작 20분 전)" \
  --attempt-deadline=30s \
  || echo "⚠️  이미 존재하거나 생성 실패"

echo ""

# 2. Health Check 유지 스케줄러 (장 중 5분마다, 07:00-17:00)
echo "📅 2. Health Check 스케줄러 생성 중..."
gcloud scheduler jobs create http kis-gateway-keepalive \
  --project="${PROJECT_ID}" \
  --location="${REGION}" \
  --schedule="*/5 7-16 * * 1-5" \
  --time-zone="Asia/Seoul" \
  --uri="${GATEWAY_URL}/health" \
  --http-method=GET \
  --oidc-service-account-email="${SERVICE_ACCOUNT}" \
  --oidc-token-audience="${GATEWAY_URL}" \
  --description="KIS Gateway 활성 유지 (평일 07:00-16:59, 5분마다)" \
  --attempt-deadline=30s \
  || echo "⚠️  이미 존재하거나 생성 실패"

echo ""
echo "✅ KIS Gateway 스케줄러 설정 완료!"
echo ""
echo "📋 생성된 스케줄러:"
echo "   1. kis-gateway-warmup   : 평일 06:50 - Gateway 워밍업"
echo "   2. kis-gateway-keepalive: 평일 07:00-16:59 (5분마다) - 활성 유지"
echo ""
echo "📊 Gateway 가동 시간:"
echo "   - 시작: 06:50 (워밍업)"
echo "   - 활성: 07:00-17:00 (10시간)"
echo "   - 종료: 17:00 이후 자동 (요청 없으면 스케일 다운)"
echo ""
echo "💰 비용 절감:"
echo "   - 기존: 24시간 가동"
echo "   - 현재: 10시간 가동 (58% 절감!)"
echo ""
echo "💡 참고:"
echo "   - min-instances=0으로 설정하면 요청 없을 시 자동 종료"
echo "   - 첫 요청 시 Cold Start (약 5-10초)"
echo "   - keepalive로 활성 상태 유지 (07:00-17:00)"


