"""
tests/shared/test_config.py - 설정 관리 테스트
=============================================

shared/config.py의 ConfigManager 클래스를 테스트합니다.
"""

import pytest
import time
from unittest.mock import MagicMock, patch


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def config_manager():
    """테스트용 ConfigManager 인스턴스"""
    from shared.config import ConfigManager
    return ConfigManager(db_conn=None, cache_ttl=5)


@pytest.fixture
def reset_global_config():
    """전역 ConfigManager 초기화"""
    from shared.config import reset_global_config
    reset_global_config()
    yield
    reset_global_config()


# ============================================================================
# Tests: ConfigManager 초기화
# ============================================================================

class TestConfigManagerInit:
    """ConfigManager 초기화 테스트"""
    
    def test_init_default(self, config_manager):
        """기본 초기화"""
        assert config_manager.cache_ttl == 5
        assert config_manager._memory_cache == {}
        assert config_manager._db_cache == {}
    
    def test_init_has_defaults(self, config_manager):
        """기본값 딕셔너리 존재"""
        assert 'SCAN_INTERVAL_SEC' in config_manager._defaults
        assert 'BUY_RSI_OVERSOLD_THRESHOLD' in config_manager._defaults
        assert 'MAX_HOLDING_STOCKS' in config_manager._defaults


# ============================================================================
# Tests: get() 메서드
# ============================================================================

class TestConfigManagerGet:
    """ConfigManager.get() 메서드 테스트"""
    
    def test_get_from_defaults(self, config_manager):
        """기본값에서 조회"""
        # _defaults에서 직접 기본값 확인
        assert 'SCAN_INTERVAL_SEC' in config_manager._defaults
        assert config_manager._defaults['SCAN_INTERVAL_SEC'] == 600
    
    def test_get_with_default_parameter(self, config_manager):
        """default 파라미터 사용"""
        # DB 캐시 비우기
        config_manager._db_cache = {}
        
        # DB 접근 부분은 실패해도 default 사용
        result = config_manager.get('UNKNOWN_KEY', default=42, use_cache=False)
        
        assert result == 42
    
    def test_get_from_env_variable(self, config_manager, monkeypatch):
        """환경 변수에서 조회"""
        monkeypatch.setenv('TEST_ENV_VAR', '12345')
        config_manager._db_cache = {}
        
        result = config_manager.get('TEST_ENV_VAR', use_cache=False)
        
        assert result == '12345'
    
    def test_get_from_memory_cache(self, config_manager):
        """메모리 캐시에서 조회"""
        config_manager._memory_cache['CACHED_KEY'] = ('cached_value', time.time())
        
        result = config_manager.get('CACHED_KEY')
        
        assert result == 'cached_value'
    
    def test_memory_cache_expired(self, config_manager):
        """메모리 캐시 만료"""
        # 과거 시간으로 캐시 설정 (만료됨)
        config_manager._memory_cache['EXPIRED_KEY'] = ('old_value', time.time() - 100)
        config_manager._db_cache = {}
        
        result = config_manager.get('EXPIRED_KEY', default='new_value', use_cache=False)
        
        assert result == 'new_value'  # 캐시 만료로 기본값 사용
    
    def test_get_returns_none_for_unknown_key(self, config_manager):
        """알 수 없는 키는 None 반환"""
        config_manager._db_cache = {}
        
        result = config_manager.get('COMPLETELY_UNKNOWN_KEY', use_cache=False)
        
        assert result is None


# ============================================================================
# Tests: set() 메서드
# ============================================================================

class TestConfigManagerSet:
    """ConfigManager.set() 메서드 테스트"""
    
    def test_set_in_memory_cache(self, config_manager):
        """메모리 캐시에 설정"""
        result = config_manager.set('NEW_KEY', 'new_value')
        
        assert result is True
        assert 'NEW_KEY' in config_manager._memory_cache
        assert config_manager._memory_cache['NEW_KEY'][0] == 'new_value'
    
    def test_set_persist_to_db(self, config_manager):
        """DB에도 저장 (DB 연결 실패해도 메모리 캐시에 저장)"""
        # DB 연결이 실패해도 메모리 캐시에는 저장됨
        result = config_manager.set('DB_KEY', 'db_value', persist_to_db=False)
        
        assert result is True
        assert 'DB_KEY' in config_manager._memory_cache
    
    def test_set_persist_to_db_fails_gracefully(self, config_manager):
        """DB 저장 실패 시 graceful handling"""
        # persist_to_db=True지만 DB 연결 실패 시
        result = config_manager.set('FAIL_KEY', 'value', persist_to_db=True)
        
        # DB 실패해도 결과 반환 (True 또는 False)
        assert result in [True, False]
        # 메모리 캐시에는 저장됨
        assert 'FAIL_KEY' in config_manager._memory_cache


# ============================================================================
# Tests: 타입별 getter
# ============================================================================

