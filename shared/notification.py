"""
shared/notification.py - Ultra Jennie 알림 모듈
==============================================

이 모듈은 텔레그램을 통한 실시간 알림 발송을 담당합니다.

주요 기능:
---------
- 매수/매도 체결 알림
- 일간 브리핑 발송
- 오류 알림

알림 형식:
---------
Mock 모드: 🧪 [MOCK 테스트] 접두사 추가
DRY RUN: ⚠️ [DRY RUN] 접두사 추가

사용 예시:
---------
>>> from shared.notification import TelegramBot
>>>
>>> bot = TelegramBot()
>>> bot.send_message("💰 매수 체결: 삼성전자 10주 @ 70,000원")

환경변수:
--------
- TELEGRAM_BOT_TOKEN: 텔레그램 봇 토큰 (또는 secrets.json)
- TELEGRAM_CHAT_ID: 텔레그램 채팅 ID (또는 secrets.json)
"""

import logging
import requests
import os

logger = logging.getLogger(__name__)

class TelegramBot:
    """텔레그램 알림 발송 클래스"""
    
    def __init__(self, token=None, chat_id=None):
        self.token = token or os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")
        self.base_url = f"https://api.telegram.org/bot{self.token}"
        
    def send_message(self, message: str) -> bool:
        """
        텔레그램 메시지를 발송합니다.
        
        Args:
            message: 보낼 메시지 내용
            
        Returns:
            bool: 성공 여부
        """
        if not self.token or not self.chat_id:
            logger.warning("⚠️ 텔레그램 토큰 또는 Chat ID가 설정되지 않아 알림을 보낼 수 없습니다.")
            return False
            
        try:
            url = f"{self.base_url}/sendMessage"
            
            # Markdown 특수문자 이스케이핑
            # _, *, [, ], (, ), ~, `, >, #, +, -, =, |, {, }, ., ! 는 이스케이프 필요
            message_escaped = message.replace('_', '\\_').replace('*', '\\*').replace('[', '\\[').replace(']', '\\]')
           
            payload = {
                "chat_id": self.chat_id,
                "text": message_escaped,
                "parse_mode": "Markdown" # 마크다운 지원
            }
            
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code != 200:
                logger.error(f"❌ 텔레그램 API 응답: {response.text}")
            response.raise_for_status()
            
            logger.info("✅ 텔레그램 알림 발송 성공")
            return True
            
        except Exception as e:
            logger.error(f"❌ 텔레그램 알림 발송 실패: {e}")
            return False
