#!/bin/bash
# setup_news_crawler_jobs.sh
# News Crawler Cloud Scheduler 작업 생성 스크립트
# 기존 crawler-job-07xx, 08-16, 17xx 스케줄을 news-crawler 서비스로 마이그레이션

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
echo "News Crawler Cloud Scheduler 작업 생성"
echo "프로젝트: ${PROJECT_ID}"
echo "리전: ${LOCATION}"
echo "================================================"

# News Crawler 서비스 URL 조회
NEWS_CRAWLER_URL=$(gcloud run services describe news-crawler \
  --region=${LOCATION} \
  --project=${PROJECT_ID} \
  --format="value(status.url)" 2>/dev/null || echo "")

if [ -z "$NEWS_CRAWLER_URL" ]; then
    echo "❌ News Crawler 서비스가 배포되지 않았습니다."
    echo "   먼저 서비스를 배포한 후 다시 실행해주세요."
    exit 1
fi

echo "News Crawler URL: ${NEWS_CRAWLER_URL}/crawl"
echo ""

# 1. news-crawler-job-07xx (오전 7시대 - 20,30,40,50분)
echo "[1/3] news-crawler-job-07xx 생성 중..."

gcloud scheduler jobs create http news-crawler-job-07xx \
  --project=${PROJECT_ID} \
  --location=${LOCATION} \
  --schedule="*/20 7 * * 1-5" \
  --time-zone="Asia/Seoul" \
  --uri="${NEWS_CRAWLER_URL}/crawl" \
  --http-method=POST \
  --oidc-service-account-email=jennie-cloud-run-account@${PROJECT_ID}.iam.gserviceaccount.com \
  --oidc-token-audience=${NEWS_CRAWLER_URL} \
  --description="News Crawler - 뉴스 수집 (오전 7시대)" || echo "⚠️  작업이 이미 존재합니다."

# 2. news-crawler-job-08-16 (오전 8시~오후 4시 - 10분 간격)
echo "[2/3] news-crawler-job-08-16 생성 중..."

gcloud scheduler jobs create http news-crawler-job-08-16 \
  --project=${PROJECT_ID} \
  --location=${LOCATION} \
  --schedule="*/20 8-16 * * 1-5" \
  --time-zone="Asia/Seoul" \
  --uri="${NEWS_CRAWLER_URL}/crawl" \
  --http-method=POST \
  --oidc-service-account-email=jennie-cloud-run-account@${PROJECT_ID}.iam.gserviceaccount.com \
  --oidc-token-audience=${NEWS_CRAWLER_URL} \
  --description="News Crawler - 뉴스 수집 (장중)" || echo "⚠️  작업이 이미 존재합니다."

# 3. news-crawler-job-17xx (오후 5시대 - 0,10,20,30,40,50분)
echo "[3/3] news-crawler-job-17xx 생성 중..."

gcloud scheduler jobs create http news-crawler-job-17xx \
  --project=${PROJECT_ID} \
  --location=${LOCATION} \
  --schedule="0,15,30,45 17 * * 1-5" \
  --time-zone="Asia/Seoul" \
  --uri="${NEWS_CRAWLER_URL}/crawl" \
  --http-method=POST \
  --oidc-service-account-email=jennie-cloud-run-account@${PROJECT_ID}.iam.gserviceaccount.com \
  --oidc-token-audience=${NEWS_CRAWLER_URL} \
  --description="News Crawler - 뉴스 수집 (오후 5시대)" || echo "⚠️  작업이 이미 존재합니다."

echo ""
echo "✅ News Crawler Cloud Scheduler 작업 생성 완료!"
echo ""
echo "📋 생성된 작업 확인:"
echo "  gcloud scheduler jobs list --project=${PROJECT_ID} --location=${LOCATION} | grep news-crawler"
echo ""
echo "🧪 작업 수동 실행 (테스트):"
echo "  gcloud scheduler jobs run news-crawler-job-08-16 --project=${PROJECT_ID} --location=${LOCATION}"
echo ""
echo "⏸️  작업 일시 중지:"
echo "  gcloud scheduler jobs pause news-crawler-job-08-16 --project=${PROJECT_ID} --location=${LOCATION}"
echo ""
echo "▶️  작업 재개:"
echo "  gcloud scheduler jobs resume news-crawler-job-08-16 --project=${PROJECT_ID} --location=${LOCATION}"
echo ""
echo "🗑️  기존 crawler-job 삭제 (옵션):"
echo "  gcloud scheduler jobs delete crawler-job-07xx --project=${PROJECT_ID} --location=${LOCATION} --quiet"
echo "  gcloud scheduler jobs delete crawler-job-08-16 --project=${PROJECT_ID} --location=${LOCATION} --quiet"
echo "  gcloud scheduler jobs delete crawler-job-17xx --project=${PROJECT_ID} --location=${LOCATION} --quiet"
echo ""

