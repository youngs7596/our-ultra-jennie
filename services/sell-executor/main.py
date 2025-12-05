"""
services/sell-executor/main.py - 매도 실행 서비스
===============================================

이 서비스는 매도 신호를 받아 실제 주문을 실행합니다.

주요 기능:
---------
1. RabbitMQ에서 매도 신호 수신 (sell-orders 큐)
2. 보유 종목 확인 및 매도 수량 계산
3. KIS Gateway를 통한 매도 주문 실행
4. 손익 계산 및 텔레그램 알림
5. 거래 로그 기록 (TRADELOG)

입력 (RabbitMQ 메시지):
--------------------
{
    "stock_code": "005930",
    "stock_name": "삼성전자",
    "sell_reason": "PROFIT_TARGET",
    "current_price": 77000,
    "profit_pct": 10.0
}

매도 사유:
---------
- PROFIT_TARGET: 목표가 도달
- STOP_LOSS: 손절가 도달
- RSI_OVERBOUGHT: RSI 과매수
- TIME_EXIT: 보유 기간 초과

환경변수:
--------
- PORT: HTTP 서버 포트 (기본: 8083)
- TRADING_MODE: REAL/MOCK
- DRY_RUN: true면 실제 주문 미실행
- RABBITMQ_URL: RabbitMQ 연결 URL
- KIS_GATEWAY_URL: KIS Gateway URL
"""

import os
import sys
import logging
from flask import Flask, request, jsonify
from dotenv import load_dotenv

# shared 패키지 임포트 경로 설정
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import shared.auth as auth
import shared.database as database
from shared.kis.client import KISClient as KIS_API
from shared.kis.gateway_client import KISGatewayClient
from shared.config import ConfigManager
from shared.rabbitmq import RabbitMQWorker  # [변경] shared 모듈 사용

from executor import SellExecutor

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - [%(funcName)s] - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# 전역 변수
executor = None
rabbitmq_worker = None


def _process_sell_request(sell_request, request_source: str = "http") -> dict:
    if not executor:
        raise RuntimeError("Service not initialized")

    if not sell_request:
        raise ValueError("Invalid request payload")

    stock_code = sell_request.get('stock_code')
    stock_name = sell_request.get('stock_name')
    quantity = sell_request.get('quantity')
    sell_reason = sell_request.get('sell_reason', 'Unknown')

    if not all([stock_code, stock_name, quantity]):
        raise ValueError("Missing required fields")

    dry_run = os.getenv('DRY_RUN', 'true').lower() == 'true'
    if dry_run:
        logger.info("🔧 DRY_RUN 모드: 실제 주문은 실행되지 않습니다")

    logger.info(
        "[%s] 매도 요청: %s(%s) %s주, 사유: %s",
        request_source.upper(),
        stock_name,
        stock_code,
        quantity,
        sell_reason,
    )

    result = executor.execute_sell_order(
        stock_code=stock_code,
        stock_name=stock_name,
        quantity=quantity,
        sell_reason=sell_reason,
        dry_run=dry_run
    )
    return result


def _rabbitmq_handler(payload):
    try:
        result = _process_sell_request(payload, request_source="rabbitmq")
        logger.info("RabbitMQ 매도 처리 결과: %s", result.get("status"))
    except Exception as exc:
        logger.error("RabbitMQ 메시지 처리 실패: %s", exc, exc_info=True)


def _start_rabbitmq_worker_if_needed():
    global rabbitmq_worker
    use_rabbitmq = os.getenv("USE_RABBITMQ", "false").lower() == "true"
    if not use_rabbitmq:
        return
    if rabbitmq_worker and rabbitmq_worker._thread and rabbitmq_worker._thread.is_alive():
        return
    amqp_url = os.getenv("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/")
    queue_name = os.getenv("RABBITMQ_QUEUE_SELL_ORDERS", "sell-orders")
    
    # shared.rabbitmq.RabbitMQWorker 사용
    rabbitmq_worker = RabbitMQWorker(amqp_url=amqp_url, queue_name=queue_name, handler=_rabbitmq_handler)
    rabbitmq_worker.start()


