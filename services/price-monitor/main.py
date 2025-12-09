"""
services/price-monitor/main.py - 실시간 가격 모니터링 서비스
=========================================================

이 서비스는 보유 종목의 가격을 실시간 모니터링하여 매도 신호를 발생시킵니다.

매도 조건:
---------
1. 목표가 도달 (PROFIT_TARGET)
2. 손절가 도달 (STOP_LOSS)
3. RSI 과매수 (RSI > 70/75/78)
4. 보유 기간 초과 (TIME_EXIT)
5. ATR 기반 트레일링 스탑

처리 흐름:
---------
1. Scheduler에서 주기적 트리거
2. 보유 종목(PORTFOLIO) 조회
3. 각 종목 현재가 조회 (KIS Gateway)
4. 매도 조건 충족 시 sell-orders 큐로 발행

출력:
----
RabbitMQ sell-orders 큐로 매도 신호 발행

환경변수:
--------
- PORT: HTTP 서버 포트 (기본: 8088)
- TRADING_MODE: REAL/MOCK
- RABBITMQ_URL: RabbitMQ 연결 URL
- KIS_GATEWAY_URL: KIS Gateway URL
"""

import os
import sys
import logging
import threading
import uuid
from datetime import datetime, timezone
from flask import Flask, jsonify
from dotenv import load_dotenv

# shared 패키지 임포트 경로 설정
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import shared.auth as auth
import shared.database as database
from shared.kis.client import KISClient as KIS_API
from shared.kis.gateway_client import KISGatewayClient
from shared.config import ConfigManager
from shared.rabbitmq import RabbitMQPublisher, RabbitMQWorker
from shared.scheduler_runtime import (
    parse_job_message,
    SchedulerJobMessage,
)
from shared.scheduler_client import mark_job_run
from shared.notification import TelegramBot

from monitor import PriceMonitor

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - [%(funcName)s] - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# 전역 변수
price_monitor = None
monitor_thread = None
is_monitoring = False
rabbitmq_url = None
rabbitmq_sell_queue = None
tasks_publisher = None
scheduler_job_worker = None
scheduler_job_publisher = None
monitor_lock = threading.Lock()


def initialize_service():
    """서비스 초기화"""
    global price_monitor, rabbitmq_url, rabbitmq_sell_queue, tasks_publisher
    
    logger.info("=== Price Monitor Service 초기화 시작 ===")
    load_dotenv()
    
    try:
        # 1. DB Connection Pool 초기화 (SQLAlchemy 사용)
        from shared.db.connection import ensure_engine_initialized
        logger.info("🔧 DB Connection 초기화 중...")
        ensure_engine_initialized()
        logger.info("✅ DB Connection 초기화 완료")
        
        # 2. KIS API 초기화
        trading_mode = os.getenv("TRADING_MODE", "MOCK")
        logger.info(f"거래 모드: {trading_mode}")
        
        kis = KIS_API(
            app_key=auth.get_secret(os.getenv(f"{trading_mode}_SECRET_ID_APP_KEY")),
            app_secret=auth.get_secret(os.getenv(f"{trading_mode}_SECRET_ID_APP_SECRET")),
            base_url=os.getenv(f"KIS_BASE_URL_{trading_mode}"),
            account_prefix=auth.get_secret(os.getenv(f"{trading_mode}_SECRET_ID_ACCOUNT_PREFIX")),
            account_suffix=os.getenv("KIS_ACCOUNT_SUFFIX"),
            trading_mode=trading_mode
        )
        kis.authenticate()
        logger.info("✅ KIS API 직접 연결 초기화 완료 (WebSocket 강제 사용)")
        
        # 3. ConfigManager 초기화
        config_manager = ConfigManager(db_conn=None, cache_ttl=300)
        
        # 4. Telegram Bot (가격 알림용, 선택)
        telegram_token = auth.get_secret("telegram_bot_token") if auth.get_secret("telegram_bot_token") else os.getenv("TELEGRAM_BOT_TOKEN")
        telegram_chat_id = auth.get_secret("telegram_chat_id") if auth.get_secret("telegram_chat_id") else os.getenv("TELEGRAM_CHAT_ID")
        telegram_bot = TelegramBot(token=telegram_token, chat_id=telegram_chat_id) if telegram_token and telegram_chat_id else None
        
        # 5. 매도 요청 Publisher 초기화 (RabbitMQ)
        rabbitmq_url = os.getenv("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/")
        rabbitmq_sell_queue = os.getenv("RABBITMQ_QUEUE_SELL_ORDERS", "sell-orders")
        
        tasks_publisher = RabbitMQPublisher(amqp_url=rabbitmq_url, queue_name=rabbitmq_sell_queue)
        logger.info("✅ RabbitMQ Publisher 초기화 완료 (queue=%s)", rabbitmq_sell_queue)
        
        # 6. Price Monitor 초기화
        price_monitor = PriceMonitor(
            kis=kis,
            config=config_manager,
            tasks_publisher=tasks_publisher,
            telegram_bot=telegram_bot
        )
        logger.info("✅ Price Monitor 초기화 완료")
        
        logger.info("=== Price Monitor Service 초기화 완료 ===")
        _start_scheduler_worker()
        return True
        
    except Exception as e:
        logger.critical(f"❌ 초기화 실패: {e}", exc_info=True)
        return False


