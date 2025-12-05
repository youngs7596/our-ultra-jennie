#!/bin/bash
# setup_scout_scheduler.sh
# Scout Job MSA 버전용 Cloud Scheduler 설정

set -e

PROJECT_ID=${GCP_PROJECT_ID:-gen-lang-client-0561302275}
LOCATION=${GCP_LOCATION:-asia-northeast3}

echo "================================================"
echo "Scout Job Cloud Scheduler 설정"
echo "프로젝트: ${PROJECT_ID}"
echo "리전: ${LOCATION}"
echo "================================================"

# Scout Job 서비스 URL 조회
SCOUT_JOB_URL=$(gcloud run services describe scout-job \
  --region=${LOCATION} \
  --project=${PROJECT_ID} \
  --format="value(status.url)" 2>/dev/null || echo "")

if [ -z "$SCOUT_JOB_URL" ]; then
    echo "❌ Scout Job 서비스가 배포되지 않았습니다."
    echo "   먼저 서비스를 배포한 후 다시 실행해주세요."
    exit 1
fi

echo "Scout Job URL: ${SCOUT_JOB_URL}/scout"
echo ""

# Scout Job Scheduler 생성 (평일 오전 8시)
echo "[1/1] scout-job-daily 생성 중..."

gcloud scheduler jobs create http scout-job-daily \
  --project=${PROJECT_ID} \
  --location=${LOCATION} \
  --schedule="0 8 * * 1-5" \
  --time-zone="Asia/Seoul" \
  --uri="${SCOUT_JOB_URL}/scout" \
  --http-method=POST \
  --oidc-service-account-email=jennie-cloud-run-account@${PROJECT_ID}.iam.gserviceaccount.com \
  --oidc-token-audience=${SCOUT_JOB_URL} \
  --description="Scout Job - 매일 오전 8시 Watchlist 갱신 및 파라미터 최적화" \
  --attempt-deadline=1800s \
  --max-retry-attempts=1 || echo "⚠️  작업이 이미 존재합니다."

echo ""
echo "✅ Scout Job Cloud Scheduler 설정 완료!"
echo ""
echo "📋 생성된 작업 확인:"
echo "  gcloud scheduler jobs describe scout-job-daily --project=${PROJECT_ID} --location=${LOCATION}"
echo ""
echo "🧪 작업 수동 실행 (테스트):"
echo "  gcloud scheduler jobs run scout-job-daily --project=${PROJECT_ID} --location=${LOCATION}"
echo ""
echo "⏸️  작업 일시 중지:"
echo "  gcloud scheduler jobs pause scout-job-daily --project=${PROJECT_ID} --location=${LOCATION}"
echo ""
echo "▶️  작업 재개:"
echo "  gcloud scheduler jobs resume scout-job-daily --project=${PROJECT_ID} --location=${LOCATION}"
echo ""
echo "🗑️  레거시 Scheduler 삭제 (확인 후):"
echo "  gcloud scheduler jobs delete run-scout-job-scheduler --project=${PROJECT_ID} --location=${LOCATION} --quiet"
echo ""

