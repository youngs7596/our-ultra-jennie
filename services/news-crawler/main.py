#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# services/news-crawler/main.py
# Version: v3.5
"""
News Crawler Service - Main API
Cloud Scheduler + RabbitMQ Self-Reschedule 하이브리드 패턴을 지원합니다.
"""

import logging
import os
import uuid
from datetime import datetime, timezone
from flask import Flask, jsonify

from crawler import run_collection_job
from shared.rabbitmq import RabbitMQPublisher, RabbitMQWorker
from shared.scheduler_runtime import parse_job_message, SchedulerJobMessage
from shared.scheduler_client import mark_job_run

app = Flask(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - [%(funcName)s] - %(message)s",
)
logger = logging.getLogger(__name__)

scheduler_job_worker = None
scheduler_job_publisher = None


def _run_crawler(trigger_source: str = "manual") -> dict:
    logger.info("📰 News Crawler 작업 시작 (trigger=%s)", trigger_source)
    run_collection_job()
    logger.info("✅ News Crawler 작업 완료 (trigger=%s)", trigger_source)
    return {"status": "success", "trigger": trigger_source}


def _get_amqp_url() -> str:
    return os.getenv("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/")


def _get_job_queue_name() -> str:
    scope = os.getenv("SCHEDULER_SCOPE", "real")
    default_queue = f"{scope}.jobs.news-crawler"
    return os.getenv("SCHEDULER_QUEUE_NEWS_CRAWLER", default_queue)


def _get_scheduler_job_id() -> str:
    return os.getenv("SCHEDULER_NEWS_CRAWLER_JOB_ID", "news-crawler")


def handle_scheduler_job(payload: dict):
    job_msg = parse_job_message(payload)
    # [v1.0] "unknown"일 때도 환경변수 job_id 사용
    effective_job_id = job_msg.job_id if job_msg.job_id and job_msg.job_id != "unknown" else _get_scheduler_job_id()
    logger.info(
        "🕒 News Crawler Scheduler Job 수신: job=%s (effective=%s) run=%s delay=%s",
        job_msg.job_id,
        effective_job_id,
        job_msg.run_id,
        job_msg.next_delay_sec,
    )
    try:
        _run_crawler(trigger_source=f"scheduler/{job_msg.trigger_source}")
    except Exception as exc:
        logger.error("❌ Scheduler Job 실패: %s", exc, exc_info=True)
    finally:
        mark_job_run(effective_job_id, scope=job_msg.scope)


def start_scheduler_worker():
    global scheduler_job_worker, scheduler_job_publisher
    if os.getenv("ENABLE_NEWS_CRAWLER_JOB_WORKER", "true").lower() != "true":
        logger.info("⚠️ News Crawler Scheduler Worker 비활성화 상태")
        return

    queue_name = _get_job_queue_name()
    scheduler_job_publisher = RabbitMQPublisher(
        amqp_url=_get_amqp_url(),
        queue_name=queue_name,
    )
    scheduler_job_worker = RabbitMQWorker(
        amqp_url=_get_amqp_url(),
        queue_name=queue_name,
        handler=handle_scheduler_job,
    )
    scheduler_job_worker.start()
    logger.info("✅ News Crawler Scheduler Worker 시작 (queue=%s)", queue_name)
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
        "timeout_sec": 300,
        "retry_limit": 1,
        "queued_at": datetime.now(timezone.utc).isoformat(),
    }

    message_id = scheduler_job_publisher.publish(payload)
    if message_id:
        logger.info("🚀 News Crawler Startup Job 발행 (message=%s)", message_id)
    else:
        logger.error("❌ News Crawler Startup Job 발행 실패")


# Gunicorn 환경에서도 즉시 Scheduler Worker를 띄운다.
start_scheduler_worker()


@app.route("/health", methods=["GET"])
def health():
    """헬스체크 엔드포인트"""
    return jsonify({"status": "healthy", "service": "news-crawler"}), 200


@app.route("/crawl", methods=["POST"])
def crawl():
    """
    뉴스 크롤링 실행 엔드포인트 (HTTP 수동 트리거)
    """
    try:
        result = _run_crawler(trigger_source="http")
        return jsonify(result), 200
    except Exception as e:
        logger.error(f"❌ News Crawler 작업 실패: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

