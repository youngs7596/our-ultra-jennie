#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Daily Briefing 실행 스크립트

기존 services/daily-briefing의 DailyReporter를 활용하여
평일 오후 5시에 cron으로 실행, 텔레그램으로 일일 브리핑 발송

사용법:
    python scripts/run_daily_briefing.py

cron 등록 (평일 17시):
    0 17 * * 1-5 cd /path/to/project && python scripts/run_daily_briefing.py
"""

import os
import sys
import logging
from datetime import datetime

# 프로젝트 루트를 Python 경로에 추가
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

# 환경변수 설정
os.environ.setdefault("DB_TYPE", "MARIADB")
os.environ.setdefault("SECRETS_FILE", os.path.join(PROJECT_ROOT, "secrets.json"))

from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)


def is_weekday():
    """평일인지 확인 (월~금)"""
    return datetime.now().weekday() < 5


def run_daily_briefing():
    """일일 브리핑 실행 - 기존 서비스 재활용"""
    logger.info("=== Daily Briefing 시작 ===")
    
    # 평일 체크 (옵션)
    if not is_weekday():
        logger.info("주말이므로 브리핑을 건너뜁니다.")
        return True
    
    try:
        # 1. 필요한 모듈 임포트
        import shared.auth as auth
        import shared.database as database
        from shared.db.connection import ensure_engine_initialized
        from shared.kis.gateway_client import KISGatewayClient
        from shared.notification import TelegramBot
        
        # services/daily-briefing 모듈 임포트
        sys.path.insert(0, os.path.join(PROJECT_ROOT, "services", "daily-briefing"))
        from reporter import DailyReporter
        
        # 2. DB 엔진 초기화
        ensure_engine_initialized()
        
        # 3. DB Connection Pool 초기화
        if not database.is_pool_initialized():
            logger.info("🔧 DB Connection Pool 초기화 중...")
            # SQLAlchemy를 통해 이미 초기화됨
        
        # 4. KIS Gateway Client 초기화
        kis = KISGatewayClient()
        logger.info("✅ KIS Gateway Client 초기화 완료")
        
        # 5. Telegram Bot 초기화
        telegram_token = auth.get_secret("telegram-bot-token")
        telegram_chat_id = auth.get_secret("telegram-chat-id")
        
        if not telegram_token or not telegram_chat_id:
            logger.error("❌ 텔레그램 설정이 없습니다.")
            return False
        
        telegram_bot = TelegramBot(token=telegram_token, chat_id=telegram_chat_id)
        logger.info("✅ Telegram Bot 초기화 완료")
        
        # 6. Reporter 초기화 및 실행
        reporter = DailyReporter(kis, telegram_bot)
        result = reporter.create_and_send_report()
        
        if result:
            logger.info("✅ Daily Briefing 발송 완료!")
            return True
        else:
            logger.error("❌ Daily Briefing 발송 실패")
            return False
        
    except Exception as e:
        logger.error(f"❌ Daily Briefing 실패: {e}", exc_info=True)
        return False


if __name__ == "__main__":
    success = run_daily_briefing()
    sys.exit(0 if success else 1)
