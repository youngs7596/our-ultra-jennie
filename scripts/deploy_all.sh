#!/bin/bash
# deploy_all.sh
# 전체 서비스 배포 스크립트 (병렬 배포 최적화)
# 작업 LLM: Auto (Claude Sonnet 4.5)

set -e

# 환경 변수 확인
if [ -z "$GCP_PROJECT_ID" ]; then
    echo "❌ GCP_PROJECT_ID 환경 변수가 설정되지 않았습니다."
    echo "사용법: export GCP_PROJECT_ID=your-project-id"
    exit 1
fi

PROJECT_ID=${GCP_PROJECT_ID}
REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)

echo "================================================"
echo "My Supreme Jennie - 전체 서비스 배포 (Optimized)"
echo "프로젝트: ${PROJECT_ID}"
echo "루트: ${REPO_ROOT}"
echo "총 11개 서비스 + 인프라 배포 예정"
echo "================================================"

# 1. Artifact Registry 저장소 생성 (최초 1회)
echo ""
echo "[1/4] Artifact Registry 저장소 확인 중..."
gcloud artifacts repositories describe trading-system \
  --location=asia-northeast3 \
  --project=${PROJECT_ID} &>/dev/null || \
gcloud artifacts repositories create trading-system \
  --repository-format=docker \
  --location=asia-northeast3 \
  --project=${PROJECT_ID} \
  --description="Trading System Docker Images"

echo "✅ Artifact Registry 준비 완료"

# Helper function for deployment
deploy_service() {
    local service_name=$1
    echo "🚀 [Deploying] ${service_name}..."
    gcloud builds submit \
      --config=services/${service_name}/cloudbuild.yaml \
      --project=${PROJECT_ID} > logs/${service_name}.log 2>&1
    if [ $? -eq 0 ]; then
        echo "✅ [Success] ${service_name}"
    else
        echo "❌ [Failed] ${service_name} (Check logs/${service_name}.log)"
        exit 1
    fi
}

mkdir -p logs

# 2. KIS Gateway 배포 (⭐ 가장 먼저! 모든 서비스가 의존)
echo ""
echo "[2/4] Phase 1: KIS Gateway (Blocking)..."
deploy_service "kis-gateway"

echo "✅ Phase 1 완료: KIS Gateway 배포됨"

# 3. Core Trading Services 배포 (병렬)
echo ""
echo "[3/4] Phase 2: Core Trading Services (Parallel)..."
pids=""
deploy_service "buy-scanner" & pids="$pids $!"
deploy_service "buy-executor" & pids="$pids $!"
deploy_service "sell-executor" & pids="$pids $!"
deploy_service "price-monitor" & pids="$pids $!"

# Wait for Phase 2
for pid in $pids; do
    wait $pid
    if [ $? -ne 0 ]; then
        echo "❌ Phase 2 배포 중 오류 발생"
        exit 1
    fi
done

echo "✅ Phase 2 완료: Core Trading Services 배포됨"

# 4. Support Services & Infrastructure (병렬)
echo ""
echo "[4/4] Phase 3: Support Services & Infrastructure (Parallel)..."
pids=""
deploy_service "rag-cacher" & pids="$pids $!"
deploy_service "command-handler" & pids="$pids $!"
deploy_service "dashboard" & pids="$pids $!"
deploy_service "news-crawler" & pids="$pids $!"
deploy_service "scout-job" & pids="$pids $!"
deploy_service "daily-briefing" & pids="$pids $!"

# Infrastructure Setup (Parallel with services)
(
    echo "⚙️ [Infra] 설정 시작..."
    bash ${REPO_ROOT}/infrastructure/pubsub/setup_topics.sh > logs/infra_pubsub.log 2>&1 || echo "⚠️ Pub/Sub 설정 실패"
    bash ${REPO_ROOT}/infrastructure/cloudtasks/setup_queues.sh > logs/infra_tasks.log 2>&1 || echo "⚠️ Cloud Tasks 설정 실패"
    bash ${REPO_ROOT}/infrastructure/scheduler/setup_jobs.sh > logs/infra_scheduler.log 2>&1 || echo "⚠️ Scheduler 설정 실패"
    bash ${REPO_ROOT}/infrastructure/scheduler/setup_news_crawler_jobs.sh > logs/infra_news.log 2>&1 || echo "⚠️ News Crawler Scheduler 설정 실패"
    bash ${REPO_ROOT}/infrastructure/scheduler/setup_scout_scheduler.sh > logs/infra_scout.log 2>&1 || echo "⚠️ Scout Scheduler 설정 실패"
    bash ${REPO_ROOT}/infrastructure/scheduler/setup_price_monitor_jobs.sh > logs/infra_price.log 2>&1 || echo "⚠️ Price Monitor Scheduler 설정 실패"
    bash ${REPO_ROOT}/infrastructure/scheduler/setup_kis_gateway_scheduler.sh > logs/infra_gateway.log 2>&1 || echo "⚠️ KIS Gateway Scheduler 설정 실패"
    bash ${REPO_ROOT}/infrastructure/scheduler/setup_daily_briefing_scheduler.sh > logs/infra_briefing.log 2>&1 || echo "⚠️ Daily Briefing Scheduler 설정 실패"
    echo "✅ [Infra] 설정 완료"
) & pids="$pids $!"

# Wait for Phase 3
for pid in $pids; do
    wait $pid
    if [ $? -ne 0 ]; then
        echo "❌ Phase 3 배포 중 오류 발생"
        exit 1
    fi
done

echo "✅ Phase 3 완료: Support Services & Infra 배포됨"

# 13. 서비스 URL 출력
echo ""
echo "[완료] 배포 완료 - 서비스 URL 확인"
echo "================================================"

services=("buy-scanner" "buy-executor" "price-monitor" "sell-executor" "rag-cacher" "command-handler" "daily-briefing" "dashboard" "news-crawler" "scout-job" "kis-gateway")

for service in "${services[@]}"; do
    url=$(gcloud run services describe ${service} \
      --region=asia-northeast3 \
      --project=${PROJECT_ID} \
      --format="value(status.url)" 2>/dev/null || echo "N/A")
    echo "${service}: ${url}"
done

echo "================================================"
echo ""
echo "✅ 전체 배포 완료!"
echo "📊 로그 확인: logs/ 디렉토리"
