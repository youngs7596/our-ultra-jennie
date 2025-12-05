#!/bin/bash
# deploy_backtest_logic.sh
# Backtest Logic Update Deployment (Smart Skip, Tiered Execution, Dynamic Risk)
# Targets: buy-scanner, buy-executor, price-monitor

set -e

# 환경 변수 확인
if [ -z "$GCP_PROJECT_ID" ]; then
    # .env 파일에서 로드 시도
    if [ -f .env ]; then
        export $(grep -v '^#' .env | xargs)
    fi
fi

if [ -z "$GCP_PROJECT_ID" ]; then
    echo "❌ GCP_PROJECT_ID 환경 변수가 설정되지 않았습니다."
    echo "사용법: export GCP_PROJECT_ID=your-project-id"
    exit 1
fi

PROJECT_ID=${GCP_PROJECT_ID}
REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)

echo "================================================"
echo "My Supreme Jennie - Backtest Logic Deployment"
echo "프로젝트: ${PROJECT_ID}"
echo "대상 서비스: buy-scanner, buy-executor, price-monitor"
echo "변경 사항: Smart Skip, Tiered Execution, Dynamic Risk"
echo "================================================"

mkdir -p logs

# Helper function for deployment
deploy_service() {
    local service_name=$1
    echo "🚀 [Deploying] ${service_name}..."
    gcloud builds submit \
      --config=services/${service_name}/cloudbuild.yaml \
      --project=${PROJECT_ID} > logs/${service_name}_deploy.log 2>&1
    if [ $? -eq 0 ]; then
        echo "✅ [Success] ${service_name}"
    else
        echo "❌ [Failed] ${service_name} (Check logs/${service_name}_deploy.log)"
        exit 1
    fi
}

# 병렬 배포 시작
echo ""
echo "[Start] 3개 서비스 병렬 배포 시작..."
pids=""

deploy_service "buy-scanner" & pids="$pids $!"
deploy_service "buy-executor" & pids="$pids $!"
deploy_service "price-monitor" & pids="$pids $!"

# Wait for all processes
for pid in $pids; do
    wait $pid
    if [ $? -ne 0 ]; then
        echo "❌ 배포 중 오류 발생"
        exit 1
    fi
done

echo ""
echo "✅ 모든 서비스 배포 완료!"
echo "================================================"

# 서비스 URL 출력
services=("buy-scanner" "buy-executor" "price-monitor")
for service in "${services[@]}"; do
    url=$(gcloud run services describe ${service} \
      --region=asia-northeast3 \
      --project=${PROJECT_ID} \
      --format="value(status.url)" 2>/dev/null || echo "N/A")
    echo "${service}: ${url}"
done

echo "================================================"
echo "📊 로그 확인: logs/ 디렉토리"
