"""
tests/shared/hybrid_scoring/test_schema.py - Schema 순수 함수 테스트
===================================================================

shared/hybrid_scoring/schema.py의 순수 함수들을 테스트합니다.
"""

import pytest
from unittest.mock import MagicMock, patch


# ============================================================================
# Tests: get_confidence_level
# ============================================================================

class TestGetConfidenceLevel:
    """get_confidence_level 함수 테스트"""
    
    def test_high_confidence(self):
        """30개 이상 → HIGH"""
        from shared.hybrid_scoring.schema import get_confidence_level
        
        assert get_confidence_level(30) == 'HIGH'
        assert get_confidence_level(50) == 'HIGH'
        assert get_confidence_level(100) == 'HIGH'
    
    def test_mid_confidence(self):
        """15~29개 → MID"""
        from shared.hybrid_scoring.schema import get_confidence_level
        
        assert get_confidence_level(15) == 'MID'
        assert get_confidence_level(20) == 'MID'
        assert get_confidence_level(29) == 'MID'
    
    def test_low_confidence(self):
        """15개 미만 → LOW"""
        from shared.hybrid_scoring.schema import get_confidence_level
        
        assert get_confidence_level(0) == 'LOW'
        assert get_confidence_level(5) == 'LOW'
        assert get_confidence_level(14) == 'LOW'


# ============================================================================
# Tests: get_confidence_weight
# ============================================================================

class TestGetConfidenceWeight:
    """get_confidence_weight 함수 테스트"""
    
    def test_full_weight(self):
        """30개 이상 → 100%"""
        from shared.hybrid_scoring.schema import get_confidence_weight
        
        assert get_confidence_weight(30) == 1.0
        assert get_confidence_weight(50) == 1.0
    
    def test_high_weight(self):
        """20~29개 → 80%"""
        from shared.hybrid_scoring.schema import get_confidence_weight
        
        assert get_confidence_weight(20) == 0.8
        assert get_confidence_weight(25) == 0.8
        assert get_confidence_weight(29) == 0.8
    
    def test_medium_weight(self):
        """10~19개 → 50%"""
        from shared.hybrid_scoring.schema import get_confidence_weight
        
        assert get_confidence_weight(10) == 0.5
        assert get_confidence_weight(15) == 0.5
        assert get_confidence_weight(19) == 0.5
    
    def test_low_weight(self):
        """5~9개 → 30%"""
        from shared.hybrid_scoring.schema import get_confidence_weight
        
        assert get_confidence_weight(5) == 0.3
        assert get_confidence_weight(7) == 0.3
        assert get_confidence_weight(9) == 0.3
    
    def test_zero_weight(self):
        """5개 미만 → 0%"""
        from shared.hybrid_scoring.schema import get_confidence_weight
        
        assert get_confidence_weight(0) == 0.0
        assert get_confidence_weight(4) == 0.0


# ============================================================================
# Tests: get_default_factor_weights
# ============================================================================

class TestGetDefaultFactorWeights:
    """get_default_factor_weights 함수 테스트"""
    
    def test_returns_dict(self):
        """딕셔너리 반환"""
        from shared.hybrid_scoring.schema import get_default_factor_weights
        
        weights = get_default_factor_weights()
        
        assert isinstance(weights, dict)
        assert len(weights) > 0
    
    def test_core_factors_exist(self):
        """핵심 팩터 존재"""
        from shared.hybrid_scoring.schema import get_default_factor_weights
        
        weights = get_default_factor_weights()
        
        # 핵심 팩터
        assert 'quality_roe' in weights
        assert 'technical_rsi' in weights
        assert 'compound_rsi_foreign' in weights
    
    def test_weights_sum_approximately_one(self):
        """가중치 합계 약 1.0"""
        from shared.hybrid_scoring.schema import get_default_factor_weights
        
        weights = get_default_factor_weights()
        
        # 음수 팩터 제외한 합계
        positive_sum = sum(v for v in weights.values() if v > 0)
        
        # 대략 1.0 근처 (음수 팩터가 있어서 정확히 1.0은 아님)
        assert 0.9 <= positive_sum <= 1.2
    
    def test_roe_highest_weight(self):
        """ROE가 가장 높은 가중치"""
        from shared.hybrid_scoring.schema import get_default_factor_weights
        
        weights = get_default_factor_weights()
        
        assert weights['quality_roe'] >= 0.20  # 25% 예상


# ============================================================================
# Tests: is_oracle / _is_mariadb
# ============================================================================

