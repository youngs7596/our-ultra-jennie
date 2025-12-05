#!/bin/bash

# deploy_v15_0_update.sh
# [v15.0] 듀얼 모멘텀 + 거래량 돌파 전략 업데이트 배포
# Scout Job 리팩토링 및 Shared 라이브러리 변경 사항 적용을 위해 주요 서비스를 재배포합니다.

set -e

echo "🚀 [v15.0] 배포 시작: 듀얼 모멘텀 + 거래량 돌파 전략 업데이트"

# 1. Scout Job (Cloud Run Job) 배포
echo "📦 [1/6] Scout Job 배포 중..."
gcloud builds submit --config services/scout-job/cloudbuild.yaml . &
PID1=$!

# 2. Buy Scanner 배포 (Shared 변경 적용)
echo "📦 [2/6] Buy Scanner 배포 중..."
gcloud builds submit --config services/buy-scanner/cloudbuild.yaml . &
PID2=$!

# 3. Buy Executor 배포 (Shared 변경 적용)
echo "📦 [3/6] Buy Executor 배포 중..."
gcloud builds submit --config services/buy-executor/cloudbuild.yaml . &
PID3=$!

# 4. Sell Executor 배포 (Shared 변경 적용)
echo "📦 [4/6] Sell Executor 배포 중..."
gcloud builds submit --config services/sell-executor/cloudbuild.yaml . &
PID4=$!

# 5. Price Monitor 배포 (Shared 변경 적용)
echo "📦 [5/6] Price Monitor 배포 중..."
gcloud builds submit --config services/price-monitor/cloudbuild.yaml . &
PID5=$!

# 6. Daily Briefing 배포 (Shared 변경 적용)
echo "📦 [6/6] Daily Briefing 배포 중..."
gcloud builds submit --config services/daily-briefing/cloudbuild.yaml . &
PID6=$!

# 모든 배포 완료 대기
wait $PID1
echo "✅ Scout Job 배포 완료"

wait $PID2
echo "✅ Buy Scanner 배포 완료"

wait $PID3
echo "✅ Buy Executor 배포 완료"

wait $PID4
echo "✅ Sell Executor 배포 완료"

wait $PID5
echo "✅ Price Monitor 배포 완료"

wait $PID6
echo "✅ Daily Briefing 배포 완료"

echo "🎉 [v15.0] 모든 서비스 배포가 완료되었습니다!"