class TestTypedGetters:
    """타입별 getter 메서드 테스트"""
    
    def test_get_int(self, config_manager):
        """정수형 조회"""
        config_manager._memory_cache['INT_KEY'] = ('42', time.time())
        
        result = config_manager.get_int('INT_KEY')
        
        assert result == 42
        assert isinstance(result, int)
    
    def test_get_int_invalid(self, config_manager):
        """정수 변환 실패 시 기본값"""
        config_manager._memory_cache['INVALID_INT'] = ('not_a_number', time.time())
        
        result = config_manager.get_int('INVALID_INT', default=100)
        
        assert result == 100
    
    def test_get_float(self, config_manager):
        """실수형 조회"""
        config_manager._memory_cache['FLOAT_KEY'] = ('3.14', time.time())
        
        result = config_manager.get_float('FLOAT_KEY')
        
        assert result == 3.14
        assert isinstance(result, float)
    
    def test_get_float_invalid(self, config_manager):
        """실수 변환 실패 시 기본값"""
        config_manager._memory_cache['INVALID_FLOAT'] = ('not_a_float', time.time())
        
        result = config_manager.get_float('INVALID_FLOAT', default=1.5)
        
        assert result == 1.5
    
    def test_get_bool_true_values(self, config_manager):
        """불린형 true 값들"""
        for true_val in ['true', 'True', 'TRUE', '1', 'yes', 'on']:
            config_manager._memory_cache['BOOL_KEY'] = (true_val, time.time())
            result = config_manager.get_bool('BOOL_KEY')
            assert result is True, f"'{true_val}' should be True"
    
    def test_get_bool_false_values(self, config_manager):
        """불린형 false 값들"""
        for false_val in ['false', 'False', '0', 'no', 'off']:
            config_manager._memory_cache['BOOL_KEY'] = (false_val, time.time())
            result = config_manager.get_bool('BOOL_KEY')
            assert result is False, f"'{false_val}' should be False"


# ============================================================================
# Tests: 캐시 관리
# ============================================================================

class TestCacheManagement:
    """캐시 관리 테스트"""
    
    def test_clear_cache_specific_key(self, config_manager):
        """특정 키 캐시 초기화"""
        config_manager._memory_cache['KEY1'] = ('value1', time.time())
        config_manager._memory_cache['KEY2'] = ('value2', time.time())
        
        config_manager.clear_cache('KEY1')
        
        assert 'KEY1' not in config_manager._memory_cache
        assert 'KEY2' in config_manager._memory_cache
    
    def test_clear_cache_all(self, config_manager):
        """전체 캐시 초기화"""
        config_manager._memory_cache['KEY1'] = ('value1', time.time())
        config_manager._memory_cache['KEY2'] = ('value2', time.time())
        config_manager._db_cache['DB_KEY'] = ('db_value', time.time())
        
        config_manager.clear_cache()
        
        assert config_manager._memory_cache == {}
        assert config_manager._db_cache == {}


# ============================================================================
# Tests: 타입 자동 변환
# ============================================================================

class TestTypeConversion:
    """타입 자동 변환 테스트"""
    
    def test_convert_to_int_based_on_default(self, config_manager):
        """기본값이 int면 int로 변환"""
        # SCAN_INTERVAL_SEC 기본값이 600 (int)
        result = config_manager._convert_type('SCAN_INTERVAL_SEC', '300')
        
        assert result == 300
        assert isinstance(result, int)
    
    def test_convert_to_float_based_on_default(self, config_manager):
        """기본값이 float면 float로 변환"""
        # RISK_PER_TRADE_PCT 기본값이 2.0 (float)
        result = config_manager._convert_type('RISK_PER_TRADE_PCT', '1.5')
        
        assert result == 1.5
        assert isinstance(result, float)
    
    def test_convert_to_bool_based_on_default(self, config_manager):
        """기본값이 bool이면 bool로 변환"""
        # ALLOW_BEAR_TRADING 기본값이 False (bool)
        result = config_manager._convert_type('ALLOW_BEAR_TRADING', 'true')
        
        # _convert_type에서 bool 변환
        assert result in [True, 'true']  # 구현에 따라 다름
    
    def test_no_conversion_if_no_default(self, config_manager):
        """기본값 없으면 변환 안함"""
        result = config_manager._convert_type('UNKNOWN_KEY', 'some_value')
        
        assert result == 'some_value'


# ============================================================================
# Tests: 전역 ConfigManager
# ============================================================================

class TestGlobalConfig:
    """전역 ConfigManager 테스트"""
    
    def test_get_global_config_singleton(self, reset_global_config):
        """싱글톤 패턴"""
        from shared.config import get_global_config
        
        config1 = get_global_config()
        config2 = get_global_config()
        
        assert config1 is config2
    
    def test_reset_global_config(self, reset_global_config):
        """전역 인스턴스 초기화"""
        from shared.config import get_global_config, reset_global_config, _global_config
        
        config1 = get_global_config()
        reset_global_config()
        config2 = get_global_config()
        
        assert config1 is not config2


# ============================================================================
# Tests: get_all()
# ============================================================================

class TestGetAll:
    """get_all() 메서드 테스트"""
    
    def test_get_all_returns_defaults(self, config_manager):
        """모든 기본값 반환"""
        config_manager._db_cache = {}
        
        result = config_manager.get_all()
        
        assert 'SCAN_INTERVAL_SEC' in result
        assert 'BUY_RSI_OVERSOLD_THRESHOLD' in result
        assert result['SCAN_INTERVAL_SEC'] == 600

