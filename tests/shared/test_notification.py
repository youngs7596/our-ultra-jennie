"""
tests/shared/test_notification.py - 알림 모듈 테스트
===================================================

shared/notification.py의 TelegramBot 클래스를 테스트합니다.
"""

import pytest
from unittest.mock import MagicMock, patch


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def telegram_bot():
    """TelegramBot 인스턴스"""
    from shared.notification import TelegramBot
    return TelegramBot(token='test-token', chat_id='test-chat-id')


@pytest.fixture
def telegram_bot_no_credentials():
    """자격 증명 없는 TelegramBot"""
    from shared.notification import TelegramBot
    return TelegramBot(token=None, chat_id=None)


# ============================================================================
# Tests: TelegramBot 초기화
# ============================================================================

class TestTelegramBotInit:
    """TelegramBot 초기화 테스트"""
    
    def test_init_with_credentials(self, telegram_bot):
        """자격 증명으로 초기화"""
        assert telegram_bot.token == 'test-token'
        assert telegram_bot.chat_id == 'test-chat-id'
        assert 'test-token' in telegram_bot.base_url
    
    def test_init_from_env(self, monkeypatch):
        """환경 변수에서 초기화"""
        from shared.notification import TelegramBot
        
        monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'env-token')
        monkeypatch.setenv('TELEGRAM_CHAT_ID', 'env-chat-id')
        
        bot = TelegramBot()
        
        assert bot.token == 'env-token'
        assert bot.chat_id == 'env-chat-id'
    
    def test_init_no_credentials(self, telegram_bot_no_credentials):
        """자격 증명 없이 초기화"""
        assert telegram_bot_no_credentials.token is None
        assert telegram_bot_no_credentials.chat_id is None


# ============================================================================
# Tests: send_message
# ============================================================================

class TestSendMessage:
    """send_message 메서드 테스트"""
    
    @patch('shared.notification.requests.post')
    def test_send_message_success(self, mock_post, telegram_bot):
        """메시지 전송 성공"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response
        
        result = telegram_bot.send_message("테스트 메시지")
        
        assert result is True
        mock_post.assert_called_once()
        
        # 올바른 URL로 호출되었는지 확인
        call_args = mock_post.call_args
        assert 'sendMessage' in call_args[0][0]
    
    @patch('shared.notification.requests.post')
    def test_send_message_with_special_chars(self, mock_post, telegram_bot):
        """특수문자 이스케이프"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response
        
        telegram_bot.send_message("*bold* _italic_ [link](url)")
        
        # payload 확인
        call_args = mock_post.call_args
        payload = call_args[1]['json']
        
        # 특수문자가 이스케이프되어야 함
        assert '\\*' in payload['text'] or '*' not in payload['text']
    
    @patch('shared.notification.requests.post')
    def test_send_message_api_error(self, mock_post, telegram_bot):
        """API 에러 처리"""
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = 'Bad Request'
        mock_response.raise_for_status.side_effect = Exception("HTTP Error")
        mock_post.return_value = mock_response
        
        result = telegram_bot.send_message("테스트 메시지")
        
        assert result is False
    
    @patch('shared.notification.requests.post')
    def test_send_message_network_error(self, mock_post, telegram_bot):
        """네트워크 에러 처리"""
        mock_post.side_effect = Exception("Network Error")
        
        result = telegram_bot.send_message("테스트 메시지")
        
        assert result is False
    
    def test_send_message_no_credentials(self, telegram_bot_no_credentials):
        """자격 증명 없으면 False 반환"""
        result = telegram_bot_no_credentials.send_message("테스트")
        
        assert result is False
    
    def test_send_message_no_token(self, monkeypatch):
        """토큰만 없는 경우"""
        from shared.notification import TelegramBot
        
        bot = TelegramBot(token=None, chat_id='some-chat-id')
        
        result = bot.send_message("테스트")
        
        assert result is False
    
    def test_send_message_no_chat_id(self, monkeypatch):
        """Chat ID만 없는 경우"""
        from shared.notification import TelegramBot
        
        bot = TelegramBot(token='some-token', chat_id=None)
        
        result = bot.send_message("테스트")
        
        assert result is False


# ============================================================================
# Tests: 메시지 포맷
# ============================================================================

class TestMessageFormat:
    """메시지 포맷 테스트"""
    
    @patch('shared.notification.requests.post')
    def test_markdown_parse_mode(self, mock_post, telegram_bot):
        """Markdown 파싱 모드"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response
        
        telegram_bot.send_message("테스트")
        
        call_args = mock_post.call_args
        payload = call_args[1]['json']
        
        assert payload['parse_mode'] == 'Markdown'
    
    @patch('shared.notification.requests.post')
    def test_chat_id_in_payload(self, mock_post, telegram_bot):
        """Chat ID가 payload에 포함"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response
        
        telegram_bot.send_message("테스트")
        
        call_args = mock_post.call_args
        payload = call_args[1]['json']
        
        assert payload['chat_id'] == 'test-chat-id'


# ============================================================================
# Tests: 타임아웃
# ============================================================================

class TestTimeout:
    """타임아웃 테스트"""
    
    @patch('shared.notification.requests.post')
    def test_request_timeout(self, mock_post, telegram_bot):
        """요청 타임아웃 설정"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response
        
        telegram_bot.send_message("테스트")
        
        call_args = mock_post.call_args
        
        # timeout 파라미터 확인
        assert call_args[1]['timeout'] == 10


# ============================================================================
# Tests: 로깅
# ============================================================================

class TestLogging:
    """로깅 테스트"""
    
    @patch('shared.notification.requests.post')
    def test_success_logging(self, mock_post, telegram_bot, caplog):
        """성공 로깅"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response
        
        with caplog.at_level('INFO'):
            telegram_bot.send_message("테스트")
        
        assert '텔레그램 알림 발송 성공' in caplog.text
    
    @patch('shared.notification.requests.post')
    def test_failure_logging(self, mock_post, telegram_bot, caplog):
        """실패 로깅"""
        mock_post.side_effect = Exception("Test Error")
        
        with caplog.at_level('ERROR'):
            telegram_bot.send_message("테스트")
        
        assert '텔레그램 알림 발송 실패' in caplog.text
    
    def test_no_credentials_warning(self, telegram_bot_no_credentials, caplog):
        """자격 증명 없음 경고"""
        with caplog.at_level('WARNING'):
            telegram_bot_no_credentials.send_message("테스트")
        
        assert '토큰 또는 Chat ID가 설정되지 않' in caplog.text

