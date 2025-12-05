import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional

from shared.rabbitmq import RabbitMQPublisher

logger = logging.getLogger(__name__)


@dataclass
class SchedulerJobMessage:
    job_id: str
    run_id: str
    trigger_source: str
    params: Dict
    scope: str = "real"
    next_delay_sec: Optional[int] = None
    auto_reschedule: bool = True
    timeout_sec: int = 120
    retry_limit: int = 3
    telemetry_label: Optional[str] = None
    queued_at: Optional[str] = None

    @property
    def default_delay(self) -> Optional[int]:
        return self.next_delay_sec


def parse_job_message(payload: Dict) -> SchedulerJobMessage:
    if not isinstance(payload, dict):
        raise ValueError("Scheduler payload must be dict")

    return SchedulerJobMessage(
        job_id=payload.get("job_id", "unknown"),
        run_id=payload.get("run_id", str(uuid.uuid4())),
        trigger_source=payload.get("trigger_source", "unknown"),
        params=payload.get("params") or {},
        scope=payload.get("scope", "real"),
        next_delay_sec=payload.get("next_delay_sec"),
        auto_reschedule=payload.get("auto_reschedule", True),
        timeout_sec=payload.get("timeout_sec", 120),
        retry_limit=payload.get("retry_limit", 3),
        telemetry_label=payload.get("telemetry_label"),
        queued_at=payload.get("queued_at"),
    )


def reschedule_job(
    publisher: RabbitMQPublisher,
    original_message: SchedulerJobMessage,
    delay_override: Optional[int] = None,
    trigger_source: str = "auto-reschedule",
) -> bool:
    delay = delay_override or original_message.next_delay_sec
    if not delay or delay <= 0:
        logger.info(
            "⏭️ Scheduler job %s 에 대해 재스케줄을 건너뜁니다 (delay=%s)",
            original_message.job_id,
            delay,
        )
        return False

    payload = {
        "job_id": original_message.job_id,
        "scope": original_message.scope,
        "run_id": str(uuid.uuid4()),
        "trigger_source": trigger_source,
        "params": original_message.params,
        "next_delay_sec": delay,
        "auto_reschedule": original_message.auto_reschedule,
        "timeout_sec": original_message.timeout_sec,
        "retry_limit": original_message.retry_limit,
        "telemetry_label": original_message.telemetry_label,
        "queued_at": datetime.now(timezone.utc).isoformat(),
    }

    message_id = publisher.publish(payload, delay_seconds=delay)
    if message_id:
        logger.info(
            "🔁 Scheduler job %s 재스케줄 완료 (delay=%ss, message=%s)",
            original_message.job_id,
            delay,
            message_id,
        )
        return True
    return False

