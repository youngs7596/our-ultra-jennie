#!/bin/bash
# demo_full_flow.sh
# 전체 매수-매도 플로우 데모 스크립트
# 작업 LLM: Auto (Claude Sonnet 4.5)

set -e

# 색상 코드
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}================================================${NC}"
echo -e "${BLUE}My Supreme Jennie - 전체 플로우 데모${NC}"
echo -e "${BLUE}================================================${NC}"

# 헬퍼 함수
wait_and_log() {
    local seconds=$1
    local message=$2
    echo -e "\n${YELLOW}⏳ $message ($seconds초 대기)${NC}"
    sleep $seconds
}

api_call() {
    local name=$1
    local url=$2
    local method=${3:-GET}
    local data=${4:-}
    
    echo -e "\n${BLUE}[단계]${NC} $name"
    
    if [ "$method" = "GET" ]; then
        response=$(curl -s -w "\n%{http_code}" "$url" 2>/dev/null)
    else
        response=$(curl -s -w "\n%{http_code}" -X "$method" -H "Content-Type: application/json" -d "$data" "$url" 2>/dev/null)
    fi
    
    http_code=$(echo "$response" | tail -n1)
    body=$(echo "$response" | sed '$d')
    
    if [ "$http_code" -eq 200 ]; then
        echo -e "${GREEN}✅ 성공${NC}"
        echo "$body" | jq '.' 2>/dev/null || echo "$body"
    else
        echo -e "${RED}❌ 실패 (HTTP $http_code)${NC}"
        echo "$body"
        return 1
    fi
}

# 서비스 상태 확인
check_services() {
    echo -e "\n${YELLOW}=== 1. 서비스 상태 확인 ===${NC}"
    
    services=(
        "Mock KIS API:http://localhost:9443/health"
        "Buy Scanner:http://localhost:8081/health"
        "Buy Executor:http://localhost:8082/health"
        "Price Monitor:http://localhost:8083/health"
        "Sell Executor:http://localhost:8084/health"
    )
    
    all_healthy=true
    
    for service in "${services[@]}"; do
        IFS=':' read -r name url <<< "$service"
        echo -n "  $name... "
        
        if curl -s "$url" > /dev/null 2>&1; then
            echo -e "${GREEN}✅${NC}"
        else
            echo -e "${RED}❌ (서비스가 실행 중이지 않습니다)${NC}"
            all_healthy=false
        fi
    done
    
    if [ "$all_healthy" = false ]; then
        echo -e "\n${RED}❌ 일부 서비스가 실행 중이지 않습니다.${NC}"
        echo -e "${YELLOW}다음 명령으로 서비스를 시작하세요:${NC}"
        echo "  ./scripts/test_service.sh <service_name>"
        exit 1
    fi
    
    echo -e "\n${GREEN}✅ 모든 서비스가 정상 실행 중입니다!${NC}"
}

# 매수 플로우
buy_flow() {
    echo -e "\n${YELLOW}=== 2. 매수 플로우 ===${NC}"
    
    # 2-1. Buy Scanner 트리거
    api_call "2-1. Buy Scanner 실행 (매수 후보 탐지)" \
        "http://localhost:8081/scan" \
        "POST"
    
    wait_and_log 2 "매수 신호 생성 대기"
    
    # 2-2. Buy Executor에 신호 전달 (시뮬레이션)
    echo -e "\n${BLUE}[단계]${NC} 2-2. Buy Executor에 매수 신호 전달"
    
    # 샘플 종목 코드 (삼성전자)
    STOCK_CODE="005930"
    STOCK_NAME="삼성전자"
    
    # Base64 인코딩된 메시지
    message_json="{\"stock_code\": \"$STOCK_CODE\", \"stock_name\": \"$STOCK_NAME\", \"score\": 85.5, \"timestamp\": \"$(date -u +%Y-%m-%dT%H:%M:%S)\"}"
    base64_data=$(echo -n "$message_json" | base64)
    
    pubsub_message="{\"message\": {\"data\": \"$base64_data\", \"messageId\": \"demo-msg-$(date +%s)\", \"publishTime\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}}"
    
    api_call "Pub/Sub 메시지 처리" \
        "http://localhost:8082/process" \
        "POST" \
        "$pubsub_message"
    
    wait_and_log 3 "매수 주문 체결 확인"
    
    # Mock Orders 확인
    api_call "2-3. Mock Orders 확인" \
        "http://localhost:9443/mock/orders"
}

# 매도 플로우
sell_flow() {
    echo -e "\n${YELLOW}=== 3. 매도 플로우 ===${NC}"
    
    # 3-1. Price Monitor 시작
    api_call "3-1. Price Monitor 시작 (실시간 감시)" \
        "http://localhost:8083/start" \
        "POST"
    
    wait_and_log 5 "가격 변동 감지 대기"
    
    # 3-2. Price Monitor 중지
    api_call "3-2. Price Monitor 중지" \
        "http://localhost:8083/stop" \
        "POST"
    
    # 3-3. Sell Executor에 매도 신호 전달 (시뮬레이션)
    echo -e "\n${BLUE}[단계]${NC} 3-3. Sell Executor에 매도 신호 전달"
    
    STOCK_CODE="005930"
    STOCK_NAME="삼성전자"
    
    sell_task="{\"stock_code\": \"$STOCK_CODE\", \"stock_name\": \"$STOCK_NAME\", \"quantity\": 10, \"current_price\": 77000, \"reason\": \"TAKE_PROFIT\", \"timestamp\": \"$(date -u +%Y-%m-%dT%H:%M:%S)\"}"
    
    api_call "매도 주문 처리" \
        "http://localhost:8084/process" \
        "POST" \
        "$sell_task"
    
    wait_and_log 2 "매도 주문 체결 확인"
    
    # Mock Orders 최종 확인
    api_call "3-4. Mock Orders 최종 확인" \
        "http://localhost:9443/mock/orders"
}

# 결과 요약
summary() {
    echo -e "\n${YELLOW}=== 4. 결과 요약 ===${NC}"
    
    echo -e "\n${GREEN}✅ 전체 플로우 완료!${NC}"
    echo ""
    echo "실행된 단계:"
    echo "  1. ✅ 서비스 상태 확인"
    echo "  2. ✅ 매수 플로우 (Buy Scanner → Buy Executor)"
    echo "  3. ✅ 매도 플로우 (Price Monitor → Sell Executor)"
    echo ""
    echo "다음 확인사항:"
    echo "  - Mock Orders: http://localhost:9443/mock/orders"
    echo "  - DB 포트폴리오: SELECT * FROM PORTFOLIO_MOCK;"
    echo "  - DB 거래 로그: SELECT * FROM TRADE_LOG_MOCK ORDER BY CREATED_AT DESC;"
    echo ""
    echo "추가 테스트:"
    echo "  - RAG Cacher: ./scripts/test_api_calls.sh (메뉴 6)"
    echo "  - Command Handler: ./scripts/test_api_calls.sh (메뉴 7)"
}

# 메인 실행
main() {
    check_services
    buy_flow
    sell_flow
    summary
}

# 실행 확인
echo ""
read -p "전체 플로우 데모를 시작하시겠습니까? (y/n): " confirm

if [ "$confirm" != "y" ]; then
    echo "취소되었습니다."
    exit 0
fi

main

echo -e "\n${BLUE}================================================${NC}"
echo -e "${BLUE}데모 완료${NC}"
echo -e "${BLUE}================================================${NC}"

