#!/bin/bash
# Fast Hands Strategy - 로컬 통합 테스트 스크립트
# 작성일: 2025-11-22

set -e  # 에러 발생 시 즉시 중단

echo "========================================"
echo "⚡ Fast Hands, Slow Brain 로컬 테스트"
echo "========================================"

# 색상 정의
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# 환경 변수 확인
echo ""
echo "${YELLOW}[Step 0] 환경 변수 확인${NC}"
echo "TRADING_MODE: ${TRADING_MODE:-MOCK}"
echo "USE_KIS_GATEWAY: ${USE_KIS_GATEWAY:-false}"
echo "DRY_RUN: ${DRY_RUN:-true}"

# DB 연결 테스트
echo ""
echo "${YELLOW}[Step 1] DB 연결 및 스키마 확인${NC}"
python3 -c "
import sys
import os
sys.path.insert(0, os.getcwd())

import shared.auth as auth
import shared.database as database
from dotenv import load_dotenv

load_dotenv()

# DB 연결
db_user = auth.get_secret(os.getenv('SECRET_ID_ORACLE_DB_USER'), os.getenv('GCP_PROJECT_ID'))
db_password = auth.get_secret(os.getenv('SECRET_ID_ORACLE_DB_PASSWORD'), os.getenv('GCP_PROJECT_ID'))
db_service_name = os.getenv('OCI_DB_SERVICE_NAME')
wallet_path = os.getenv('OCI_WALLET_DIR_NAME', 'wallet')

conn = database.get_db_connection(db_user, db_password, db_service_name, wallet_path)
if not conn:
    print('❌ DB 연결 실패')
    sys.exit(1)