@app.route('/health', methods=['GET'])
def health_check():
    if price_monitor:
        return jsonify({
            "status": "ok",
            "service": "price-monitor",
            "is_monitoring": is_monitoring
        }), 200
    else:
        return jsonify({"status": "initializing"}), 503


@app.route('/start', methods=['POST'])
def start_monitoring():
    try:
        result = _start_monitor_thread(trigger_source="http")
        http_status = 200 if result.get("status") != "error" else 500
        return jsonify(result), http_status
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/stop', methods=['POST'])
def stop_monitoring():
    try:
        result = _stop_monitor_thread(trigger_source="http")
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/', methods=['GET'])
def root():
    return jsonify({
        "service": "price-monitor",
        "version": "v3.5",
        "trading_mode": os.getenv("TRADING_MODE", "MOCK"),
        "dry_run": os.getenv("DRY_RUN", "true"),
        "is_monitoring": is_monitoring
    }), 200


# =============================================================================
# Scheduler Worker & Helper Functions
# =============================================================================
def _get_scheduler_queue_name():
    scope = os.getenv("SCHEDULER_SCOPE", "real")
    default_queue = f"{scope}.jobs.price-monitor"
    return os.getenv("SCHEDULER_QUEUE_PRICE_MONITOR", default_queue)


def _get_scheduler_job_id() -> str:
    return os.getenv("SCHEDULER_PRICE_MONITOR_START_JOB_ID", "price-monitor-start")


def _start_monitor_thread(trigger_source: str):
    global monitor_thread, is_monitoring
    if not price_monitor:
        raise RuntimeError("Service not initialized")

    with monitor_lock:
        if is_monitoring:
            logger.info("⚠️ Price Monitor 이미 실행 중 (trigger=%s)", trigger_source)
            return {"status": "already_running"}

        price_monitor.stop_event.clear()
        dry_run = os.getenv("DRY_RUN", "true").lower() == "true"
        monitor_thread = threading.Thread(
            target=price_monitor.start_monitoring,
            args=(dry_run,),
            daemon=True,
        )
        is_monitoring = True
        monitor_thread.start()
        logger.info("🚀 Price Monitor 시작 (trigger=%s, dry_run=%s)", trigger_source, dry_run)
        return {"status": "started", "dry_run": dry_run, "trigger": trigger_source}


