# services/daily-briefing/main.py
# Version: v3.5
# Daily Briefing Service - Flask 엔트리포인트

import os
import sys
import logging
from flask import Flask, jsonify
from dotenv import load_dotenv

# shared 패키지 임포트 경로 설정
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import shared.auth as auth
import shared.database as database
from shared.kis.client import KISClient as KIS_API
from shared.kis.gateway_client import KISGatewayClient
from shared.notification import TelegramBot

from reporter import DailyReporter

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - [%(funcName)s] - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

def initialize_service():
    """서비스 초기화 및 리포트 발송"""
    logger.info("=== Daily Briefing Service 시작 ===")
    load_dotenv()
    
    try:
        # 1. DB Connection Pool 초기화 (Cloud Run 인스턴스 내에서 재사용)
        # Secret은 캐싱되므로 두 번째 호출부터는 빠르게 동작
        if not database.is_pool_initialized():
            logger.info("🔧 DB Connection Pool 초기화 중... (Secret 캐싱 활성화)")
            db_user = auth.get_secret(
                os.getenv("SECRET_ID_ORACLE_DB_USER"), 
                os.getenv("GCP_PROJECT_ID"),
                use_cache=True  # Secret 캐싱 사용
            )
            db_password = auth.get_secret(
                os.getenv("SECRET_ID_ORACLE_DB_PASSWORD"), 
                os.getenv("GCP_PROJECT_ID"),
                use_cache=True  # Secret 캐싱 사용
            )
            db_service_name = os.getenv("OCI_DB_SERVICE_NAME")
            wallet_path = os.getenv("OCI_WALLET_DIR_NAME", "wallet")
            
            # 절대 경로로 변환
            if not wallet_path.startswith('/'):
                wallet_path = f"/app/{wallet_path}"
            
            # Pool 생성 (min=1, max=5로 설정하여 성능 최적화)
            database.init_connection_pool(
                db_user=db_user,
                db_password=db_password,
                db_service_name=db_service_name,
                wallet_path=wallet_path,
                min_sessions=1,  # 초기화 시간 단축
                max_sessions=5,  # 성능 향상
                increment=1
            )
            logger.info("✅ DB Connection Pool 초기화 완료 (Secret 캐싱 적용)")
        else:
            logger.info("✅ DB Connection Pool 이미 초기화됨 (재사용, Secret도 캐시됨)")
        
        # 2. KIS API 초기화
        trading_mode = os.getenv("TRADING_MODE", "MOCK")
        use_gateway = os.getenv("USE_KIS_GATEWAY", "false").lower() == "true"
        
        if use_gateway:
            kis = KISGatewayClient()
            logger.info("✅ KIS Gateway Client 초기화 완료")
        else:
            # Secret 캐싱 사용
            kis = KIS_API(
                app_key=auth.get_secret(
                    os.getenv(f"{trading_mode}_SECRET_ID_APP_KEY"), 
                    os.getenv("GCP_PROJECT_ID"),
                    use_cache=True
                ),
                app_secret=auth.get_secret(
                    os.getenv(f"{trading_mode}_SECRET_ID_APP_SECRET"), 
                    os.getenv("GCP_PROJECT_ID"),
                    use_cache=True
                ),
                base_url=os.getenv(f"KIS_BASE_URL_{trading_mode}"),
                account_prefix=auth.get_secret(
                    os.getenv(f"{trading_mode}_SECRET_ID_ACCOUNT_PREFIX"), 
                    os.getenv("GCP_PROJECT_ID"),
                    use_cache=True
                ),
                account_suffix=os.getenv("KIS_ACCOUNT_SUFFIX"),
                token_file_path="/tmp/kis_token_daily_briefing.json",
                trading_mode=trading_mode
            )
            kis.authenticate()
            logger.info("✅ KIS API 초기화 완료")
            
        # 3. Telegram Bot 초기화
        # Secret Manager에서 토큰 로드 시도 (캐싱 사용), 없으면 환경변수 사용
        try:
            telegram_token = auth.get_secret("telegram_bot_token", os.getenv("GCP_PROJECT_ID"), use_cache=True)
            telegram_chat_id = auth.get_secret("telegram_chat_id", os.getenv("GCP_PROJECT_ID"), use_cache=True)
        except Exception:
            logger.warning("Secret Manager에서 텔레그램 정보를 찾을 수 없습니다. 환경변수를 사용합니다.")
            telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
            telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")
            
        telegram_bot = TelegramBot(token=telegram_token, chat_id=telegram_chat_id)
        logger.info("✅ Telegram Bot 초기화 완료")
        
        # 4. Reporter 초기화 및 실행
        reporter = DailyReporter(kis, telegram_bot)
        
        # 리포트 생성 및 발송
        result = reporter.create_and_send_report()
        
        if result:
            logger.info("✅ 일일 브리핑 발송 완료")
            return True
        else:
            logger.error("❌ 일일 브리핑 발송 실패")
            return False
            
    except Exception as e:
        logger.critical(f"❌ 서비스 실행 중 오류: {e}", exc_info=True)
        return False

@app.route('/report', methods=['POST'])
def trigger_report():
    """리포트 발송 트리거 (Cloud Scheduler용)"""
    if initialize_service():
        return jsonify({"status": "success"}), 200
    else:
        return jsonify({"status": "error"}), 500

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "ok"}), 200

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
