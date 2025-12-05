#!/bin/bash
# scale-services.sh
# Cloud Run 서비스의 min-instances를 동적으로 조절하는 스크립트
# Usage: ./scale-services.sh [up|down]

MODE=$1
REGION="asia-northeast3"
PROJECT_ID="${GCP_PROJECT_ID}"

if [ -z "$PROJECT_ID" ]; then
    echo "❌ GCP_PROJECT_ID is not set"
    exit 1
fi

if [ "$MODE" == "up" ]; then
    echo "🚀 [Scale UP] 장 시작 준비: 주요 서비스 min-instances=1 설정"
    MIN_INSTANCES=1
elif [ "$MODE" == "down" ]; then
    echo "🌙 [Scale DOWN] 장 마감: 주요 서비스 min-instances=0 설정 (비용 절감)"
    MIN_INSTANCES=0
else
    echo "Usage: $0 [up|down]"
    exit 1
fi

# 대상 서비스 목록
SERVICES=("kis-gateway" "buy-scanner" "price-monitor" "buy-executor" "sell-executor")

for SERVICE in "${SERVICES[@]}"; do
    echo "Updating $SERVICE..."
    gcloud run services update $SERVICE \
        --min-instances=$MIN_INSTANCES \
        --region=$REGION \
        --project=$PROJECT_ID \
        --quiet
done

echo "✅ Scaling $MODE completed."