def _stop_monitor_thread(trigger_source: str):
    global monitor_thread, is_monitoring
    with monitor_lock:
        if not is_monitoring:
            logger.info("ℹ️ Price Monitor 정지 요청 (이미 중지 상태, trigger=%s)", trigger_source)
            return {"status": "not_running"}

        logger.info("🛑 Price Monitor 정지 요청 수신 (trigger=%s)", trigger_source)
        is_monitoring = False
        price_monitor.stop_monitoring()

        if monitor_thread:
            monitor_thread.join(timeout=30)
            monitor_thread = None

        return {"status": "stopped", "trigger": trigger_source}


def handle_scheduler_job(payload: dict):
    job_msg = parse_job_message(payload)
    action = (job_msg.params or {}).get("action", "start")
    logger.info(
        "🕒 Price Monitor Scheduler Job 수신: job=%s action=%s run=%s",
        job_msg.job_id,
        action,
        job_msg.run_id,
    )

    # [v1.0] "unknown"일 때도 환경변수 job_id 사용
    effective_job_id = job_msg.job_id if job_msg.job_id and job_msg.job_id != "unknown" else _get_scheduler_job_id()
    
    try:
        if action == "start":
            _start_monitor_thread(trigger_source=f"scheduler/{job_msg.trigger_source}")
        elif action == "stop":
            _stop_monitor_thread(trigger_source=f"scheduler/{job_msg.trigger_source}")
        elif action == "pulse":
            if not is_monitoring:
                logger.warning("⚠️ Pulse 트리거 감지: Price Monitor가 중지 상태라 자동 시작합니다.")
                _start_monitor_thread(trigger_source="scheduler/pulse")
            else:
                logger.info("✅ Pulse 확인: Price Monitor 정상 실행 중.")
        else:
            logger.warning("⚠️ 알 수 없는 action=%s. 작업을 건너뜁니다.", action)
    except Exception as exc:
        logger.error("❌ Price Monitor Scheduler Job 처리 실패: %s", exc, exc_info=True)
    finally:
        mark_job_run(effective_job_id, scope=job_msg.scope)


def _start_scheduler_worker():
    global scheduler_job_worker, scheduler_job_publisher
    if os.getenv("ENABLE_PRICE_MONITOR_JOB_WORKER", "true").lower() != "true":
        logger.info("⚠️ Price Monitor Scheduler Worker 비활성화 (ENABLE_PRICE_MONITOR_JOB_WORKER=false)")
        return

    queue_name = _get_scheduler_queue_name()
    scheduler_job_publisher = RabbitMQPublisher(
        amqp_url=rabbitmq_url or os.getenv("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/"),
        queue_name=queue_name,
    )
    scheduler_job_worker = RabbitMQWorker(
        amqp_url=rabbitmq_url or os.getenv("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/"),
        queue_name=queue_name,
        handler=handle_scheduler_job,
    )
    scheduler_job_worker.start()
    logger.info("✅ Price Monitor Scheduler Worker 시작 (queue=%s)", queue_name)
    _bootstrap_scheduler_job()


def _bootstrap_scheduler_job():
    if not scheduler_job_publisher:
        logger.warning("⚠️ Scheduler Job Publisher 없음. Startup 메시지를 건너뜁니다.")
        return

    payload = {
        "job_id": _get_scheduler_job_id(),
        "scope": os.getenv("SCHEDULER_SCOPE", "real"),
        "run_id": str(uuid.uuid4()),
        "trigger_source": "startup_oneshot",
        "params": {"action": "start"},
        "timeout_sec": 180,
        "retry_limit": 1,
        "queued_at": datetime.now(timezone.utc).isoformat(),
    }

    message_id = scheduler_job_publisher.publish(payload)
    if message_id:
        logger.info("🚀 Price Monitor Startup Job 발행 (message=%s)", message_id)
    else:
        logger.error("❌ Price Monitor Startup Job 발행 실패")


if price_monitor is None and os.getenv('WERKZEUG_RUN_MAIN') != 'true':
    if not initialize_service():
        logger.critical("서비스 초기화 실패")
        raise RuntimeError("Service initialization failed")

if __name__ == '__main__':
    if price_monitor is None:
        if not initialize_service():
            sys.exit(1)
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, debug=False)
