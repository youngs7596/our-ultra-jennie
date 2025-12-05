#!/bin/bash
# setup_daily_briefing_scheduler.sh
# Daily Briefing MSA 버전용 Cloud Scheduler 설정

set -e

PROJECT_ID=${GCP_PROJECT_ID:-gen-lang-client-0561302275}
LOCATION=${GCP_LOCATION:-asia-northeast3}

echo "================================================"
echo "Daily Briefing Cloud Scheduler 설정"
echo "프로젝트: ${PROJECT_ID}"
echo "리전: ${LOCATION}"
echo "================================================"

# Daily Briefing 서비스 URL 조회
DAILY_BRIEFING_URL=$(gcloud run services describe daily-briefing \
  --region=${LOCATION} \
  --project=${PROJECT_ID} \
  --format="value(status.url)" 2>/dev/null || echo "")

if [ -z "$DAILY_BRIEFING_URL" ]; then
    echo "❌ Daily Briefing 서비스가 배포되지 않았습니다."
    echo "   먼저 서비스를 배포한 후 다시 실행해주세요."
    exit 1
fi

echo "Daily Briefing URL: ${DAILY_BRIEFING_URL}/report"
echo ""

# Daily Briefing Scheduler 생성 (평일 오후 5시)
echo "[1/1] daily-briefing-daily 생성 중..."

gcloud scheduler jobs create http daily-briefing-daily \
  --project=${PROJECT_ID} \
  --location=${LOCATION} \
  --schedule="0 17 * * 1-5" \
  --time-zone="Asia/Seoul" \
  --uri="${DAILY_BRIEFING_URL}/report" \
  --http-method=POST \
  --oidc-service-account-email=jennie-cloud-run-account@${PROJECT_ID}.iam.gserviceaccount.com \
  --oidc-token-audience=${DAILY_BRIEFING_URL} \
  --description="Daily Briefing - 매일 오후 5시 당일 결산 리포트 발송" \
  --attempt-deadline=1800s \
  --max-retry-attempts=1 || echo "⚠️  작업이 이미 존재합니다."

echo ""
echo "✅ Daily Briefing Cloud Scheduler 설정 완료!"
echo ""
echo "📋 생성된 작업 확인:"
echo "  gcloud scheduler jobs describe daily-briefing-daily --project=${PROJECT_ID} --location=${LOCATION}"
echo ""
echo "🧪 작업 수동 실행 (테스트):"
echo "  gcloud scheduler jobs run daily-briefing-daily --project=${PROJECT_ID} --location=${LOCATION}"
echo ""
echo "⏸️  작업 일시 중지:"
echo "  gcloud scheduler jobs pause daily-briefing-daily --project=${PROJECT_ID} --location=${LOCATION}"
echo ""
echo "▶️  작업 재개:"
echo "  gcloud scheduler jobs resume daily-briefing-daily --project=${PROJECT_ID} --location=${LOCATION}"
echo ""