class TestDbTypeDetection:
    """DB 타입 감지 함수 테스트"""
    
    def test_is_oracle_default(self, monkeypatch):
        """기본값은 Oracle"""
        from shared.hybrid_scoring.schema import is_oracle
        
        monkeypatch.delenv('DB_TYPE', raising=False)
        
        assert is_oracle() is True
    
    def test_is_oracle_explicit(self, monkeypatch):
        """명시적 Oracle 설정"""
        from shared.hybrid_scoring.schema import is_oracle
        
        monkeypatch.setenv('DB_TYPE', 'ORACLE')
        
        assert is_oracle() is True
    
    def test_is_mariadb(self, monkeypatch):
        """MariaDB 설정"""
        from shared.hybrid_scoring.schema import is_oracle, _is_mariadb
        
        monkeypatch.setenv('DB_TYPE', 'MARIADB')
        
        assert is_oracle() is False
        assert _is_mariadb() is True
    
    def test_case_insensitive(self, monkeypatch):
        """대소문자 구분 안함"""
        from shared.hybrid_scoring.schema import is_oracle
        
        monkeypatch.setenv('DB_TYPE', 'oracle')
        assert is_oracle() is True
        
        monkeypatch.setenv('DB_TYPE', 'mariadb')
        assert is_oracle() is False


# ============================================================================
# Tests: execute_upsert
# ============================================================================

class TestExecuteUpsert:
    """execute_upsert 함수 테스트"""
    
    def test_mariadb_upsert(self, monkeypatch):
        """MariaDB UPSERT (ON DUPLICATE KEY UPDATE)"""
        from shared.hybrid_scoring.schema import execute_upsert
        
        monkeypatch.setenv('DB_TYPE', 'MARIADB')
        
        mock_cursor = MagicMock()
        
        result = execute_upsert(
            cursor=mock_cursor,
            table_name='TEST_TABLE',
            columns=['ID', 'NAME', 'VALUE'],
            values=(1, 'test', 100),
            unique_keys=['ID'],
            update_columns=['NAME', 'VALUE']
        )
        
        assert result is True
        mock_cursor.execute.assert_called_once()
        
        # SQL에 ON DUPLICATE KEY UPDATE 포함 확인
        call_args = mock_cursor.execute.call_args[0][0]
        assert 'ON DUPLICATE KEY UPDATE' in call_args
    
    def test_oracle_upsert(self, monkeypatch):
        """Oracle UPSERT (MERGE INTO)"""
        from shared.hybrid_scoring.schema import execute_upsert
        
        monkeypatch.setenv('DB_TYPE', 'ORACLE')
        
        mock_cursor = MagicMock()
        
        result = execute_upsert(
            cursor=mock_cursor,
            table_name='TEST_TABLE',
            columns=['ID', 'NAME', 'VALUE'],
            values=(1, 'test', 100),
            unique_keys=['ID'],
            update_columns=['NAME', 'VALUE']
        )
        
        assert result is True
        mock_cursor.execute.assert_called_once()
        
        # SQL에 MERGE INTO 포함 확인
        call_args = mock_cursor.execute.call_args[0][0]
        assert 'MERGE INTO' in call_args
    
    def test_auto_update_columns(self, monkeypatch):
        """update_columns 자동 생성"""
        from shared.hybrid_scoring.schema import execute_upsert
        
        monkeypatch.setenv('DB_TYPE', 'MARIADB')
        
        mock_cursor = MagicMock()
        
        # update_columns=None이면 unique_keys 제외 전체
        result = execute_upsert(
            cursor=mock_cursor,
            table_name='TEST_TABLE',
            columns=['ID', 'NAME', 'VALUE'],
            values=(1, 'test', 100),
            unique_keys=['ID'],
            update_columns=None  # 자동 생성
        )
        
        assert result is True
        
        # NAME, VALUE가 업데이트 대상이어야 함
        call_args = mock_cursor.execute.call_args[0][0]
        assert 'NAME' in call_args
        assert 'VALUE' in call_args


# ============================================================================
# Tests: create_hybrid_scoring_tables
# ============================================================================

class TestCreateHybridScoringTables:
    """create_hybrid_scoring_tables 함수 테스트"""
    
    def test_mariadb_tables(self, monkeypatch):
        """MariaDB 테이블 생성"""
        from shared.hybrid_scoring.schema import create_hybrid_scoring_tables
        
        monkeypatch.setenv('DB_TYPE', 'MARIADB')
        
        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        
        result = create_hybrid_scoring_tables(mock_conn)
        
        assert result is True
        mock_conn.commit.assert_called_once()
    
    def test_oracle_tables(self, monkeypatch):
        """Oracle 테이블 생성"""
        from shared.hybrid_scoring.schema import create_hybrid_scoring_tables
        
        monkeypatch.setenv('DB_TYPE', 'ORACLE')
        
        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        
        result = create_hybrid_scoring_tables(mock_conn)
        
        assert result is True
        mock_conn.commit.assert_called_once()
    
    def test_handles_existing_tables(self, monkeypatch):
        """이미 존재하는 테이블 처리"""
        from shared.hybrid_scoring.schema import create_hybrid_scoring_tables
        
        monkeypatch.setenv('DB_TYPE', 'MARIADB')
        
        mock_cursor = MagicMock()
        # 일부 쿼리가 "already exists" 에러 발생
        mock_cursor.execute.side_effect = [None, Exception("already exists"), None]
        
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        
        # 에러가 발생해도 True 반환 (graceful handling)
        result = create_hybrid_scoring_tables(mock_conn)
        
        assert result is True

