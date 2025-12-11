# services/command-handler/main.py
# Version: v3.6
# Command Handler Service - Telegram 명령 폴링 서비스

import os
import sys
import time
import logging
import threading
from flask import Flask, request, jsonify
from dotenv import load_dotenv

# shared 패키지 임포트 경로 설정
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import shared.auth as auth
import shared.database as database
from shared.db.connection import ensure_engine_initialized
from shared.kis.client import KISClient as KIS_API
from shared.kis.gateway_client import KISGatewayClient
from shared.config import ConfigManager
from shared.notification import TelegramBot
from shared.rabbitmq import RabbitMQPublisher

from handler import CommandHandler

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - [%(funcName)s] - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# 전역 변수
command_handler = None
telegram_bot = None
polling_thread = None
is_polling = False
buy_publisher = None
sell_publisher = None


def initialize_service():
    """서비스 초기화"""
    global command_handler, telegram_bot, buy_publisher, sell_publisher
    
    logger.info("=== Command Handler Service 초기화 시작 ===")
    load_dotenv()
    
    try:
        # 1. DB Connection Pool 초기화
        if not database.is_pool_initialized():
            logger.info("🔧 DB Connection Pool 초기화 중...")
            db_user = auth.get_secret(
                os.getenv("SECRET_ID_ORACLE_DB_USER") or "mariadb-user",
                os.getenv("GCP_PROJECT_ID"),
                use_cache=True
            )
            db_password = auth.get_secret(
                os.getenv("SECRET_ID_ORACLE_DB_PASSWORD") or "mariadb-password",
                os.getenv("GCP_PROJECT_ID"),
                use_cache=True
            )
            db_service_name = os.getenv("OCI_DB_SERVICE_NAME")
            wallet_path = os.getenv("OCI_WALLET_DIR_NAME", "wallet")
            
            if not wallet_path.startswith('/'):
                wallet_path = f"/app/{wallet_path}"
            
            database.init_connection_pool(
                db_user=db_user,
                db_password=db_password,
                db_service_name=db_service_name,
                wallet_path=wallet_path,
                min_sessions=1,
                max_sessions=5,
                increment=1
            )
            logger.info("✅ DB Connection Pool 초기화 완료")
        else:
            logger.info("✅ DB Connection Pool 이미 초기화됨")
        
        # 1.5. SQLAlchemy 엔진 초기화 (session_scope 사용을 위해 필수)
        try:
            ensure_engine_initialized()
            logger.info("✅ SQLAlchemy 엔진 초기화 완료")
        except Exception as e:
            logger.warning(f"⚠️ SQLAlchemy 엔진 초기화 실패: {e}")
        
        # 2. KIS API 초기화
        trading_mode = os.getenv("TRADING_MODE", "MOCK")
        use_gateway = os.getenv("USE_KIS_GATEWAY", "true").lower() == "true"
        logger.info(f"거래 모드: {trading_mode}, Gateway 사용: {use_gateway}")
        
        if use_gateway:
            kis = KISGatewayClient()
            logger.info("✅ KIS Gateway Client 초기화 완료")
        else:
            kis = KIS_API(
                app_key=auth.get_secret(os.getenv(f"{trading_mode}_SECRET_ID_APP_KEY"), os.getenv("GCP_PROJECT_ID")),
                app_secret=auth.get_secret(os.getenv(f"{trading_mode}_SECRET_ID_APP_SECRET"), os.getenv("GCP_PROJECT_ID")),
                base_url=os.getenv(f"KIS_BASE_URL_{trading_mode}"),
                account_prefix=auth.get_secret(os.getenv(f"{trading_mode}_SECRET_ID_ACCOUNT_PREFIX"), os.getenv("GCP_PROJECT_ID")),
                account_suffix=os.getenv("KIS_ACCOUNT_SUFFIX"),
                token_file_path="/tmp/kis_token_command_handler.json",
                trading_mode=trading_mode
            )
            kis.authenticate()
            logger.info("✅ KIS API 초기화 완료")
        
        # 3. ConfigManager 초기화
        config_manager = ConfigManager(db_conn=None, cache_ttl=300)
        logger.info("✅ ConfigManager 초기화 완료")
        
        # 4. Telegram Bot 초기화
        telegram_token = auth.get_secret("telegram_bot_token") if auth.get_secret("telegram_bot_token") else os.getenv("TELEGRAM_BOT_TOKEN")
        telegram_chat_id = auth.get_secret("telegram_chat_id") if auth.get_secret("telegram_chat_id") else os.getenv("TELEGRAM_CHAT_ID")
        
        telegram_bot = TelegramBot(
            token=telegram_token,
            chat_id=telegram_chat_id
        )
        logger.info("✅ Telegram Bot 초기화 완료")
        
        # 5. RabbitMQ Publisher 초기화
        amqp_url = os.getenv("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/")
        buy_queue = os.getenv("RABBITMQ_QUEUE_BUY_SIGNALS", "buy-signals")
        sell_queue = os.getenv("RABBITMQ_QUEUE_SELL_ORDERS", "sell-orders")
        
        buy_publisher = RabbitMQPublisher(amqp_url=amqp_url, queue_name=buy_queue)
        sell_publisher = RabbitMQPublisher(amqp_url=amqp_url, queue_name=sell_queue)
        logger.info("✅ RabbitMQ Publisher 초기화 완료 (buy=%s, sell=%s)", buy_queue, sell_queue)
        
        # 6. Command Handler 초기화
        command_handler = CommandHandler(
            kis=kis, 
            config=config_manager, 
            telegram_bot=telegram_bot,
            buy_publisher=buy_publisher,
            sell_publisher=sell_publisher
        )
        logger.info("✅ Command Handler 초기화 완료")
        
        logger.info("=== Command Handler Service 초기화 완료 ===")
        return True
        
    except Exception as e:
        logger.critical(f"❌ 초기화 실패: {e}", exc_info=True)
        return False


