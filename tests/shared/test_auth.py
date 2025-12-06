"""
tests/shared/test_auth.py - 인증 및 시크릿 관리 테스트
===================================================

shared/auth.py의 get_secret 함수를 테스트합니다.
"""

import pytest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture(autouse=True)
def clear_cache():
    """각 테스트 전후로 캐시 초기화"""
    from shared.auth import clear_secret_cache
    clear_secret_cache()
    yield
    clear_secret_cache()


@pytest.fixture
def temp_secrets_file():
    """임시 secrets.json 파일 생성"""
    secrets_data = {
        "gemini-api-key": "test-gemini-key",
        "openai-api-key": "test-openai-key",
        "telegram_bot_token": "test-telegram-token",
        "kis-r-app-key": "test-kis-key"
    }
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(secrets_data, f)
        temp_path = f.name
    
    yield temp_path
    
    # 정리
    Path(temp_path).unlink(missing_ok=True)


# ============================================================================
# Tests: get_secret 기본 동작
# ============================================================================

class TestGetSecret:
    """get_secret 함수 테스트"""
    
    def test_get_secret_from_json_file(self, temp_secrets_file, monkeypatch):
        """secrets.json에서 시크릿 조회"""
        from shared.auth import get_secret, clear_secret_cache
        
        clear_secret_cache()
        monkeypatch.setenv('SECRETS_FILE', temp_secrets_file)
        
        result = get_secret('gemini-api-key')
        
        assert result == 'test-gemini-key'
    
    def test_get_secret_with_underscore_conversion(self, temp_secrets_file, monkeypatch):
        """하이픈/언더스코어 변환"""
        from shared.auth import get_secret, clear_secret_cache
        
        clear_secret_cache()
        monkeypatch.setenv('SECRETS_FILE', temp_secrets_file)
        
        # telegram_bot_token으로 저장되어 있음
        result = get_secret('telegram-bot-token')
        
        assert result == 'test-telegram-token'
    
    def test_get_secret_from_env_variable(self, monkeypatch):
        """환경 변수에서 시크릿 조회"""
        from shared.auth import get_secret, clear_secret_cache
        
        clear_secret_cache()
        # secrets.json 경로를 존재하지 않는 경로로 설정
        monkeypatch.setenv('SECRETS_FILE', '/nonexistent/secrets.json')
        monkeypatch.setenv('MY_SECRET_KEY', 'env-secret-value')
        
        result = get_secret('MY_SECRET_KEY')
        
        assert result == 'env-secret-value'
    
    def test_get_secret_not_found(self, monkeypatch):
        """시크릿 찾지 못함"""
        from shared.auth import get_secret, clear_secret_cache
        
        clear_secret_cache()
        monkeypatch.setenv('SECRETS_FILE', '/nonexistent/secrets.json')
        # 환경 변수에도 없음
        
        result = get_secret('NONEXISTENT_SECRET')
        
        assert result is None
    
    def test_get_secret_caching(self, temp_secrets_file, monkeypatch):
        """캐싱 동작 확인"""
        from shared.auth import get_secret, clear_secret_cache, _secret_cache
        
        clear_secret_cache()
        monkeypatch.setenv('SECRETS_FILE', temp_secrets_file)
        
        # 첫 번째 호출
        result1 = get_secret('gemini-api-key')
        
        # 캐시에 저장되었는지 확인
        assert 'local/gemini-api-key' in _secret_cache
        
        # 두 번째 호출 (캐시 사용)
        result2 = get_secret('gemini-api-key')
        
        assert result1 == result2
    
    def test_get_secret_no_cache(self, temp_secrets_file, monkeypatch):
        """캐시 비활성화"""
        from shared.auth import get_secret, clear_secret_cache, _secret_cache
        
        clear_secret_cache()
        monkeypatch.setenv('SECRETS_FILE', temp_secrets_file)
        
        result = get_secret('gemini-api-key', use_cache=False)
        
        # 캐시에 저장 안됨
        assert 'local/gemini-api-key' not in _secret_cache
        assert result == 'test-gemini-key'
    
    def test_get_secret_with_project_id(self, temp_secrets_file, monkeypatch):
        """project_id로 캐시 키 스코프 지정"""
        from shared.auth import get_secret, clear_secret_cache, _secret_cache
        
        clear_secret_cache()
        monkeypatch.setenv('SECRETS_FILE', temp_secrets_file)
        
        result = get_secret('gemini-api-key', project_id='my-project')
        
        assert 'my-project/gemini-api-key' in _secret_cache
        assert result == 'test-gemini-key'


# ============================================================================
# Tests: 환경 변수 매핑
# ============================================================================

