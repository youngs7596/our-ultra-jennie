#!/bin/bash

# setup_price_monitor_jobs.sh
# Price Monitor 장 시작/종료 Cloud Scheduler 설정

set -e

PROJECT_ID="gen-lang-client-0561302275"
LOCATION="asia-northeast3"
SERVICE_ACCOUNT="641885523217-compute@developer.gserviceaccount.com"
SERVICE_URL="https://price-monitor-641885523217.asia-northeast3.run.app"

echo "🔧 Price Monitor Cloud Scheduler 설정 시작..."

# 1. 평일 장 시작 (09:00 KST) - /start 호출
echo "📅 [1/2] price-monitor-start Scheduler Job 생성 중..."
gcloud scheduler jobs create http price-monitor-start \
  --project="${PROJECT_ID}" \
  --location="${LOCATION}" \
  --schedule="0 9 * * 1-5" \
  --time-zone="Asia/Seoul" \
  --uri="${SERVICE_URL}/start" \
  --http-method=POST \
  --oidc-service-account-email="${SERVICE_ACCOUNT}" \
  --description="평일 장 시작 시 Price Monitor 시작 (09:00 KST)" \
  || echo "⚠️  price-monitor-start 이미 존재함"

# 2. 평일 장 종료 (15:30 KST) - /stop 호출
echo "📅 [2/2] price-monitor-stop Scheduler Job 생성 중..."
gcloud scheduler jobs create http price-monitor-stop \
  --project="${PROJECT_ID}" \
  --location="${LOCATION}" \
  --schedule="30 15 * * 1-5" \
  --time-zone="Asia/Seoul" \
  --uri="${SERVICE_URL}/stop" \
  --http-method=POST \
  --oidc-service-account-email="${SERVICE_ACCOUNT}" \
  --description="평일 장 종료 시 Price Monitor 중단 (15:30 KST)" \
  || echo "⚠️  price-monitor-stop 이미 존재함"

echo "✅ Price Monitor Cloud Scheduler 설정 완료!"
echo ""
echo "📋 생성된 Scheduler Jobs:"
gcloud scheduler jobs list \
  --project="${PROJECT_ID}" \
  --location="${LOCATION}" \
  --filter="name:price-monitor" \
  --format="table(name,schedule,state)"

