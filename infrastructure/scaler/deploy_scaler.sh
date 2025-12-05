#!/bin/bash
# deploy_scaler.sh
# Scaler Job 배포 및 Cloud Scheduler 설정

set -e

if [ -z "$GCP_PROJECT_ID" ]; then
    echo "❌ GCP_PROJECT_ID is not set"
    exit 1
fi

PROJECT_ID=${GCP_PROJECT_ID}
REGION="asia-northeast3"
IMAGE="asia-northeast3-docker.pkg.dev/${PROJECT_ID}/trading-system/service-scaler:latest"

echo "=== Service Scaler 배포 시작 ==="

# 1. Docker 이미지 빌드 및 Push
echo "[1/3] Docker 이미지 빌드..."
gcloud builds submit --tag ${IMAGE} infrastructure/scaler

# 2. Cloud Run Job 생성 (Up/Down 공용)
echo "[2/3] Cloud Run Job 생성..."

# Scale Up Job
gcloud run jobs deploy scale-up-job \
    --image ${IMAGE} \
    --args "up" \
    --region ${REGION} \
    --project ${PROJECT_ID} \
    --service-account "jennie-cloud-run-account@${PROJECT_ID}.iam.gserviceaccount.com" \
    --max-retries 3 \
    --task-timeout 300s

# Scale Down Job
gcloud run jobs deploy scale-down-job \
    --image ${IMAGE} \
    --args "down" \
    --region ${REGION} \
    --project ${PROJECT_ID} \
    --service-account "jennie-cloud-run-account@${PROJECT_ID}.iam.gserviceaccount.com" \
    --max-retries 3 \
    --task-timeout 300s

# 3. Cloud Scheduler 설정
echo "[3/3] Cloud Scheduler 설정..."

# Scale Up: 평일 07:00 (장 시작 2시간 전 - 여유 확보)
gcloud scheduler jobs create http scale-services-up \
    --schedule="0 7 * * 1-5" \
    --uri="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/scale-up-job:run" \
    --http-method=POST \
    --oauth-service-account-email="jennie-cloud-run-account@${PROJECT_ID}.iam.gserviceaccount.com" \
    --location=${REGION} \
    --time-zone="Asia/Seoul" \
    --description="Scale UP services before market open" \
    --quiet || echo "⚠️ Scheduler scale-services-up already exists (updating...)"

# Scale Down: 평일 17:00 (장 마감 1시간 30분 후 - 여유 확보)
gcloud scheduler jobs create http scale-services-down \
    --schedule="0 17 * * 1-5" \
    --uri="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/scale-down-job:run" \
    --http-method=POST \
    --oauth-service-account-email="jennie-cloud-run-account@${PROJECT_ID}.iam.gserviceaccount.com" \
    --location=${REGION} \
    --time-zone="Asia/Seoul" \
    --description="Scale DOWN services after market close" \
    --quiet || echo "⚠️ Scheduler scale-services-down already exists (updating...)"

echo "✅ Service Scaler 배포 및 스케줄링 완료!"
