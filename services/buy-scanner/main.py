# services/buy-scanner/main.py
# Version: v3.5
# Buy Scanner Service - Flask 엔트리포인트

import os
import sys
import uuid
import logging
from datetime import datetime, timezone
from flask import Flask, request, jsonify
from dotenv import load_dotenv

# shared 패키지 임포트 경로 설정
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import shared.auth as auth
import shared.database as database
from shared.kis.client import KISClient as KIS_API
from shared.kis.gateway_client import KISGatewayClient
from shared.config import ConfigManager
from shared.rabbitmq import RabbitMQPublisher, RabbitMQWorker  # [변경] shared 모듈 사용
from shared.scheduler_runtime import parse_job_message, SchedulerJobMessage
from shared.scheduler_client import mark_job_run

from scanner import BuyScanner

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - [%(funcName)s] - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# 전역 변수
scanner = None
rabbitmq_publisher = None
scheduler_job_worker = None
scheduler_job_publisher = None
scheduler_job_queue = None


def initialize_service():
    """서비스 초기화"""
    global scanner, rabbitmq_publisher, scheduler_job_worker, scheduler_job_publisher, scheduler_job_queue
    
    logger.info("=== Buy Scanner Service 초기화 시작 ===")
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
                token_file_path="/tmp/kis_token_buy_scanner.json",
                trading_mode=trading_mode
            )
            kis.authenticate()
            logger.info("✅ KIS API 초기화 완료")
        
        # 3. ConfigManager 초기화
        config_manager = ConfigManager(db_conn=None, cache_ttl=300)
        
        # 4. Buy Scanner 초기화
        scanner = BuyScanner(kis=kis, config=config_manager)
        logger.info("✅ Buy Scanner 초기화 완료")
        
        # 5. RabbitMQ Publisher 초기화 (Pub/Sub 대체)
        amqp_url = os.getenv("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/")
        # 매수 신호 큐는 아직 정의되지 않았으므로 'buy-signals'로 정의
        queue_name = os.getenv("RABBITMQ_QUEUE_BUY_SIGNALS", "buy-signals")
        rabbitmq_publisher = RabbitMQPublisher(amqp_url=amqp_url, queue_name=queue_name)
        logger.info("✅ RabbitMQ Publisher 초기화 완료 (queue=%s)", queue_name)

        # 6. Scheduler Job Worker (RabbitMQ)
        if os.getenv("ENABLE_BUY_SCANNER_JOB_WORKER", "true").lower() == "true":
            scheduler_job_queue = os.getenv("SCHEDULER_QUEUE_BUY_SCANNER", "real.jobs.buy-scanner")
            scheduler_job_publisher = RabbitMQPublisher(amqp_url=amqp_url, queue_name=scheduler_job_queue)
            scheduler_job_worker = RabbitMQWorker(
                amqp_url=amqp_url,
                queue_name=scheduler_job_queue,
                handler=handle_scheduler_job_message,
            )
            scheduler_job_worker.start()
            logger.info("✅ Scheduler Job Worker 시작 (queue=%s)", scheduler_job_queue)
            _bootstrap_scheduler_job()
        else:
            logger.info("⚠️ Scheduler Job Worker 비활성화 (ENABLE_BUY_SCANNER_JOB_WORKER=false)")
        
        logger.info("=== Buy Scanner Service 초기화 완료 ===")
        return True
        
    except Exception as e:
        logger.critical(f"❌ 초기화 실패: {e}", exc_info=True)
        return False


@app.route('/health', methods=['GET'])
def health_check():
    if scanner and rabbitmq_publisher:
        return jsonify({"status": "ok", "service": "buy-scanner"}), 200
    else:
        return jsonify({"status": "initializing"}), 503


