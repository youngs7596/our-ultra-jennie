# youngs75_jennie/notification.py
# Version: v3.5
# 텔레그램 등 외부 알림 전송 유틸리티

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
