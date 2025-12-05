#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Scout Job Service - Main API
Cloud Scheduler에 의해 HTTP로 트리거되는 Watchlist 갱신 서비스
"""

from flask import Flask, jsonify
import logging
import os
import uuid
from datetime import datetime, timezone

app = Flask(__name__)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [%(funcName)s] - %(message)s'
)
logger = logging.getLogger(__name__)

# scout 모듈을 지연 임포트하여 Health Check는 항상 작동하도록 함
def get_scout_main():
    """Scout 모듈의 main 함수를 가져옵니다 (지연 임포트)"""
    from scout import main as run_scout_job
    return run_scout_job

from shared.rabbitmq import RabbitMQPublisher, RabbitMQWorker
from shared.scheduler_runtime import parse_job_message, SchedulerJobMessage
from shared.scheduler_client import mark_job_run

scheduler_job_worker = None
scheduler_job_publisher = None

@app.route('/health', methods=['GET'])
def health():
    """헬스체크 엔드포인트"""
    return jsonify({"status": "healthy", "service": "scout-job"}), 200

@app.route('/scout', methods=['POST'])
def scout():
    """
    Scout Job 실행 엔드포인트
    Cloud Scheduler가 이 엔드포인트를 호출
    
    1. 자동 파라미터 최적화 (백테스트 + AI 검증)
    2. Watchlist 갱신 (트리플 소스 전략)
    3. 과거 데이터 수집 및 저장
    """
    try:
        result = _run_scout_job(trigger_source="http")
        return jsonify(result), 200
    except Exception as e:
        logger.error(f"❌ Scout Job 실패: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500

def _run_scout_job(trigger_source: str):
    logger.info("🤖 Scout Job 시작 (trigger=%s)", trigger_source)
    run_scout_job = get_scout_main()
    run_scout_job()
    logger.info("✅ Scout Job 완료 (trigger=%s)", trigger_source)
    return {"status": "success", "trigger": trigger_source}


def _get_scheduler_queue_name():
    scope = os.getenv("SCHEDULER_SCOPE", "real")
    default_queue = f"{scope}.jobs.scout"
    return os.getenv("SCHEDULER_QUEUE_SCOUT_JOB", default_queue)


def _get_scheduler_job_id() -> str:
    return os.getenv("SCHEDULER_SCOUT_JOB_ID", "scout-job")


def handle_scheduler_job(payload: dict):
    job_msg = parse_job_message(payload)
    # [v5.1] "unknown"일 때도 환경변수 job_id 사용
    effective_job_id = job_msg.job_id if job_msg.job_id and job_msg.job_id != "unknown" else _get_scheduler_job_id()
    logger.info(
        "🕒 Scout Job Scheduler 메시지 수신: job=%s (effective=%s) run=%s",
        job_msg.job_id,
        effective_job_id,
        job_msg.run_id,
    )
    try:
        _run_scout_job(trigger_source=f"scheduler/{job_msg.trigger_source}")
    except Exception as exc:
        logger.error("❌ Scout Job Scheduler 실행 실패: %s", exc, exc_info=True)
    finally:
        mark_job_run(effective_job_id, scope=job_msg.scope)


def start_scheduler_worker():
    global scheduler_job_worker, scheduler_job_publisher
    if os.getenv("ENABLE_SCOUT_JOB_WORKER", "true").lower() != "true":
        logger.info("⚠️ Scout Job Scheduler Worker 비활성화 상태")
        return

    amqp_url = os.getenv("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/")
    queue_name = _get_scheduler_queue_name()
    scheduler_job_publisher = RabbitMQPublisher(amqp_url=amqp_url, queue_name=queue_name)
    scheduler_job_worker = RabbitMQWorker(
        amqp_url=amqp_url,
        queue_name=queue_name,
        handler=handle_scheduler_job,
    )
    scheduler_job_worker.start()
    logger.info("✅ Scout Job Scheduler Worker 시작 (queue=%s)", queue_name)
    _bootstrap_scheduler_job()


def _bootstrap_scheduler_job():
    if not scheduler_job_publisher:
        logger.warning("⚠️ Scheduler Job Publisher 없음. Bootstrap을 건너뜁니다.")
        return

    payload = {
        "job_id": _get_scheduler_job_id(),
        "scope": os.getenv("SCHEDULER_SCOPE", "real"),
        "run_id": str(uuid.uuid4()),
        "trigger_source": "startup_oneshot",
        "params": {},
        "timeout_sec": 600,
        "retry_limit": 1,
        "queued_at": datetime.now(timezone.utc).isoformat(),
    }

    message_id = scheduler_job_publisher.publish(payload)
    if message_id:
        logger.info("🚀 Scout Job Startup 메시지 발행 (message=%s)", message_id)
    else:
        logger.error("❌ Scout Job Startup 메시지 발행 실패")


start_scheduler_worker()

if __name__ == '__main__':
    # Cloud Run에서는 PORT 환경 변수를 사용
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