def _perform_scan(trigger_source: str = "manual") -> dict:
    """Scanner 실행 및 RabbitMQ 발행 (공용 로직)"""
    if not scanner or not rabbitmq_publisher:
        raise RuntimeError("Service not initialized")

    dry_run = os.getenv("DRY_RUN", "true").lower() == "true"
    logger.info("=== 매수 신호 스캔 시작 (trigger=%s) ===", trigger_source)
    scan_result = scanner.scan_buy_opportunities()

    if not scan_result or not scan_result.get("candidates"):
        logger.info("매수 후보가 없습니다.")
        return {"status": "no_candidates", "dry_run": dry_run}

    message_id = rabbitmq_publisher.publish(scan_result)
    if not message_id:
        raise RuntimeError("Failed to publish buy signal to RabbitMQ")

    logger.info(
        "✅ 매수 신호 발행 완료 (ID: %s, 후보 %d개)",
        message_id,
        len(scan_result["candidates"]),
    )
    return {
        "status": "success",
        "message_id": message_id,
        "candidates_count": len(scan_result["candidates"]),
        "market_regime": scan_result.get("market_regime"),
        "dry_run": dry_run,
    }


@app.route('/scan', methods=['POST'])
def scan():
    """매수 신호 스캔"""
    try:
        result = _perform_scan(trigger_source="http")
        http_status = 200 if result.get("status") != "error" else 500
        return jsonify(result), http_status
    except RuntimeError as err:
        logger.error("❌ /scan 처리 중 오류: %s", err, exc_info=True)
        return jsonify({"status": "error", "error": str(err)}), 500
    except Exception as e:
        logger.error(f"❌ /scan 처리 중 예외: {e}", exc_info=True)
        return jsonify({"status": "error", "error": str(e)}), 500


def _get_scheduler_job_id() -> str:
    return os.getenv("SCHEDULER_BUY_SCANNER_JOB_ID", "buy-scanner")


def _bootstrap_scheduler_job():
    """서비스 기동 시 1회 실행 메시지를 발행."""
    if not scheduler_job_publisher:
        logger.warning("⚠️ Scheduler Job Publisher 없음. Bootstrap을 건너뜁니다.")
        return

    job_id = _get_scheduler_job_id()
    payload = {
        "job_id": job_id,
        "scope": os.getenv("SCHEDULER_SCOPE", "real"),
        "run_id": str(uuid.uuid4()),
        "trigger_source": "startup_oneshot",
        "params": {},
        "timeout_sec": 180,
        "retry_limit": 1,
        "queued_at": datetime.now(timezone.utc).isoformat(),
    }

    message_id = scheduler_job_publisher.publish(payload)
    if message_id:
        logger.info("🚀 Buy Scanner Startup Job 발행 (job=%s, message=%s)", job_id, message_id)
    else:
        logger.error("❌ Buy Scanner Startup Job 발행 실패 (job=%s)", job_id)


def handle_scheduler_job_message(payload: dict):
    """Scheduler Queue에서 전달된 Job 처리"""
    job_msg = parse_job_message(payload)
    # [v5.1] "unknown"일 때도 환경변수 job_id 사용
    effective_job_id = job_msg.job_id if job_msg.job_id and job_msg.job_id != "unknown" else _get_scheduler_job_id()
    logger.info(
        "🕒 Scheduler Job 수신: job=%s (effective=%s) run=%s trigger=%s delay=%s",
        job_msg.job_id,
        effective_job_id,
        job_msg.run_id,
        job_msg.trigger_source,
        job_msg.next_delay_sec,
    )

    try:
        _perform_scan(trigger_source=f"scheduler/{job_msg.trigger_source}")
        logger.info("✅ Scheduler Job 처리 완료: job=%s", effective_job_id)
    except Exception as exc:
        logger.error("❌ Scheduler Job 실패: %s", exc, exc_info=True)
    finally:
        mark_job_run(effective_job_id, scope=job_msg.scope)


@app.route('/', methods=['GET'])
def root():
    return jsonify({
        "service": "buy-scanner",
        "version": "v3.5",
        "trading_mode": os.getenv("TRADING_MODE", "MOCK"),
        "dry_run": os.getenv("DRY_RUN", "true")
    }), 200


if scanner is None and os.getenv('WERKZEUG_RUN_MAIN') != 'true':
    logger.info("모듈 로드 시 서비스 초기화 시작")
    if not initialize_service():
        logger.critical("서비스 초기화 실패")
        raise RuntimeError("Service initialization failed")

if __name__ == '__main__':
    if scanner is None:
        if not initialize_service():
            sys.exit(1)
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=False)
