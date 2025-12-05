"""
services/kis-gateway/main.py - 한국투자증권 API 게이트웨이
========================================================

이 서비스는 KIS Open API 호출을 중앙화하여 관리합니다.

주요 기능:
---------
1. API 토큰 관리: 자동 발급 및 갱신
2. Rate Limiting: Flask-Limiter (초당 10회)
3. Circuit Breaker: pybreaker (연속 실패 시 차단)
4. 요청 프록시: 모든 KIS API 호출 중계

API 엔드포인트:
-------------
- GET /health: 헬스 체크
- GET /api/token: 토큰 발급
- POST /api/order/buy: 매수 주문
- POST /api/order/sell: 매도 주문
- GET /api/stock/{code}: 종목 정보
- POST /api/market-data/snapshot: 현재가 조회
- GET /api/balance: 잔고 조회

Circuit Breaker 설정:
-------------------
- fail_max: 5 (연속 5회 실패 시 차단)
- reset_timeout: 60 (60초 후 재시도)

환경변수:
--------
- PORT: HTTP 서버 포트 (기본: 8080)
- TRADING_MODE: REAL/MOCK
- SECRETS_FILE: secrets.json 경로
"""

import os
import sys
import time
import json
import logging
from datetime import datetime
from flask import Flask, request, jsonify
from collections import deque
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from pybreaker import CircuitBreaker, CircuitBreakerError, CircuitBreakerListener
import requests

# shared 패키지 임포트
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import shared.auth as auth
from shared.kis.client import KISClient
import shared.database as database

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - [%(funcName)s] - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Flask-Limiter 설정
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Redis 연결 설정
REDIS_URL = os.getenv('REDIS_URL', 'memory://')

# KIS API 정책에 따른 Global Rate Limit 설정
# 실전: 초당 20건 (안전하게 19건으로 설정)
# 모의: 초당 2건
TRADING_MODE = os.getenv("TRADING_MODE", "MOCK")
GLOBAL_RATE_LIMIT = "19 per second" if TRADING_MODE == "REAL" else "2 per second"

logger.info(f"🚦 KIS Gateway Rate Limit 설정: {GLOBAL_RATE_LIMIT} (Mode: {TRADING_MODE})")

def get_global_key():
    """
    모든 클라이언트의 요청을 하나의 버킷으로 통합하기 위한 Key 함수
    KIS API는 '계좌(AppKey)' 단위로 제한되므로, IP가 아닌 단일 키를 사용해야 함
    """
    return "global_kis_account_limit"

limiter = Limiter(
    app=app,
    key_func=get_global_key,  # ⭐️ 중요: IP 기반이 아닌 전역 키 사용
    storage_uri=REDIS_URL,
    default_limits=["2000 per day", "500 per hour"],
    strategy="fixed-window"
)

logger.info(f"✅ Flask-Limiter 초기화 완료 (Backend: {REDIS_URL})")
logger.info(f"🛡️ 적용된 정책: 모든 요청 합산 {GLOBAL_RATE_LIMIT} 제한")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Circuit Breaker 설정 (pybreaker)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class GatewayCircuitBreakerListener(CircuitBreakerListener):
    """Circuit Breaker 상태 변경 감지 리스너"""
    
    def state_change(self, breaker, old, new):
        if new.name == 'open':
            logger.error(f"🚨 Circuit Breaker OPEN! (연속 {breaker.fail_counter}회 실패)")
            stats['circuit_breaker_trips'] += 1
        elif new.name == 'closed':
            logger.info(f"✅ Circuit Breaker CLOSED (복구 완료)")
        elif new.name == 'half_open':
            logger.info(f"⚠️ Circuit Breaker HALF-OPEN (테스트 요청 시도)")

# 500 에러 감지를 위한 예외 처리 필요
# requests.exceptions.HTTPError 등을 감지하도록 설정
kis_circuit_breaker = CircuitBreaker(
    fail_max=int(os.getenv('CIRCUIT_BREAKER_FAIL_MAX', '20')),  # 20회 연속 실패 시 OPEN
    reset_timeout=int(os.getenv('CIRCUIT_BREAKER_TIMEOUT', '60')),  # 60초 후 HALF_OPEN
    exclude=[KeyError, ValueError],  # 비즈니스 로직 오류는 Circuit Breaker에서 제외
    listeners=[GatewayCircuitBreakerListener()]
)

