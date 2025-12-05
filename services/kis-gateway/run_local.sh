#!/bin/bash
# services/kis-gateway/run_local.sh
# KIS Gateway 로컬 실행 스크립트

set -e

echo "🚀 KIS Gateway 로컬 실행 시작..."
echo ""

# 현재 디렉토리 확인
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "📁 Project Root: $PROJECT_ROOT"
echo "📁 Gateway Dir: $SCRIPT_DIR"
echo ""

# 환경 변수 로드
if [ -f "$SCRIPT_DIR/env.local" ]; then
    echo "📋 환경 변수 로드 중..."
    export $(cat "$SCRIPT_DIR/env.local" | grep -v '^#' | xargs)
    echo "✅ 환경 변수 로드 완료"
else
    echo "❌ env.local 파일이 없습니다!"
    exit 1
fi

# Python 경로 설정
export PYTHONPATH="$PROJECT_ROOT:$PYTHONPATH"

echo ""
echo "🔧 설정 확인:"
echo "  - TRADING_MODE: $TRADING_MODE"
echo "  - DRY_RUN: $DRY_RUN"
echo "  - MIN_REQUEST_INTERVAL: $MIN_REQUEST_INTERVAL"
echo "  - PORT: $PORT"
echo ""

# Python 실행
cd "$SCRIPT_DIR"
echo "🎯 KIS Gateway 시작..."
echo "📍 http://localhost:$PORT"
echo ""

python3 main.py

