#!/bin/bash
# deploy_scout_expansion.sh
# Scout Job Update Deployment (Smart Universe Expansion: Top 200 -> Momentum Filter)

set -e

# 환경 변수 확인
if [ -z "$GCP_PROJECT_ID" ]; then
    if [ -f .env ]; then
        export $(grep -v '^#' .env | xargs)
    fi
fi

if [ -z "$GCP_PROJECT_ID" ]; then
    echo "❌ GCP_PROJECT_ID 환경 변수가 설정되지 않았습니다."
    exit 1
fi

PROJECT_ID=${GCP_PROJECT_ID}
REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)

echo "================================================"
echo "My Supreme Jennie - Scout Expansion Deployment"
echo "프로젝트: ${PROJECT_ID}"
echo "대상 서비스: scout-job"
echo "변경 사항: Smart Universe (Top 200 -> Momentum Filter)"
echo "================================================"

mkdir -p logs

echo "🚀 [Deploying] scout-job..."
gcloud builds submit \
  --config=services/scout-job/cloudbuild.yaml \
  --project=${PROJECT_ID} > logs/scout_job_deploy.log 2>&1

if [ $? -eq 0 ]; then
    echo "✅ [Success] scout-job"
else
    echo "❌ [Failed] scout-job (Check logs/scout_job_deploy.log)"
    exit 1
fi

echo ""
echo "✅ Scout Job 배포 완료!"
echo "================================================"