def polling_loop():
    """Telegram 명령 폴링 루프"""
    global is_polling
    
    logger.info("🚀 Telegram 명령 폴링 시작")
    
    poll_interval = int(os.getenv("POLL_INTERVAL_SECONDS", "5"))
    dry_run = os.getenv('DRY_RUN', 'true').lower() == 'true'
    
    while is_polling:
        try:
            if command_handler:
                result = command_handler.poll_and_process(dry_run=dry_run)
                
                if result.get('processed_count', 0) > 0:
                    logger.info(f"✅ {result['processed_count']}개 명령 처리 완료")
            
        except Exception as e:
            logger.error(f"❌ 폴링 중 오류: {e}")
        
        time.sleep(poll_interval)
    
    logger.info("🛑 Telegram 명령 폴링 종료")


def start_polling():
    """폴링 스레드 시작"""
    global polling_thread, is_polling
    
    if is_polling:
        logger.warning("이미 폴링 중입니다.")
        return
    
    is_polling = True
    polling_thread = threading.Thread(target=polling_loop, daemon=True)
    polling_thread.start()
    logger.info("✅ 폴링 스레드 시작됨")


def stop_polling():
    """폴링 스레드 중지"""
    global is_polling
    is_polling = False
    logger.info("폴링 중지 요청됨")


@app.route('/health', methods=['GET'])
def health_check():
    """헬스 체크"""
    if command_handler:
        return jsonify({
            "status": "ok", 
            "service": "command-handler",
            "polling": is_polling
        }), 200
    else:
        return jsonify({"status": "initializing"}), 503


@app.route('/poll', methods=['POST'])
def poll_commands():
    """명령 폴링 및 처리 (수동 호출용)"""
    try:
        logger.info("=== /poll 엔드포인트 호출 ===")
        
        if not command_handler:
            logger.error("서비스가 초기화되지 않았습니다")
            return jsonify({"error": "Service not initialized"}), 503
        
        dry_run = os.getenv('DRY_RUN', 'true').lower() == 'true'
        result = command_handler.poll_and_process(dry_run=dry_run)
        
        if result['processed_count'] > 0:
            logger.info(f"✅ {result['processed_count']}개 명령 처리 완료")
        else:
            logger.debug("대기 중인 명령 없음")
        
        return jsonify(result), 200
        
    except Exception as e:
        logger.error(f"❌ /poll 처리 중 오류: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/start', methods=['POST'])
def start_polling_endpoint():
    """폴링 시작"""
    start_polling()
    return jsonify({"status": "polling started"}), 200


@app.route('/stop', methods=['POST'])
def stop_polling_endpoint():
    """폴링 중지"""
    stop_polling()
    return jsonify({"status": "polling stopped"}), 200


@app.route('/', methods=['GET'])
def root():
    """루트 엔드포인트"""
    return jsonify({
        "service": "command-handler",
        "version": "v3.6",
        "trading_mode": os.getenv("TRADING_MODE", "MOCK"),
        "dry_run": os.getenv("DRY_RUN", "true"),
        "polling": is_polling
    }), 200


# 서비스 초기화
if command_handler is None and os.getenv('WERKZEUG_RUN_MAIN') != 'true':
    logger.info("모듈 로드 시 서비스 초기화 시작")
    if not initialize_service():
        logger.critical("서비스 초기화 실패")
        raise RuntimeError("Service initialization failed")
    
    # 자동 폴링 시작 (환경변수로 제어 가능)
    auto_start = os.getenv('AUTO_START_POLLING', 'true').lower() == 'true'
    if auto_start:
        start_polling()

if __name__ == '__main__':
    if command_handler is None:
        if not initialize_service():
            logger.critical("서비스 초기화 실패로 종료합니다.")
            sys.exit(1)
    
    auto_start = os.getenv('AUTO_START_POLLING', 'true').lower() == 'true'
    if auto_start:
        start_polling()
    
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=False)
