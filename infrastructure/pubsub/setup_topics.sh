#!/bin/bash
# setup_topics.sh
# Pub/Sub 토픽 및 구독 생성 스크립트
# 작업 LLM: Auto (Claude Sonnet 4.5)

set -e

# 환경 변수 확인
if [ -z "$GCP_PROJECT_ID" ]; then
    echo "❌ GCP_PROJECT_ID 환경 변수가 설정되지 않았습니다."
    echo "사용법: export GCP_PROJECT_ID=your-project-id"
    exit 1
fi

PROJECT_ID=${GCP_PROJECT_ID}

echo "================================================"
echo "Pub/Sub 토픽 및 구독 생성"
echo "프로젝트: ${PROJECT_ID}"
echo "================================================"

# 1. buy-signals 토픽 생성
echo "[1/4] buy-signals 토픽 생성 중..."
gcloud pubsub topics create buy-signals \
  --project=${PROJECT_ID} \
  --message-retention-duration=10m || echo "토픽이 이미 존재합니다."

# 2. buy-signals-dlq (Dead Letter Queue) 토픽 생성
echo "[2/4] buy-signals-dlq 토픽 생성 중..."
gcloud pubsub topics create buy-signals-dlq \
  --project=${PROJECT_ID} \
  --message-retention-duration=7d || echo "토픽이 이미 존재합니다."

# 3. buy-executor-sub 구독 생성 (Push 방식)
echo "[3/4] buy-executor-sub 구독 생성 중..."
echo "⚠️  주의: Buy Executor 서비스를 먼저 배포한 후 URL을 업데이트해야 합니다."

# Buy Executor 서비스 URL 조회 (배포 후)
BUY_EXECUTOR_URL=$(gcloud run services describe buy-executor \
  --region=asia-northeast3 \
  --project=${PROJECT_ID} \
  --format="value(status.url)" 2>/dev/null || echo "")

if [ -z "$BUY_EXECUTOR_URL" ]; then
    echo "⚠️  Buy Executor 서비스가 배포되지 않았습니다."
    echo "   먼저 서비스를 배포한 후 다시 실행해주세요."
    BUY_EXECUTOR_URL="https://buy-executor-PLACEHOLDER-an.a.run.app"
fi

echo "Buy Executor URL: ${BUY_EXECUTOR_URL}/process"

gcloud pubsub subscriptions create buy-executor-sub \
  --project=${PROJECT_ID} \
  --topic=buy-signals \
  --ack-deadline=300 \
  --min-retry-delay=10s \
  --max-retry-delay=600s \
  --dead-letter-topic=buy-signals-dlq \
  --max-delivery-attempts=5 \
  --push-endpoint=${BUY_EXECUTOR_URL}/process || echo "구독이 이미 존재합니다."

# 4. price-updates 토픽 생성 (선택적)
echo "[4/4] price-updates 토픽 생성 중 (선택적)..."
gcloud pubsub topics create price-updates \
  --project=${PROJECT_ID} \
  --message-retention-duration=10m || echo "토픽이 이미 존재합니다."

echo ""
echo "✅ Pub/Sub 토픽 및 구독 생성 완료!"
echo ""
echo "생성된 리소스 확인:"
echo "  gcloud pubsub topics list --project=${PROJECT_ID}"
echo "  gcloud pubsub subscriptions list --project=${PROJECT_ID}"