logger.info(f"✅ Circuit Breaker 초기화 완료 (fail_max={kis_circuit_breaker.fail_max}, reset_timeout={kis_circuit_breaker._reset_timeout}s)")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 전역 변수 및 통계
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

kis_client = None
db_pool_initialized = False

# 통계
stats = {
    'total_requests': 0,
    'successful_requests': 0,
    'failed_requests': 0,
    'rate_limited_requests': 0,
    'circuit_breaker_trips': 0,
    'request_history': deque(maxlen=100)  # 최근 100개 요청 기록
}


def initialize_kis_client():
    """KIS Client 초기화"""
    global kis_client
    
    logger.info("=== KIS Gateway 초기화 시작 ===")
    
    trading_mode = os.getenv("TRADING_MODE", "MOCK")
    logger.info(f"거래 모드: {trading_mode}")
    
    try:
        kis_client = KISClient(
            app_key=auth.get_secret(os.getenv(f"{trading_mode}_SECRET_ID_APP_KEY"), os.getenv("GCP_PROJECT_ID")),
            app_secret=auth.get_secret(os.getenv(f"{trading_mode}_SECRET_ID_APP_SECRET"), os.getenv("GCP_PROJECT_ID")),
            base_url=os.getenv(f"KIS_BASE_URL_{trading_mode}"),
            account_prefix=auth.get_secret(os.getenv(f"{trading_mode}_SECRET_ID_ACCOUNT_PREFIX"), os.getenv("GCP_PROJECT_ID")),
            account_suffix=os.getenv("KIS_ACCOUNT_SUFFIX"),
            token_file_path="/tmp/kis_token_gateway.json",
            trading_mode=trading_mode
        )
        
        # ⭐ 인증 실패 시 명확한 에러 처리
        if not kis_client.authenticate():
            logger.error("❌ KIS Client 인증 실패! 토큰을 발급받지 못했습니다.")
            logger.error("❌ APP_KEY/APP_SECRET 또는 KIS API 서버 상태를 확인하세요.")
            raise RuntimeError("KIS Client authentication failed - token not acquired")
        
        # 헤더가 제대로 설정되었는지 확인
        if kis_client.headers is None or 'Authorization' not in kis_client.headers:
            logger.error("❌ KIS Client 헤더 설정 실패! Authorization 헤더가 없습니다.")
            raise RuntimeError("KIS Client headers not properly set")
        
        logger.info("✅ KIS Client 초기화 완료 (토큰 발급 성공)")
        logger.info("=== KIS Gateway 초기화 완료 ===")
        return True
    except Exception as e:
        logger.error(f"❌ KIS Client 초기화 실패: {e}", exc_info=True)
        return False

def initialize_db_pool():
    """MariaDB 연결 풀 초기화 (일봉 데이터 DB fallback용)"""
    global db_pool_initialized
    if db_pool_initialized:
        return True

    try:
        # MariaDB 연결 정보는 secrets.json 또는 환경변수에서 로드
        # shared/db/connection.py의 init_engine()이 자동으로 처리
        from shared.db.connection import init_engine, ensure_engine_initialized
        
        ensure_engine_initialized()
        db_pool_initialized = True
        logger.info("✅ KIS Gateway DB 연결 풀 초기화 완료 (MariaDB)")
        return True
    except Exception as e:
        logger.error(f"❌ KIS Gateway DB 연결 풀 초기화 실패: {e}", exc_info=True)
        return False


