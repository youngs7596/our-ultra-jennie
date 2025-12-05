#!/bin/bash
# setup_jobs.sh
# Cloud Scheduler 작업 생성 스크립트
# 작업 LLM: Auto (Claude Sonnet 4.5)

set -e

# 환경 변수 확인
if [ -z "$GCP_PROJECT_ID" ]; then
    echo "❌ GCP_PROJECT_ID 환경 변수가 설정되지 않았습니다."
    echo "사용법: export GCP_PROJECT_ID=your-project-id"
    exit 1
fi

PROJECT_ID=${GCP_PROJECT_ID}
LOCATION=${GCP_LOCATION:-asia-northeast3}

echo "================================================"
echo "Cloud Scheduler 작업 생성"
echo "프로젝트: ${PROJECT_ID}"
echo "리전: ${LOCATION}"
echo "================================================"

# Buy Scanner 서비스 URL 조회
BUY_SCANNER_URL=$(gcloud run services describe buy-scanner \
  --region=${LOCATION} \
  --project=${PROJECT_ID} \
  --format="value(status.url)" 2>/dev/null || echo "")

if [ -z "$BUY_SCANNER_URL" ]; then
    echo "❌ Buy Scanner 서비스가 배포되지 않았습니다."
    echo "   먼저 서비스를 배포한 후 다시 실행해주세요."
    exit 1
fi

echo "Buy Scanner URL: ${BUY_SCANNER_URL}/scan"

# 1. buy-scanner-job (10분마다 실행, 장중에만)
echo "[1/3] buy-scanner-job 생성 중..."

gcloud scheduler jobs create http buy-scanner-job \
  --project=${PROJECT_ID} \
  --location=${LOCATION} \
  --schedule="*/5 9-15 * * 1-5" \
  --time-zone="Asia/Seoul" \
  --uri="${BUY_SCANNER_URL}/scan" \
  --http-method=POST \
  --oidc-service-account-email=agent-runner@${PROJECT_ID}.iam.gserviceaccount.com \
  --oidc-token-audience=${BUY_SCANNER_URL} \
  --description="Buy Scanner - 매수 신호 스캔 (10분 주기)" || echo "작업이 이미 존재합니다."

# 2. rag-cacher-job (10분마다 실행, 장중에만)
echo "[2/3] rag-cacher-job 생성 중..."

RAG_CACHER_URL=$(gcloud run services describe rag-cacher \
  --region=${LOCATION} \
  --project=${PROJECT_ID} \
  --format="value(status.url)" 2>/dev/null || echo "")

if [ -z "$RAG_CACHER_URL" ]; then
    echo "⚠️  RAG Cacher 서비스가 배포되지 않았습니다. 스킵합니다."
else
    echo "RAG Cacher URL: ${RAG_CACHER_URL}/cache"
    
    gcloud scheduler jobs create http rag-cacher-job \
      --project=${PROJECT_ID} \
      --location=${LOCATION} \
      --schedule="*/10 9-15 * * 1-5" \
      --time-zone="Asia/Seoul" \
      --uri="${RAG_CACHER_URL}/cache" \
      --http-method=POST \
      --oidc-service-account-email=agent-runner@${PROJECT_ID}.iam.gserviceaccount.com \
      --oidc-token-audience=${RAG_CACHER_URL} \
      --description="RAG Cacher - RAG 컨텍스트 캐싱 (10분 주기)" || echo "작업이 이미 존재합니다."
fi

# 3. command-handler-job (30분마다 실행)
echo "[3/3] command-handler-job 생성 중..."

COMMAND_HANDLER_URL=$(gcloud run services describe command-handler \
  --region=${LOCATION} \
  --project=${PROJECT_ID} \
  --format="value(status.url)" 2>/dev/null || echo "")

if [ -z "$COMMAND_HANDLER_URL" ]; then
    echo "⚠️  Command Handler 서비스가 배포되지 않았습니다. 스킵합니다."
else
    echo "Command Handler URL: ${COMMAND_HANDLER_URL}/poll"
    
    gcloud scheduler jobs create http command-handler-job \
      --project=${PROJECT_ID} \
      --location=${LOCATION} \
      --schedule="*/30 * * * *" \
      --time-zone="Asia/Seoul" \
      --uri="${COMMAND_HANDLER_URL}/poll" \
      --http-method=POST \
      --oidc-service-account-email=agent-runner@${PROJECT_ID}.iam.gserviceaccount.com \
      --oidc-token-audience=${COMMAND_HANDLER_URL} \
      --description="Command Handler - 수동 명령 폴링 (30분 주기)" || echo "작업이 이미 존재합니다."
fi

echo ""
echo "✅ Cloud Scheduler 작업 생성 완료!"
echo ""
echo "생성된 작업 확인:"
echo "  gcloud scheduler jobs list --project=${PROJECT_ID} --location=${LOCATION}"
echo ""
echo "작업 수동 실행 (테스트):"
echo "  gcloud scheduler jobs run buy-scanner-job --project=${PROJECT_ID} --location=${LOCATION}"
echo ""
echo "작업 일시 중지:"
echo "  gcloud scheduler jobs pause buy-scanner-job --project=${PROJECT_ID} --location=${LOCATION}"
echo ""
echo "작업 재개:"
echo "  gcloud scheduler jobs resume buy-scanner-job --project=${PROJECT_ID} --location=${LOCATION}"
