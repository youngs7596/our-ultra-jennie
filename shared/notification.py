"""
shared/notification.py - Ultra Jennie 알림 모듈
==============================================

이 모듈은 텔레그램을 통한 실시간 알림 발송 및 명령 수신을 담당합니다.

주요 기능:
---------
- 매수/매도 체결 알림
- 일간 브리핑 발송
- 오류 알림
- [v3.6] Telegram 명령 수신 및 파싱

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
>>>
>>> # 명령 수신 (polling)
>>> commands = bot.get_pending_commands()
>>> for cmd in commands:
...     print(cmd)  # {'command': 'pause', 'args': ['변동성 크다'], 'chat_id': 123456}

환경변수:
--------
- TELEGRAM_BOT_TOKEN: 텔레그램 봇 토큰 (또는 secrets.json)
- TELEGRAM_CHAT_ID: 텔레그램 채팅 ID (또는 secrets.json)
- TELEGRAM_ALLOWED_CHAT_IDS: 허용된 Chat ID 목록 (콤마 구분)
"""

import logging
import requests
import os
import re
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


class TelegramBot:
    """텔레그램 알림 발송 및 명령 수신 클래스"""
    
    # 지원하는 명령어 목록
    SUPPORTED_COMMANDS = [
        'pause', 'resume', 'stop', 'dryrun',  # 매매 제어
        'buy', 'sell', 'sellall',              # 수동 매매
        'status', 'portfolio', 'pnl', 'balance', 'price',  # 조회
        'watch', 'unwatch', 'watchlist',       # 관심종목
        'mute', 'unmute', 'alert', 'alerts',   # 알림 제어
        'risk', 'minscore', 'maxbuy', 'config', # 설정
        'help'                                  # 도움말
    ]
    
    def __init__(self, token=None, chat_id=None, allowed_chat_ids=None):
        self.token = token or os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")
        self.base_url = f"https://api.telegram.org/bot{self.token}"
        
        # 허용된 Chat ID 목록 (보안)
        if allowed_chat_ids:
            self.allowed_chat_ids = allowed_chat_ids
        else:
            allowed_str = os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "")
            if allowed_str:
                self.allowed_chat_ids = [cid.strip() for cid in allowed_str.split(",")]
            else:
                # 기본 chat_id만 허용
                self.allowed_chat_ids = [str(self.chat_id)] if self.chat_id else []
        
        # 마지막 처리한 update_id (중복 방지)
        self._last_update_id = 0
        
    def send_message(self, message: str, chat_id: str = None) -> bool:
        """
        텔레그램 메시지를 발송합니다.
        
        Args:
            message: 보낼 메시지 내용
            chat_id: 대상 Chat ID (None이면 기본 chat_id 사용)
            
        Returns:
            bool: 성공 여부
        """
        target_chat_id = chat_id or self.chat_id
        
        if not self.token or not target_chat_id:
            logger.warning("⚠️ 텔레그램 토큰 또는 Chat ID가 설정되지 않아 알림을 보낼 수 없습니다.")
            return False
            
        try:
            url = f"{self.base_url}/sendMessage"
            
            # Markdown 특수문자 이스케이핑
            # _, *, [, ], (, ), ~, `, >, #, +, -, =, |, {, }, ., ! 는 이스케이프 필요
            message_escaped = message.replace('_', '\\_').replace('*', '\\*').replace('[', '\\[').replace(']', '\\]')
           
            payload = {
                "chat_id": target_chat_id,
                "text": message_escaped,
                "parse_mode": "Markdown"  # 마크다운 지원
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
    
    def get_updates(self, timeout: int = 30) -> List[Dict[str, Any]]:
        """
        Telegram getUpdates API를 호출하여 새 메시지를 가져옵니다.
        
        Args:
            timeout: Long polling 타임아웃 (초)
        
        Returns:
            새 메시지 목록
        """
        if not self.token:
            logger.warning("⚠️ 텔레그램 토큰이 설정되지 않았습니다.")
            return []
        
        try:
            url = f"{self.base_url}/getUpdates"
            params = {
                "offset": self._last_update_id + 1,
                "timeout": timeout,
                "allowed_updates": ["message"]
            }
            
            response = requests.get(url, params=params, timeout=timeout + 10)
            response.raise_for_status()
            
            result = response.json()
            if not result.get("ok"):
                logger.error(f"❌ Telegram API 오류: {result}")
                return []
            
            updates = result.get("result", [])
            
            # 마지막 update_id 업데이트
            if updates:
                self._last_update_id = max(u.get("update_id", 0) for u in updates)
            
            return updates
            
        except requests.exceptions.Timeout:
            # 타임아웃은 정상 동작 (새 메시지 없음)
            return []
        except Exception as e:
            logger.error(f"❌ Telegram getUpdates 실패: {e}")
            return []
    
    def parse_command(self, message_text: str) -> Optional[Dict[str, Any]]:
        """
        메시지 텍스트에서 명령어를 파싱합니다.
        
        Args:
            message_text: 메시지 텍스트 (예: "/buy 삼성전자 10")
        
        Returns:
            {'command': 'buy', 'args': ['삼성전자', '10']} 또는 None
        """
        if not message_text:
            return None
        
        text = message_text.strip()
        
        # / 로 시작하는 명령어인지 확인
        if not text.startswith('/'):
            return None
        
        # 명령어와 인자 분리
        parts = text[1:].split()  # / 제거 후 공백으로 분리
        if not parts:
            return None
        
        command = parts[0].lower()
        
        # @botname 제거 (그룹 채팅에서 발생)
        if '@' in command:
            command = command.split('@')[0]
        
        # 지원하는 명령어인지 확인
        if command not in self.SUPPORTED_COMMANDS:
            logger.debug(f"지원하지 않는 명령어: {command}")
            return None
        
        args = parts[1:] if len(parts) > 1 else []
        
        return {
            "command": command,
            "args": args,
            "raw_text": text
        }
    
    def is_authorized(self, chat_id: int) -> bool:
        """
        Chat ID가 허용된 사용자인지 확인합니다.
        
        Args:
            chat_id: 확인할 Chat ID
        
        Returns:
            허용 여부
        """
        return str(chat_id) in self.allowed_chat_ids
    
    def get_pending_commands(self, timeout: int = 1) -> List[Dict[str, Any]]:
        """
        대기 중인 명령어를 가져옵니다.
        
        Args:
            timeout: 폴링 타임아웃 (초)
        
        Returns:
            파싱된 명령어 목록
            [{'command': 'pause', 'args': [], 'chat_id': 123, 'username': 'user'}]
        """
        updates = self.get_updates(timeout=timeout)
        commands = []
        
        for update in updates:
            message = update.get("message", {})
            chat_id = message.get("chat", {}).get("id")
            username = message.get("from", {}).get("username", "unknown")
            text = message.get("text", "")
            
            # 권한 확인
            if not self.is_authorized(chat_id):
                logger.warning(f"⚠️ 미인가 사용자 명령 시도: {chat_id} (@{username})")
                self.send_message(
                    "⛔ 권한이 없습니다. 관리자에게 문의하세요.",
                    chat_id=str(chat_id)
                )
                continue
            
            # 명령어 파싱
            parsed = self.parse_command(text)
            if parsed:
                parsed["chat_id"] = chat_id
                parsed["username"] = username
                commands.append(parsed)
                logger.info(f"📩 명령 수신: /{parsed['command']} {' '.join(parsed['args'])} (from @{username})")
        
        return commands
    
    def reply(self, chat_id: int, message: str) -> bool:
        """
        특정 채팅에 응답 메시지를 보냅니다.
        
        Args:
            chat_id: 응답할 Chat ID
            message: 응답 메시지
        
        Returns:
            성공 여부
        """
        return self.send_message(message, chat_id=str(chat_id))