class TestEnvMapping:
    """환경 변수 매핑 테스트"""
    
    def test_local_env_mapping_gemini(self, monkeypatch):
        """GEMINI_API_KEY 환경 변수 매핑"""
        from shared.auth import get_secret, clear_secret_cache
        
        clear_secret_cache()
        monkeypatch.setenv('SECRETS_FILE', '/nonexistent/secrets.json')
        monkeypatch.setenv('GEMINI_API_KEY', 'mapped-gemini-key')
        
        result = get_secret('gemini-api-key')
        
        assert result == 'mapped-gemini-key'
    
    def test_local_env_mapping_oracle(self, monkeypatch):
        """ORACLE_USER 환경 변수 매핑"""
        from shared.auth import get_secret, clear_secret_cache
        
        clear_secret_cache()
        monkeypatch.setenv('SECRETS_FILE', '/nonexistent/secrets.json')
        monkeypatch.setenv('ORACLE_USER', 'oracle_user_name')
        
        result = get_secret('oracle-db-user')
        
        assert result == 'oracle_user_name'


# ============================================================================
# Tests: secrets.json 로딩
# ============================================================================

class TestLoadLocalSecrets:
    """_load_local_secrets 함수 테스트"""
    
    def test_load_valid_json(self, temp_secrets_file, monkeypatch):
        """유효한 JSON 로딩"""
        from shared.auth import _load_local_secrets, clear_secret_cache
        
        clear_secret_cache()
        monkeypatch.setenv('SECRETS_FILE', temp_secrets_file)
        
        secrets = _load_local_secrets()
        
        assert 'gemini-api-key' in secrets
        assert secrets['gemini-api-key'] == 'test-gemini-key'
    
    def test_load_invalid_json(self, monkeypatch):
        """유효하지 않은 JSON"""
        from shared.auth import _load_local_secrets, clear_secret_cache
        
        clear_secret_cache()
        
        # 유효하지 않은 JSON 파일 생성
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write("not valid json")
            temp_path = f.name
        
        try:
            monkeypatch.setenv('SECRETS_FILE', temp_path)
            secrets = _load_local_secrets()
            
            assert secrets == {}  # 파싱 실패 시 빈 dict
        finally:
            Path(temp_path).unlink(missing_ok=True)
    
    def test_load_nonexistent_file(self, monkeypatch):
        """존재하지 않는 파일"""
        from shared.auth import _load_local_secrets, clear_secret_cache
        
        clear_secret_cache()
        monkeypatch.setenv('SECRETS_FILE', '/absolutely/nonexistent/path.json')
        
        secrets = _load_local_secrets()
        
        assert secrets == {}
    
    def test_caching_local_secrets(self, temp_secrets_file, monkeypatch):
        """로컬 시크릿 캐싱"""
        from shared.auth import _load_local_secrets, clear_secret_cache, _local_secrets_cache
        
        clear_secret_cache()
        monkeypatch.setenv('SECRETS_FILE', temp_secrets_file)
        
        # 첫 번째 호출
        secrets1 = _load_local_secrets()
        
        # 두 번째 호출 (캐시 사용)
        secrets2 = _load_local_secrets()
        
        assert secrets1 is secrets2


# ============================================================================
# Tests: clear_secret_cache
# ============================================================================

class TestClearSecretCache:
    """clear_secret_cache 함수 테스트"""
    
    def test_clears_all_caches(self, temp_secrets_file, monkeypatch):
        """모든 캐시 초기화"""
        from shared.auth import get_secret, clear_secret_cache, _secret_cache, _local_secrets_cache
        
        monkeypatch.setenv('SECRETS_FILE', temp_secrets_file)
        
        # 시크릿 조회하여 캐시 채우기
        get_secret('gemini-api-key')
        
        assert len(_secret_cache) > 0
        
        # 캐시 초기화
        clear_secret_cache()
        
        # auth 모듈의 전역 변수 직접 확인
        from shared import auth
        assert auth._secret_cache == {}
        assert auth._local_secrets_cache is None


# ============================================================================
# Tests: Edge Cases
# ============================================================================

class TestEdgeCases:
    """Edge Cases 테스트"""
    
    def test_secret_value_with_whitespace(self, monkeypatch):
        """시크릿 값에 공백 포함"""
        from shared.auth import clear_secret_cache
        
        clear_secret_cache()
        
        secrets_data = {"test-key": "  value with spaces  "}
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(secrets_data, f)
            temp_path = f.name
        
        try:
            monkeypatch.setenv('SECRETS_FILE', temp_path)
            
            from shared.auth import get_secret
            result = get_secret('test-key')
            
            # 공백이 strip 되어야 함
            assert result == 'value with spaces'
        finally:
            Path(temp_path).unlink(missing_ok=True)
    
    def test_json_with_non_string_values(self, monkeypatch):
        """JSON에 문자열이 아닌 값"""
        from shared.auth import _load_local_secrets, clear_secret_cache
        
        clear_secret_cache()
        
        secrets_data = {
            "string-key": "string-value",
            "int-key": 12345,
            "bool-key": True
        }
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(secrets_data, f)
            temp_path = f.name
        
        try:
            monkeypatch.setenv('SECRETS_FILE', temp_path)
            secrets = _load_local_secrets()
            
            # 모든 값이 문자열로 변환됨
            assert secrets['string-key'] == 'string-value'
            assert secrets['int-key'] == '12345'
            assert secrets['bool-key'] == 'True'
        finally:
            Path(temp_path).unlink(missing_ok=True)

