#!/bin/bash
# start_mock_server.sh
# Mock KIS API 서버 시작 스크립트 (Docker 기반)
# 작업 LLM: Auto (Jennie)

set -e

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)

echo "================================================"
echo "Mock KIS API Server 시작 (Docker)"
echo "================================================"

# Docker 설치 확인
if ! command -v docker &> /dev/null; then
    echo "❌ Docker가 설치되어 있지 않습니다."
    exit 1
fi

echo "🐳 Docker 이미지 빌드 중..."
docker build -t kis-mock-server -f "$REPO_ROOT/docker/kis-mock/Dockerfile" "$REPO_ROOT"

echo ""
echo "🚀 Mock 서버 컨테이너 시작 중..."
# 기존 컨테이너 정리
docker rm -f kis-mock-server 2>/dev/null || true

# 컨테이너 실행 (포트 9443 매핑)
docker run -d \
  --name kis-mock-server \
  -p 9443:9443 \
  kis-mock-server

echo ""
echo "✅ Mock 서버가 시작되었습니다!"
echo "   주소: http://localhost:9443"
echo "   로그 확인: docker logs -f kis-mock-server"
echo "   중지: docker stop kis-mock-server"