def initialize_service():
    """서비스 초기화"""
    global executor
    
    logger.info("=== Sell Executor Service 초기화 시작 ===")
    load_dotenv()
    
    try:
        # 1. DB Connection Pool 초기화 (SQLAlchemy 사용)
        from shared.db.connection import ensure_engine_initialized
        logger.info("🔧 DB Connection 초기화 중...")
        ensure_engine_initialized()
        logger.info("✅ DB Connection 초기화 완료")
        
        # 2. KIS API 초기화
        trading_mode = os.getenv("TRADING_MODE", "MOCK")
        use_gateway = os.getenv("USE_KIS_GATEWAY", "true").lower() == "true"
        logger.info(f"거래 모드: {trading_mode}, Gateway 사용: {use_gateway}")
        
        if use_gateway:
            kis = KISGatewayClient()
            logger.info("✅ KIS Gateway Client 초기화 완료")
        else:
            kis = KIS_API(
                app_key=auth.get_secret(os.getenv(f"{trading_mode}_SECRET_ID_APP_KEY")),
                app_secret=auth.get_secret(os.getenv(f"{trading_mode}_SECRET_ID_APP_SECRET")),
                base_url=os.getenv(f"KIS_BASE_URL_{trading_mode}"),
                account_prefix=auth.get_secret(os.getenv(f"{trading_mode}_SECRET_ID_ACCOUNT_PREFIX")),
                account_suffix=os.getenv("KIS_ACCOUNT_SUFFIX"),
                token_file_path="/tmp/kis_token_sell_executor.json",
                trading_mode=trading_mode
            )
            kis.authenticate()
            logger.info("✅ KIS API 초기화 완료")
        
        # 3. ConfigManager 초기화
        config_manager = ConfigManager(db_conn=None, cache_ttl=300)
        
        # 4. Telegram Bot 초기화
        try:
            telegram_token = auth.get_secret("telegram_bot_token")
            telegram_chat_id = auth.get_secret("telegram_chat_id")
        except Exception:
            logger.warning("텔레그램 Secret 로드 실패, 환경변수 사용")
            telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
            telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        
        from shared.notification import TelegramBot
        telegram_bot = TelegramBot(token=telegram_token, chat_id=telegram_chat_id)
        
        # 5. Gemini API 초기화
        gemini_api_key = auth.get_secret(os.getenv("SECRET_ID_GEMINI_API_KEY"))
        
        # 6. Sell Executor 초기화
        executor = SellExecutor(kis=kis, config=config_manager, telegram_bot=telegram_bot)
        logger.info("✅ Sell Executor 초기화 완료")
        
        logger.info("=== Sell Executor Service 초기화 완료 ===")

        _start_rabbitmq_worker_if_needed()

        return True
        
    except Exception as e:
        logger.critical(f"❌ 초기화 실패: {e}", exc_info=True)
        return False


@app.route('/health', methods=['GET'])
def health_check():
    if executor:
        return jsonify({"status": "ok", "service": "sell-executor"}), 200
    else:
        return jsonify({"status": "initializing"}), 503


@app.route('/execute', methods=['POST'])
def execute():
    """
    Cloud Tasks 또는 기타 HTTP 호출을 통한 매도 요청 처리
    """
    try:
        sell_request = request.get_json(silent=True)
        result = _process_sell_request(sell_request, request_source="http")
        status_code = 200 if result.get("status") == "success" else 200
        return jsonify(result), status_code
    except ValueError as err:
        logger.error("잘못된 요청: %s", err)
        return jsonify({"error": str(err)}), 400
    except Exception as e:
        logger.error(f"❌ /execute 처리 중 오류: {e}", exc_info=True)
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route('/', methods=['GET'])
def root():
    return jsonify({
        "service": "sell-executor",
        "version": "1.0",
        "trading_mode": os.getenv("TRADING_MODE", "MOCK"),
        "dry_run": os.getenv("DRY_RUN", "true")
    }), 200


if executor is None:
    logger.info("모듈 로드 시 서비스 초기화 시작")
    if not initialize_service():
        logger.critical("서비스 초기화 실패")
        raise RuntimeError("Service initialization failed")

if __name__ == '__main__':
    if executor is None:
        if not initialize_service():
            sys.exit(1)
    else:
        _start_rabbitmq_worker_if_needed()
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=False)