def fetch_daily_prices_from_db(stock_code: str, limit: int):
    """MariaDB에서 일봉 데이터를 조회 (Fallback 용도)"""
    if not initialize_db_pool():
        return None

    try:
        with database.get_db_connection_context() as conn:
            df = database.get_daily_prices(conn, stock_code, limit=limit, table_name="STOCK_DAILY_PRICES_3Y")

        if df is None or df.empty:
            logger.warning(f"⚠️ [Gateway] DB 일봉 데이터 없음 ({stock_code})")
            return None

        records = []
        for _, row in df.iterrows():
            price_date = row['PRICE_DATE']
            if hasattr(price_date, 'strftime'):
                date_str = price_date.strftime('%Y-%m-%d')
            else:
                date_str = str(price_date)
            records.append({
                "date": date_str,
                "open": float(row.get('OPEN_PRICE', 0)),
                "close": float(row.get('CLOSE_PRICE', 0)),
                "high": float(row.get('HIGH_PRICE', 0)),
                "low": float(row.get('LOW_PRICE', 0)),
                "volume": float(row.get('VOLUME', 0)),
            })
        logger.info(f"📈 [Gateway] DB Fallback 일봉 데이터 {len(records)}건 반환 ({stock_code})")
        return records
    except Exception as e:
        logger.error(f"❌ [Gateway] DB 일봉 데이터 조회 실패 ({stock_code}): {e}", exc_info=True)
        return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# KIS API 호출 래퍼 (Circuit Breaker 적용)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def call_kis_api_with_breaker(api_func, *args, **kwargs):
    """
    Circuit Breaker를 적용한 KIS API 호출 래퍼
    
    - 예외 발생 시: Circuit Breaker에서 failure로 카운트
    - None 반환 시: 정상 처리 (failure로 카운트 안 함)
    """
    # pybreaker는 call() 메서드를 사용
    # 수정: KISClient의 메서드들은 requests.Response 객체나 딕셔너리를 반환할 수 있음.
    # API 에러(500 등) 발생 시 requests.exceptions.HTTPError가 발생해야 Circuit Breaker가 감지함.
    # KISClient 내부에서 에러를 삼키고 None이나 에러 메시지를 담은 dict를 리턴하면 안됨.
    # 하지만 현재 구조상 KISClient는 에러 로깅 후 None 등을 리턴할 수 있음.
    # 따라서 여기서 결과를 확인하고 에러면 예외를 발생시켜야 함.
    
    try:
        result = kis_circuit_breaker.call(api_func, *args, **kwargs)
        
        # 결과 검증 (KISClient가 에러를 dict로 리턴하는 경우 체크)
        if isinstance(result, dict) and ('rt_cd' in result and result['rt_cd'] != '0'):
             # rt_cd '0'이 성공, 그 외는 실패 (단, 모의투자는 다를 수 있음)
             # 여기서는 명확한 500 에러 등을 잡아야 함.
             pass

        return result
    except Exception as e:
        # Circuit Breaker가 이미 잡았을 것임
        raise e


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Health Check
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@limiter.exempt
@app.route('/health', methods=['GET'])
def health():
    """Health Check"""
    return jsonify({
        "status": "ok",
        "service": "kis-gateway",
        "circuit_breaker": {
            "state": kis_circuit_breaker.current_state,
            "fail_counter": kis_circuit_breaker.fail_counter,
            "fail_max": kis_circuit_breaker.fail_max
        },
        "rate_limiter": {
            "backend": REDIS_URL,
            "limits": "3 per second (per endpoint)"
        },
        "stats": {
            "total_requests": stats['total_requests'],
            "successful_requests": stats['successful_requests'],
            "failed_requests": stats['failed_requests'],
            "success_rate": f"{(stats['successful_requests'] / max(stats['total_requests'], 1) * 100):.1f}%"
        }
    }), 200


