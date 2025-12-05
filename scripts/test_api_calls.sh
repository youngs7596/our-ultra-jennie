#!/bin/bash
# test_api_calls.sh
# 각 서비스 API 테스트 스크립트
# 작업 LLM: Auto (Claude Sonnet 4.5)

set -e

echo "================================================"
echo "My Supreme Jennie - API 테스트"
echo "================================================"

# 색상 코드
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 헬퍼 함수
test_endpoint() {
    local name=$1
    local url=$2
    local method=${3:-GET}
    local data=${4:-}
    
    echo -e "\n${YELLOW}[테스트]${NC} $name"
    echo "  URL: $url"
    
    if [ "$method" = "GET" ]; then
        response=$(curl -s -w "\n%{http_code}" "$url")
    else
        response=$(curl -s -w "\n%{http_code}" -X "$method" -H "Content-Type: application/json" -d "$data" "$url")
    fi
    
    http_code=$(echo "$response" | tail -n1)
    body=$(echo "$response" | sed '$d')
    
    if [ "$http_code" -eq 200 ]; then
        echo -e "  ${GREEN}✅ 성공 (HTTP $http_code)${NC}"
        echo "  응답: $body" | head -c 200
        if [ ${#body} -gt 200 ]; then
            echo "... (생략)"
        else
            echo ""
        fi
    else
        echo -e "  ${RED}❌ 실패 (HTTP $http_code)${NC}"
        echo "  응답: $body"
    fi
}

# 메뉴
echo ""
echo "테스트 메뉴:"
echo "1. Mock KIS API 서버"
echo "2. Buy Scanner"
echo "3. Buy Executor"
echo "4. Price Monitor"
echo "5. Sell Executor"
echo "6. RAG Cacher"
echo "7. Command Handler"
echo "8. 전체 서비스 Health Check"
echo ""
read -p "선택 (1-8): " choice

case $choice in
    1)
        echo -e "\n${YELLOW}=== Mock KIS API 서버 테스트 ===${NC}"
        test_endpoint "Health Check" "http://localhost:9443/health"
        test_endpoint "현재가 조회 (삼성전자)" "http://localhost:9443/uapi/domestic-stock/v1/quotations/inquire-price?FID_INPUT_ISCD=005930"
        test_endpoint "Mock Orders 조회" "http://localhost:9443/mock/orders"
        ;;
    2)
        echo -e "\n${YELLOW}=== Buy Scanner 테스트 ===${NC}"
        test_endpoint "Health Check" "http://localhost:8080/health"
        test_endpoint "매수 스캔 실행" "http://localhost:8080/scan" "POST"
        ;;
    3)
        echo -e "\n${YELLOW}=== Buy Executor 테스트 ===${NC}"
        test_endpoint "Health Check" "http://localhost:8081/health"
        
        # Base64 인코딩된 메시지 (삼성전자 매수 신호)
        base64_data=$(echo -n '{"stock_code": "005930", "stock_name": "삼성전자", "score": 85.5, "timestamp": "2025-11-18T10:00:00"}' | base64)
        pubsub_message="{\"message\": {\"data\": \"$base64_data\", \"messageId\": \"test-msg-001\", \"publishTime\": \"2025-11-18T10:00:00Z\"}}"
        
        test_endpoint "Pub/Sub 메시지 처리" "http://localhost:8081/process" "POST" "$pubsub_message"
        ;;
    4)
        echo -e "\n${YELLOW}=== Price Monitor 테스트 ===${NC}"
        test_endpoint "Health Check" "http://localhost:8082/health"
        test_endpoint "모니터링 시작" "http://localhost:8082/start" "POST"
        echo ""
        echo "5초 대기 중..."
        sleep 5
        test_endpoint "모니터링 중지" "http://localhost:8082/stop" "POST"
        ;;
    5)
        echo -e "\n${YELLOW}=== Sell Executor 테스트 ===${NC}"
        test_endpoint "Health Check" "http://localhost:8083/health"
        
        sell_task='{"stock_code": "005930", "stock_name": "삼성전자", "quantity": 10, "current_price": 77000, "reason": "TAKE_PROFIT", "timestamp": "2025-11-18T10:00:00"}'
        
        test_endpoint "매도 주문 처리" "http://localhost:8083/process" "POST" "$sell_task"
        ;;
    6)
        echo -e "\n${YELLOW}=== RAG Cacher 테스트 ===${NC}"
        test_endpoint "Health Check" "http://localhost:8084/health"
        test_endpoint "RAG 캐싱 실행" "http://localhost:8084/cache" "POST"
        ;;
    7)
        echo -e "\n${YELLOW}=== Command Handler 테스트 ===${NC}"
        test_endpoint "Health Check" "http://localhost:8085/health"
        test_endpoint "명령 폴링 실행" "http://localhost:8085/poll" "POST"
        ;;
    8)
        echo -e "\n${YELLOW}=== 전체 서비스 Health Check ===${NC}"
        test_endpoint "Mock KIS API" "http://localhost:9443/health"
        test_endpoint "Buy Scanner" "http://localhost:8080/health"
        test_endpoint "Buy Executor" "http://localhost:8081/health"
        test_endpoint "Price Monitor" "http://localhost:8082/health"
        test_endpoint "Sell Executor" "http://localhost:8083/health"
        test_endpoint "RAG Cacher" "http://localhost:8084/health"
        test_endpoint "Command Handler" "http://localhost:8085/health"
        ;;
    *)
        echo "잘못된 선택입니다."
        exit 1
        ;;
esac

echo ""
echo "================================================"
echo "테스트 완료"
echo "================================================"

