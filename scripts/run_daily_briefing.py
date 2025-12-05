#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Daily Briefing 트리거 스크립트

Docker 내의 daily-briefing 서비스 엔드포인트를 HTTP 호출하여 브리핑 발송

사용법:
    python scripts/run_daily_briefing.py

    또는 curl 직접 사용:
    curl -X POST http://localhost:8086/report

cron 등록 (평일 17시):
    0 17 * * 1-5 curl -X POST http://localhost:8086/report
"""

import os
import sys
import logging
import requests
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Daily Briefing 서비스 URL (docker-compose.yml 참조)
DAILY_BRIEFING_URL = os.getenv("DAILY_BRIEFING_URL", "http://localhost:8086/report")


def is_weekday():
    """평일인지 확인 (월~금)"""
    return datetime.now().weekday() < 5


def trigger_daily_briefing():
    """Docker의 daily-briefing 서비스 엔드포인트 호출"""
    logger.info("=== Daily Briefing 트리거 ===")
    
    if not is_weekday():
        logger.info("주말이므로 브리핑을 건너뜁니다.")
        return True
    
    try:
        logger.info(f"📤 호출: {DAILY_BRIEFING_URL}")
        response = requests.post(DAILY_BRIEFING_URL, timeout=60)
        
        if response.status_code == 200:
            logger.info("✅ Daily Briefing 발송 성공!")
            return True
        else:
            logger.error(f"❌ 실패: HTTP {response.status_code} - {response.text}")
            return False
            
    except requests.exceptions.ConnectionError:
        logger.error("❌ 연결 실패: daily-briefing 서비스가 실행 중인지 확인하세요")
        logger.info("💡 힌트: docker compose --profile real up -d daily-briefing")
        return False
    except Exception as e:
        logger.error(f"❌ 오류: {e}")
        return False


if __name__ == "__main__":
    success = trigger_daily_briefing()
    sys.exit(0 if success else 1)
