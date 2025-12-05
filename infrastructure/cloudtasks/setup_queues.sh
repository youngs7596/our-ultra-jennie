#!/bin/bash
# setup_queues.sh
# Cloud Tasks 큐 생성 스크립트
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
echo "Cloud Tasks 큐 생성"
echo "프로젝트: ${PROJECT_ID}"
echo "리전: ${LOCATION}"
echo "================================================"

# 1. sell-orders 큐 생성
echo "[1/1] sell-orders 큐 생성 중..."

gcloud tasks queues create sell-orders \
  --project=${PROJECT_ID} \
  --location=${LOCATION} \
  --max-dispatches-per-second=10 \
  --max-concurrent-dispatches=100 \
  --max-attempts=5 \
  --min-backoff=10s \
  --max-backoff=300s || echo "큐가 이미 존재합니다."

echo ""
echo "✅ Cloud Tasks 큐 생성 완료!"
echo ""
echo "생성된 큐 확인:"
echo "  gcloud tasks queues list --project=${PROJECT_ID} --location=${LOCATION}"
echo ""
echo "큐 상세 정보:"
echo "  gcloud tasks queues describe sell-orders --project=${PROJECT_ID} --location=${LOCATION}"

