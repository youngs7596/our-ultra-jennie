#!/bin/bash
# scripts/secure_all_services.sh
# 모든 Cloud Run 서비스를 "인증 필요"로 변경

set -e

PROJECT_ID="gen-lang-client-0561302275"
REGION="asia-northeast3"

SERVICES=(
  "buy-scanner"
  "buy-executor"
  "sell-executor"
  "price-monitor"
  "rag-cacher"
  "command-handler"
  "dashboard"
)

echo "🔒 모든 서비스를 '인증 필요'로 변경 시작..."
echo ""

for SERVICE in "${SERVICES[@]}"; do
  echo "🔐 [$SERVICE] 권한 제거 중..."
  
  # allUsers의 invoker 권한 제거
  gcloud run services remove-iam-policy-binding "$SERVICE" \
    --project="$PROJECT_ID" \
    --region="$REGION" \
    --member="allUsers" \
    --role="roles/run.invoker" \
    2>/dev/null || echo "   (이미 제거됨 또는 권한 없음)"
  
  echo "   ✅ [$SERVICE] '인증 필요'로 변경 완료"
  echo ""
done

echo "✅ 모든 서비스 보안 설정 완료!"
echo ""
echo "📋 서비스 인증 상태 확인:"
echo ""

for SERVICE in "${SERVICES[@]}"; do
  echo "[$SERVICE]"
  gcloud run services get-iam-policy "$SERVICE" \
    --project="$PROJECT_ID" \
    --region="$REGION" \
    --format="table(bindings.role,bindings.members)" 2>/dev/null || echo "   조회 실패"
  echo ""
done