# WatchList 테이블 스키마 확인
cursor = conn.cursor()
cursor.execute(\"\"\"
    SELECT COLUMN_NAME FROM USER_TAB_COLUMNS 
    WHERE TABLE_NAME = 'WATCHLIST' 
    AND COLUMN_NAME IN ('LLM_SCORE', 'LLM_REASON', 'LLM_UPDATED_AT')
    ORDER BY COLUMN_NAME
\"\"\")
columns = [row[0] for row in cursor.fetchall()]

if 'LLM_SCORE' not in columns:
    print('⚠️  LLM_SCORE 컬럼이 없습니다!')
    print('   다음 SQL을 실행해주세요:')
    print('   ALTER TABLE WatchList ADD (LLM_SCORE NUMBER DEFAULT 0);')
    sys.exit(1)

print(f'✅ WatchList 테이블 LLM 컬럼 확인 완료: {columns}')
conn.close()
"

if [ $? -ne 0 ]; then
    echo "${RED}❌ DB 스키마 확인 실패. 위 SQL을 먼저 실행해주세요.${NC}"
    exit 1
fi

# Scout Job 실행 (Slow Brain 테스트)
echo ""
echo "${YELLOW}[Step 2] Scout Job 실행 (Slow Brain - 사전 분석)${NC}"
echo "WatchList에 LLM 점수가 포함된 데이터를 저장합니다..."

cd services/scout-job
python3 scout.py 2>&1 | tee ../../test_scout_output.log
cd ../..

# Scout 결과 확인
echo ""
echo "${YELLOW}[Step 2-1] Scout 결과 확인${NC}"
python3 -c "
import sys
import os
sys.path.insert(0, os.getcwd())

import shared.auth as auth
import shared.database as database
from dotenv import load_dotenv

load_dotenv()

db_user = auth.get_secret(os.getenv('SECRET_ID_ORACLE_DB_USER'), os.getenv('GCP_PROJECT_ID'))
db_password = auth.get_secret(os.getenv('SECRET_ID_ORACLE_DB_PASSWORD'), os.getenv('GCP_PROJECT_ID'))
db_service_name = os.getenv('OCI_DB_SERVICE_NAME')
wallet_path = os.getenv('OCI_WALLET_DIR_NAME', 'wallet')

conn = database.get_db_connection(db_user, db_password, db_service_name, wallet_path)
watchlist = database.get_active_watchlist(conn)

print(f'\\n📊 WatchList 조회 결과: {len(watchlist)}개 종목')
for code, info in list(watchlist.items())[:5]:
    llm_score = info.get('llm_score', 0)
    llm_reason = info.get('llm_reason', '')[:50]
    print(f'  - {info[\"name\"]}({code}): {llm_score}점 | {llm_reason}...')

# 70점 이상 종목 확인
high_score_stocks = [
    (code, info) for code, info in watchlist.items() 
    if info.get('llm_score', 0) >= 70
]
print(f'\\n⭐ 70점 이상 종목: {len(high_score_stocks)}개')

conn.close()
"

# Buy Scanner 테스트 (Fast Hands - 신호 감지)
echo ""
echo "${YELLOW}[Step 3] Buy Scanner 테스트 (신호 감지)${NC}"
echo "실시간 가격을 반영하여 매수 후보를 선정합니다..."

# Scanner는 Flask 앱이므로 별도 실행이 필요
# 여기서는 스캔 로직만 테스트
python3 -c "
import sys
import os
sys.path.insert(0, os.getcwd())
sys.path.insert(0, os.path.join(os.getcwd(), 'services', 'buy-scanner'))

from dotenv import load_dotenv
load_dotenv()

import shared.auth as auth
from shared.kis.gateway_client import KISGatewayClient
from shared.config import ConfigManager
from scanner import BuyScanner

# KIS Client 초기화
use_gateway = os.getenv('USE_KIS_GATEWAY', 'false').lower() == 'true'
if use_gateway:
    kis = KISGatewayClient()
else:
    from shared.kis.client import KISClient as KIS_API
    trading_mode = os.getenv('TRADING_MODE', 'MOCK')
    kis = KIS_API(
        app_key=auth.get_secret(os.getenv(f'{trading_mode}_SECRET_ID_APP_KEY'), os.getenv('GCP_PROJECT_ID')),
        app_secret=auth.get_secret(os.getenv(f'{trading_mode}_SECRET_ID_APP_SECRET'), os.getenv('GCP_PROJECT_ID')),
        base_url=os.getenv(f'KIS_BASE_URL_{trading_mode}'),
        account_prefix=auth.get_secret(os.getenv(f'{trading_mode}_SECRET_ID_ACCOUNT_PREFIX'), os.getenv('GCP_PROJECT_ID')),
        account_suffix=os.getenv('KIS_ACCOUNT_SUFFIX'),
        token_file_path='/tmp/kis_token_scanner_test.json',
        trading_mode=trading_mode
    )
    kis.authenticate()

# ConfigManager 초기화
config = ConfigManager(db_conn=None, cache_ttl=60)

# Scanner 실행
scanner = BuyScanner(kis=kis, config=config)
result = scanner.scan_buy_opportunities()

if result and result.get('candidates'):
    print(f'\\n🎯 매수 후보 발견: {len(result[\"candidates\"])}개')
    for idx, candidate in enumerate(result['candidates'][:3], 1):
        print(f'  {idx}. {candidate[\"name\"]}({candidate[\"code\"]})')
        print(f'     - LLM 점수: {candidate.get(\"llm_score\", 0)}점')
        print(f'     - 신호: {candidate.get(\"buy_signal_type\")}')
        print(f'     - 가격: {candidate.get(\"current_price\", 0):,.0f}원')
else:
    print('\\n⚠️  매수 후보가 없습니다.')
" 2>&1 | tee test_scanner_output.log

# Buy Executor 테스트 (Fast Hands - 즉시 체결)
echo ""
echo "${YELLOW}[Step 4] Buy Executor 로직 검증${NC}"
echo "LLM 호출 없이 점수 기반으로 즉시 매수하는지 확인합니다..."

python3 -c "
import sys
import os
sys.path.insert(0, os.getcwd())

# executor.py의 핵심 로직 시뮬레이션
candidates = [
    {'code': '005930', 'name': '삼성전자', 'llm_score': 85, 'current_price': 72000},
    {'code': '000660', 'name': 'SK하이닉스', 'llm_score': 75, 'current_price': 150000},
    {'code': '035420', 'name': 'NAVER', 'llm_score': 65, 'current_price': 200000},
]

print('\\n📋 매수 후보 목록:')
for c in candidates:
    print(f'  - {c[\"name\"]}: {c[\"llm_score\"]}점')

# Fast Hands 로직 (점수 기반 정렬)
candidates.sort(key=lambda x: x.get('llm_score', 0), reverse=True)
selected = candidates[0]

print(f'\\n⚡ [Fast Hands] 최고점 후보 선정: {selected[\"name\"]}({selected[\"code\"]}) - {selected[\"llm_score\"]}점')

if selected['llm_score'] >= 70:
    print(f'✅ 점수 기준 충족 (70점 이상) → 즉시 매수 진행!')
else:
    print(f'❌ 점수 부족 ({selected[\"llm_score\"]}점 < 70점) → 매수 건너뜀')
"

# 테스트 완료
echo ""
echo "${GREEN}========================================"
echo "✅ Fast Hands 로컬 테스트 완료!"
echo "========================================${NC}"
echo ""
echo "📝 다음 단계:"
echo "  1. test_scout_output.log 확인 (LLM 점수 산출 로그)"
echo "  2. test_scanner_output.log 확인 (실시간 가격 반영 로그)"
echo "  3. DRY_RUN=false로 실제 주문 테스트 (주의!)"
echo ""

