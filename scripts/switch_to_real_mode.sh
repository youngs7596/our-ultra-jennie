#!/bin/bash
# switch_to_real_mode.sh
# Agent 서비스들을 MOCK 모드에서 REAL 모드로 전환하는 스크립트

set -e

echo "================================================"
echo "MSA 서비스 REAL 모드 전환 스크립트"
echo "================================================"
echo ""
echo "⚠️  주의: 이 스크립트는 실제 트레이딩 모드로 전환합니다!"
echo "⚠️  실제 계좌에서 주문이 실행될 수 있습니다!"
echo ""
read -p "계속하시겠습니까? (yes/no): " confirm

if [ "$confirm" != "yes" ]; then
    echo "전환을 취소했습니다."
    exit 0
fi

echo ""
echo "================================================"
echo "REAL 모드로 전환할 서비스 목록:"
echo "================================================"
echo "1. scout-job (Watchlist 생성)"
echo "2. buy-scanner (매수 스캔)"
echo "3. buy-executor (매수 실행)"
echo "4. sell-executor (매도 실행)"
echo "5. price-monitor (가격 모니터링)"
echo "6. command-handler (명령 처리)"
echo ""

SERVICES=(
    "scout-job"
    "buy-scanner"
    "buy-executor"
    "sell-executor"
    "price-monitor"
    "command-handler"
)

for service in "${SERVICES[@]}"; do
    echo "================================================"
    echo "[${service}] env-vars-mock.yaml → env-vars-real.yaml"
    echo "================================================"
    
    cloudbuild_file="services/${service}/cloudbuild.yaml"
    
    if [ ! -f "$cloudbuild_file" ]; then
        echo "⚠️  ${cloudbuild_file} 파일을 찾을 수 없습니다. 건너뜁니다."
        continue
    fi
    
    # env-vars-mock.yaml → env-vars-real.yaml로 변경
    sed -i 's|infrastructure/env-vars-mock.yaml|infrastructure/env-vars-real.yaml|g' "$cloudbuild_file"
    
    echo "✅ ${service} cloudbuild.yaml 수정 완료"
    echo ""
done

echo "================================================"
echo "✅ 모든 서비스 cloudbuild.yaml 수정 완료!"
echo "================================================"
echo ""
echo "다음 단계:"
echo "1. 각 서비스를 재배포하세요:"
echo "   cd /home/youngs75/projects/my-supreme-jennie"
echo "   gcloud builds submit --config services/scout-job/cloudbuild.yaml ."
echo "   gcloud builds submit --config services/buy-scanner/cloudbuild.yaml ."
echo "   gcloud builds submit --config services/buy-executor/cloudbuild.yaml ."
echo "   gcloud builds submit --config services/sell-executor/cloudbuild.yaml ."
echo "   gcloud builds submit --config services/price-monitor/cloudbuild.yaml ."
echo "   gcloud builds submit --config services/command-handler/cloudbuild.yaml ."
echo ""
echo "2. Cloud Scheduler 재시작 (옵션):"
echo "   gcloud scheduler jobs resume buy-scanner-job --location=asia-northeast3"
echo ""
echo "3. Dashboard에서 트레이딩 모드 확인"
echo ""