@limiter.exempt
@app.route('/api/token', methods=['POST'])
def issue_token():
    """공유 토큰 발급 API (다른 서비스가 Gateway를 통해 토큰을 재사용하도록)"""
    if not kis_client:
        return jsonify({"error": "KIS client not initialized"}), 503

    data = request.get_json(silent=True) or {}
    force_new = bool(data.get("force_new"))

    access_token = kis_client.auth.get_access_token(force_new=force_new)
    if not access_token:
        return jsonify({"error": "Failed to acquire access token"}), 500

    expires_at = None
    issued_at = None
    try:
        if os.path.exists(kis_client.TOKEN_FILE_PATH):
            with open(kis_client.TOKEN_FILE_PATH, "r") as f:
                token_data = json.load(f)
                expires_at = token_data.get("expires_at")
                issued_at = token_data.get("issued_at")
    except Exception as e:
        logger.warning(f"⚠️ 토큰 파일 읽기 실패: {e}")

    return jsonify({
        "access_token": access_token,
        "expires_at": expires_at,
        "issued_at": issued_at,
        "mode": TRADING_MODE,
    }), 200


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# API Endpoints (Rate Limiting + Circuit Breaker 적용)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@app.route('/api/market-data/snapshot', methods=['POST'])
@limiter.limit(GLOBAL_RATE_LIMIT)
def get_snapshot():
    """주식 현재가 조회 (Proxy)"""
    start_time = time.time()
    stats['total_requests'] += 1
    
    try:
        # 요청 파라미터 추출
        data = request.get_json() or {}
        stock_code = data.get('stock_code')
        is_index = data.get('is_index', False)
        
        if not stock_code:
            stats['failed_requests'] += 1
            return jsonify({"error": "stock_code required"}), 400
        
        # KIS API 호출 (Circuit Breaker 적용)
        logger.info(f"📊 [Gateway] Snapshot 요청: {stock_code}")
        snapshot = call_kis_api_with_breaker(
            kis_client.get_stock_snapshot, 
            stock_code, 
            is_index=is_index
        )
        
        if snapshot is None:
             raise Exception("Failed to get snapshot from KIS API")

        stats['successful_requests'] += 1
        
        response_time = time.time() - start_time
        stats['request_history'].append({
            'endpoint': '/api/market-data/snapshot',
            'timestamp': datetime.now().isoformat(),
            'response_time': response_time,
            'status': 'success',
            'stock_code': stock_code
        })
        
        return jsonify({
            "success": True,
            "data": snapshot,
            "response_time": response_time
        }), 200
            
    except CircuitBreakerError as e:
        stats['failed_requests'] += 1
        logger.error(f"🚨 Circuit Breaker OPEN: {e}")
        return jsonify({"error": "Circuit Breaker OPEN - KIS API 일시적으로 사용 불가"}), 503
        
    except Exception as e:
        stats['failed_requests'] += 1
        logger.error(f"❌ Snapshot 오류: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/api/trading/buy', methods=['POST'])
@limiter.limit(GLOBAL_RATE_LIMIT)
def place_buy_order():
    """매수 주문 (Proxy)"""
    start_time = time.time()
    stats['total_requests'] += 1
    
    try:
        # 요청 파라미터
        data = request.get_json() or {}
        stock_code = data.get('stock_code')
        quantity = data.get('quantity')
        price = data.get('price', 0)
        
        if not stock_code or not quantity:
            stats['failed_requests'] += 1
            return jsonify({"error": "stock_code and quantity required"}), 400
        
        # KIS API 호출
        logger.info(f"💰 [Gateway] 매수 주문: {stock_code} x {quantity}주")
        order_no = call_kis_api_with_breaker(
            kis_client.trading.place_buy_order,
            stock_code,
            quantity,
            price
        )
        
        if not order_no:
            raise Exception("Buy order failed")
            
        stats['successful_requests'] += 1
        
        response_time = time.time() - start_time
        stats['request_history'].append({
            'endpoint': '/api/trading/buy',
            'timestamp': datetime.now().isoformat(),
            'response_time': response_time,
            'status': 'success',
            'stock_code': stock_code,
            'quantity': quantity
        })
        
        return jsonify({
            "success": True,
            "order_no": order_no,
            "response_time": response_time
        }), 200
            
    except CircuitBreakerError as e:
        stats['failed_requests'] += 1
        logger.error(f"🚨 Circuit Breaker OPEN: {e}")
        return jsonify({"error": "Circuit Breaker OPEN - KIS API 일시적으로 사용 불가"}), 503
        
    except Exception as e:
        stats['failed_requests'] += 1
        logger.error(f"❌ 매수 주문 오류: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/api/trading/sell', methods=['POST'])
@limiter.limit(GLOBAL_RATE_LIMIT)
def place_sell_order():
    """매도 주문 (Proxy)"""
    start_time = time.time()
    stats['total_requests'] += 1
    
    try:
        # 요청 파라미터
        data = request.get_json() or {}
        stock_code = data.get('stock_code')
        quantity = data.get('quantity')
        price = data.get('price', 0)
        
        if not stock_code or not quantity:
            stats['failed_requests'] += 1
            return jsonify({"error": "stock_code and quantity required"}), 400
        
        # KIS API 호출
        logger.info(f"💸 [Gateway] 매도 주문: {stock_code} x {quantity}주")
        order_no = call_kis_api_with_breaker(
            kis_client.trading.place_sell_order,
            stock_code,
            quantity,
            price
        )
        
        if not order_no:
            raise Exception("Sell order failed")

        stats['successful_requests'] += 1
        
        response_time = time.time() - start_time
        stats['request_history'].append({
            'endpoint': '/api/trading/sell',
            'timestamp': datetime.now().isoformat(),
            'response_time': response_time,
            'status': 'success',
            'stock_code': stock_code,
            'quantity': quantity
        })
        
        return jsonify({
            "success": True,
            "order_no": order_no,
            "response_time": response_time
        }), 200
            
    except CircuitBreakerError as e:
        stats['failed_requests'] += 1
        logger.error(f"🚨 Circuit Breaker OPEN: {e}")
        return jsonify({"error": "Circuit Breaker OPEN - KIS API 일시적으로 사용 불가"}), 503
        
    except Exception as e:
        stats['failed_requests'] += 1
        logger.error(f"❌ 매도 주문 오류: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/api/market-data/daily-prices', methods=['POST'])
@limiter.limit(GLOBAL_RATE_LIMIT)
def get_daily_prices():
    """일봉 데이터 조회 (Proxy)"""
    start_time = time.time()
    stats['total_requests'] += 1
    
    try:
        # 요청 파라미터
        data = request.get_json() or {}
        stock_code = data.get('stock_code')
        num_days_to_fetch = data.get('num_days_to_fetch', 30)
        
        if not stock_code:
            stats['failed_requests'] += 1
            return jsonify({"error": "stock_code required"}), 400
        
        logger.info(f"📈 [Gateway] Daily Prices 요청: {stock_code} ({num_days_to_fetch}일)")

        use_db_only = stock_code == "0001" or num_days_to_fetch > 30
        daily_prices = None

        if not use_db_only:
            try:
                daily_prices = call_kis_api_with_breaker(
                    kis_client.get_stock_daily_prices,
                    stock_code,
                    num_days_to_fetch=num_days_to_fetch
                )
                if daily_prices in (None, []) or (hasattr(daily_prices, 'empty') and daily_prices.empty):
                    daily_prices = None
            except Exception as api_error:
                logger.warning(f"⚠️ KIS API 일봉 조회 실패, DB Fallback 시도 ({stock_code}): {api_error}")
                daily_prices = None

        if daily_prices is None:
            daily_prices = fetch_daily_prices_from_db(stock_code, num_days_to_fetch)

        if daily_prices is None:
            raise Exception("Failed to fetch daily prices")

        stats['successful_requests'] += 1
        
        response_time = time.time() - start_time
        stats['request_history'].append({
            'endpoint': '/api/market-data/daily-prices',
            'timestamp': datetime.now().isoformat(),
            'response_time': response_time,
            'status': 'success',
            'stock_code': stock_code
        })
        
        normalized_data = daily_prices.to_dict('records') if hasattr(daily_prices, 'to_dict') else daily_prices
        
        return jsonify({
            "success": True,
            "data": normalized_data,
            "response_time": response_time
        }), 200
            
    except CircuitBreakerError as e:
        stats['failed_requests'] += 1
        logger.error(f"🚨 Circuit Breaker OPEN: {e}")
        return jsonify({"error": "Circuit Breaker OPEN - KIS API 일시적으로 사용 불가"}), 503
        
    except Exception as e:
        stats['failed_requests'] += 1
        logger.error(f"❌ Daily Prices 오류: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/api/account/balance', methods=['POST'])
@limiter.limit(GLOBAL_RATE_LIMIT)
def get_account_balance():
    """계좌 잔고 조회 (Proxy)"""
    start_time = time.time()
    stats['total_requests'] += 1
    
    try:
        # KIS API 호출
        logger.info(f"💰 [Gateway] Account Balance 요청")
        balance = call_kis_api_with_breaker(
            kis_client.trading.get_account_balance
        )
        
        if balance is None:
            raise Exception("Failed to fetch account balance")

        stats['successful_requests'] += 1
        
        response_time = time.time() - start_time
        stats['request_history'].append({
            'endpoint': '/api/account/balance',
            'timestamp': datetime.now().isoformat(),
            'response_time': response_time,
            'status': 'success'
        })
        
        return jsonify({
            "success": True,
            "data": balance,
            "response_time": response_time
        }), 200
            
    except CircuitBreakerError as e:
        stats['failed_requests'] += 1
        logger.error(f"🚨 Circuit Breaker OPEN: {e}")
        return jsonify({"error": "Circuit Breaker OPEN - KIS API 일시적으로 사용 불가"}), 503
        
    except Exception as e:
        stats['failed_requests'] += 1
        logger.error(f"❌ Account Balance 오류: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/api/account/cash-balance', methods=['POST'])
@limiter.limit(GLOBAL_RATE_LIMIT)
def get_cash_balance():
    """현금 잔고 조회 (Proxy)"""
    start_time = time.time()
    stats['total_requests'] += 1
    
    try:
        # KIS API 호출
        logger.info(f"💰 [Gateway] Cash Balance 요청")
        balance = call_kis_api_with_breaker(
            kis_client.trading.get_cash_balance
        )
        
        # 0일 수 있으므로 None 체크만
        if balance is None:
             raise Exception("Failed to fetch cash balance")

        stats['successful_requests'] += 1
        
        response_time = time.time() - start_time
        stats['request_history'].append({
            'endpoint': '/api/account/cash-balance',
            'timestamp': datetime.now().isoformat(),
            'response_time': response_time,
            'status': 'success'
        })
        
        return jsonify({
            "success": True,
            "data": balance,
            "response_time": response_time
        }), 200
            
    except CircuitBreakerError as e:
        stats['failed_requests'] += 1
        logger.error(f"🚨 Circuit Breaker OPEN: {e}")
        return jsonify({"error": "Circuit Breaker OPEN - KIS API 일시적으로 사용 불가"}), 503
        
    except Exception as e:
        stats['failed_requests'] += 1
        logger.error(f"❌ Cash Balance 오류: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 통계 API
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@app.route('/stats', methods=['GET'])
def get_stats():
    """통계 조회"""
    return jsonify({
        "circuit_breaker": {
            "state": kis_circuit_breaker.current_state,
            "fail_counter": kis_circuit_breaker.fail_counter,
            "fail_max": kis_circuit_breaker.fail_max,
            "reset_timeout": kis_circuit_breaker._reset_timeout,
            "trips": stats['circuit_breaker_trips']
        },
        "rate_limiting": {
            "backend": REDIS_URL,
            "limit_per_endpoint": "3 per second"
        },
        "requests": {
            "total": stats['total_requests'],
            "successful": stats['successful_requests'],
            "failed": stats['failed_requests'],
            "rate_limited": stats['rate_limited_requests'],
            "success_rate": f"{(stats['successful_requests'] / max(stats['total_requests'], 1) * 100):.1f}%"
        },
        "recent_requests": list(stats['request_history'])[-10:]  # 최근 10개
    }), 200


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Rate Limit 초과 핸들러
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@app.errorhandler(429)
def rate_limit_handler(e):
    """Rate Limit 초과 시 응답"""
    stats['rate_limited_requests'] += 1
    # 로드 밸런서 IP가 아닌 실제 IP 로깅 (정보용)
    client_ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    
    logger.warning(f"⏳ Rate Limit 초과: {request.path} (Client: {client_ip}, Limit: {GLOBAL_RATE_LIMIT})")
    return jsonify({
        "error": "Rate limit exceeded",
        "message": f"KIS API 정책에 따라 {GLOBAL_RATE_LIMIT} 제한을 초과했습니다. 잠시 후 다시 시도해주세요.",
        "retry_after": e.description
    }), 429


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 초기화
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# 모듈 로드 시 초기화
if kis_client is None and os.getenv('WERKZEUG_RUN_MAIN') != 'true':
    logger.info("모듈 로드 시 KIS Gateway 초기화")
    if not initialize_kis_client():
        logger.critical("KIS Gateway 초기화 실패")
        raise RuntimeError("KIS Gateway initialization failed")
    initialize_db_pool()

if __name__ == '__main__':
    if kis_client is None:
        if not initialize_kis_client():
            logger.critical("KIS Gateway 초기화 실패로 종료합니다.")
            sys.exit(1)
    initialize_db_pool()
    
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=False)
