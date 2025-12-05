# services/command-handler/main.py
# Version: v3.5
# Command Handler Service - Flask 엔트리포인트

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


def initialize_service():
    """서비스 초기화"""
    global command_handler
    
    logger.info("=== Command Handler Service 초기화 시작 ===")
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
                max_sessions=5,  # 성능 향상 (기존 3에서 5로 증가)
                increment=1
            )
            logger.info("✅ DB Connection Pool 초기화 완료 (Secret 캐싱 적용)")
        else:
            logger.info("✅ DB Connection Pool 이미 초기화됨 (재사용, Secret도 캐시됨)")
        
        # 2. KIS API 초기화 (Gateway 사용)
        trading_mode = os.getenv("TRADING_MODE", "MOCK")
        use_gateway = os.getenv("USE_KIS_GATEWAY", "true").lower() == "true"
        logger.info(f"거래 모드: {trading_mode}, Gateway 사용: {use_gateway}")
        
        if use_gateway:
            # KIS Gateway 사용 (권장)
            kis = KISGatewayClient()
            logger.info("✅ KIS Gateway Client 초기화 완료")
        else:
            # 직접 KIS API 호출 (Fallback)
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
        
        # 4. Command Handler 초기화
        command_handler = CommandHandler(kis=kis, config=config_manager)
        logger.info("✅ Command Handler 초기화 완료")
        
        logger.info("=== Command Handler Service 초기화 완료 ===")
        return True
        
    except Exception as e:
        logger.critical(f"❌ 초기화 실패: {e}", exc_info=True)
        return False


@app.route('/health', methods=['GET'])
def health_check():
    """헬스 체크"""
    if command_handler:
        return jsonify({"status": "ok", "service": "command-handler"}), 200
    else:
        return jsonify({"status": "initializing"}), 503


@app.route('/poll', methods=['POST'])
def poll_commands():
    """명령 폴링 및 처리"""
    try:
        logger.info("=== /poll 엔드포인트 호출 ===")
        
        if not command_handler:
            logger.error("서비스가 초기화되지 않았습니다")
            return jsonify({"error": "Service not initialized"}), 503
        
        # DRY_RUN 모드 확인
        dry_run = os.getenv('DRY_RUN', 'true').lower() == 'true'
        
        # 명령 폴링 및 처리
        result = command_handler.poll_and_process(dry_run=dry_run)
        
        if result['processed_count'] > 0:
            logger.info(f"✅ {result['processed_count']}개 명령 처리 완료")
        else:
            logger.debug("대기 중인 명령 없음")
        
        return jsonify(result), 200
        
    except Exception as e:
        logger.error(f"❌ /poll 처리 중 오류: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route('/', methods=['GET'])
def root():
    """루트 엔드포인트"""
    return jsonify({
        "service": "command-handler",
        "version": "v3.5",
        "trading_mode": os.getenv("TRADING_MODE", "MOCK"),
        "dry_run": os.getenv("DRY_RUN", "true")
    }), 200


# Gunicorn은 if __name__ == '__main__' 블록을 실행하지 않으므로
# 모듈 로드 시 초기화 (단, 개발 모드에서는 중복 초기화 방지)
if command_handler is None and os.getenv('WERKZEUG_RUN_MAIN') != 'true':
    logger.info("모듈 로드 시 서비스 초기화 시작")
    if not initialize_service():
        logger.critical("서비스 초기화 실패")
        raise RuntimeError("Service initialization failed")

if __name__ == '__main__':
    # 로컬 개발 모드 (python main.py 직접 실행)
    if command_handler is None:
        if not initialize_service():
            logger.critical("서비스 초기화 실패로 종료합니다.")
            sys.exit(1)
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=False)

