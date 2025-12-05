import logging
import os
from typing import Optional

import requests

logger = logging.getLogger(__name__)

SCHEDULER_SERVICE_URL = os.getenv(
    "SCHEDULER_SERVICE_URL",
    "http://scheduler-service:8095",
)


def mark_job_run(job_id: str, scope: Optional[str] = None) -> bool:
    """
    Scheduler Service에 해당 Job이 정상 실행되었음을 보고합니다.
    scope를 명시하지 않으면 현재 환경 변수 SCHEDULER_SCOPE를 사용합니다.
    """
    if not job_id:
        logger.warning("⚠️ Scheduler job_id가 비어 있어 last_run 업데이트를 건너뜁니다.")
        return False

    target_scope = scope or os.getenv("SCHEDULER_SCOPE", "real")
    url = f"{SCHEDULER_SERVICE_URL}/jobs/{job_id}/last-run"
    payload = {"scope": target_scope}

    try:
        resp = requests.post(url, json=payload, timeout=5)
        resp.raise_for_status()
        logger.debug("✅ Scheduler last_run_at 업데이트 성공 (job=%s, scope=%s)", job_id, target_scope)
        return True
    except requests.RequestException as exc:
        logger.error("❌ Scheduler last_run 보고 실패 (job=%s): %s", job_id, exc)
        return False

