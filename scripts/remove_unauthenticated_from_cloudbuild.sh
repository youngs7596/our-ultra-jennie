#!/bin/bash
# scripts/remove_unauthenticated_from_cloudbuild.sh
# 모든 cloudbuild.yaml에서 --allow-unauthenticated 제거

set -e

echo "🔒 cloudbuild.yaml 파일들에서 --allow-unauthenticated 제거 시작..."
echo ""

CLOUDBUILD_FILES=$(find services -name "cloudbuild.yaml")

for FILE in $CLOUDBUILD_FILES; do
  if grep -q "allow-unauthenticated" "$FILE"; then
    echo "🔧 [$FILE] 수정 중..."
    
    # macOS/Linux 호환 sed 사용
    sed -i.bak '/allow-unauthenticated/d' "$FILE"
    
    # 백업 파일 삭제
    rm -f "${FILE}.bak"
    
    echo "   ✅ [$FILE] --allow-unauthenticated 제거 완료"
  else
    echo "   ℹ️  [$FILE] 이미 제거됨"
  fi
done

echo ""
echo "✅ 모든 cloudbuild.yaml 수정 완료!"
echo ""
echo "📋 변경된 파일 확인:"
git diff services/*/cloudbuild.yaml | grep -E "^(---|\+|\-)" | head -20

