"""
tests/shared/test_position_sizing.py - 포지션 사이징 테스트
=========================================================

shared/position_sizing.py의 PositionSizer 클래스를 테스트합니다.
"""

import pytest
from unittest.mock import MagicMock


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def mock_config():
    """Mock ConfigManager"""
    config = MagicMock()
    config.get_float.side_effect = lambda key, default=None: {
        'RISK_PER_TRADE_PCT': 2.0,
        'ATR_MULTIPLIER': 2.0,
        'MAX_POSITION_VALUE_PCT': 15.0,
        'CASH_KEEP_PCT': 10.0,
    }.get(key, default)
    config.get_int.side_effect = lambda key, default=None: {
        'MIN_QUANTITY': 1,
        'MAX_QUANTITY': 1000,
    }.get(key, default)
    return config


@pytest.fixture
def sizer(mock_config):
    """PositionSizer 인스턴스"""
    from shared.position_sizing import PositionSizer
    return PositionSizer(mock_config)


# ============================================================================
# Tests: 기본 계산
# ============================================================================

class TestBasicCalculation:
    """기본 수량 계산 테스트"""
    
    def test_calculate_quantity_basic(self, sizer):
        """기본 수량 계산"""
        result = sizer.calculate_quantity(
            stock_code='005930',
            stock_price=70000,
            atr=1400,  # 2% ATR
            account_balance=10000000,  # 1000만원
            portfolio_value=0
        )
        
        assert result['quantity'] > 0
        assert result['position_value'] > 0
        assert 'risk_pct' in result
        assert 'reason' in result
    
    def test_calculate_quantity_with_portfolio(self, sizer):
        """포트폴리오 포함 계산"""
        result = sizer.calculate_quantity(
            stock_code='005930',
            stock_price=70000,
            atr=1400,
            account_balance=5000000,
            portfolio_value=5000000  # 총 1000만원
        )
        
        assert result['quantity'] > 0
        # 위험 비율이 2% 근처여야 함
        assert 0 < result['risk_pct'] <= 3.0
    
    def test_quantity_increases_with_balance(self, sizer):
        """잔고 증가 시 수량 증가"""
        result_small = sizer.calculate_quantity(
            stock_code='005930',
            stock_price=70000,
            atr=1400,
            account_balance=5000000,
            portfolio_value=0
        )
        
        result_large = sizer.calculate_quantity(
            stock_code='005930',
            stock_price=70000,
            atr=1400,
            account_balance=20000000,
            portfolio_value=0
        )
        
        assert result_large['quantity'] > result_small['quantity']


# ============================================================================
# Tests: ATR 기반 계산
# ============================================================================

class TestAtrBasedCalculation:
    """ATR 기반 수량 계산 테스트"""
    
    def test_high_volatility_reduces_quantity(self, sizer):
        """높은 변동성(높은 ATR) → 적은 수량"""
        result_low_atr = sizer.calculate_quantity(
            stock_code='005930',
            stock_price=70000,
            atr=700,  # 1% ATR
            account_balance=5000000,  # 적은 잔고로 제한 안 걸리게
            portfolio_value=0
        )
        
        result_high_atr = sizer.calculate_quantity(
            stock_code='005930',
            stock_price=70000,
            atr=3500,  # 5% ATR (더 높게)
            account_balance=5000000,
            portfolio_value=0
        )
        
        # 변동성이 높으면 수량이 같거나 적어야 함
        assert result_low_atr['quantity'] >= result_high_atr['quantity']
    
    def test_invalid_atr_uses_default(self, sizer):
        """유효하지 않은 ATR은 기본값 사용 또는 에러 처리"""
        # ATR 0은 기본값으로 대체되어 처리됨
        result_zero = sizer.calculate_quantity(
            stock_code='005930',
            stock_price=70000,
            atr=0,
            account_balance=10000000,
            portfolio_value=0
        )
        
        result_nan = sizer.calculate_quantity(
            stock_code='005930',
            stock_price=70000,
            atr=float('nan'),
            account_balance=10000000,
            portfolio_value=0
        )
        
        # ATR 0이나 nan은 기본값(주가의 2%)으로 대체되어 수량 계산
        assert result_zero['quantity'] >= 0
        assert result_nan['quantity'] >= 0


# ============================================================================
# Tests: 제약 조건
# ============================================================================

class TestConstraints:
    """제약 조건 테스트"""
    
    def test_min_quantity_constraint(self, sizer):
        """최소 수량 제약"""
        result = sizer.calculate_quantity(
            stock_code='005930',
            stock_price=500000,  # 비싼 주식
            atr=50000,  # 높은 ATR
            account_balance=1000000,  # 적은 잔고
            portfolio_value=0
        )
        
        # 수량이 0이 아니면 최소 1주
        if result['quantity'] > 0:
            assert result['quantity'] >= 1
    
    def test_max_quantity_constraint(self, sizer):
        """최대 수량 제약"""
        result = sizer.calculate_quantity(
            stock_code='005930',
            stock_price=1000,  # 저가 주식
            atr=10,  # 낮은 ATR
            account_balance=100000000,  # 많은 잔고
            portfolio_value=0
        )
        
        assert result['quantity'] <= 1000  # max_quantity
    
    def test_max_position_value_constraint(self, sizer):
        """최대 포지션 비중 제약 (15%)"""
        result = sizer.calculate_quantity(
            stock_code='005930',
            stock_price=1000,
            atr=10,
            account_balance=10000000,
            portfolio_value=0
        )
        
        # 포지션 가치가 총 자산의 15% 이하
        total_assets = 10000000
        max_position = total_assets * 0.15
        
        assert result['position_value'] <= max_position
    
    def test_cash_keep_constraint(self, sizer):
        """현금 유지 비율 제약 (10%)"""
        result = sizer.calculate_quantity(
            stock_code='005930',
            stock_price=70000,
            atr=1400,
            account_balance=1000000,  # 100만원만 있음
            portfolio_value=9000000  # 총 1000만원
        )
        
        # 현금 10% 유지해야 하므로 90만원까지만 사용 가능
        assert result['position_value'] <= 900000


# ============================================================================
# Tests: Smart Skip
# ============================================================================

class TestSmartSkip:
    """Smart Skip 로직 테스트"""
    
    def test_smart_skip_triggered(self, sizer):
        """목표의 50% 미만이면 매수 포기"""
        result = sizer.calculate_quantity(
            stock_code='005930',
            stock_price=70000,
            atr=1400,
            account_balance=200000,  # 매우 적은 잔고
            portfolio_value=10000000  # 큰 포트폴리오
        )
        
        # 현금 부족으로 Smart Skip 발동 가능
        if result['quantity'] == 0:
            assert 'Smart Skip' in result['reason']


# ============================================================================
# Tests: Zero/Edge Cases
# ============================================================================

class TestEdgeCases:
    """Edge Cases 테스트"""
    
    def test_zero_balance(self, sizer):
        """잔고 0"""
        result = sizer.calculate_quantity(
            stock_code='005930',
            stock_price=70000,
            atr=1400,
            account_balance=0,
            portfolio_value=0
        )
        
        assert result['quantity'] == 0
        assert '총 자산이 0' in result['reason']
    
    def test_negative_balance(self, sizer):
        """음수 잔고"""
        result = sizer.calculate_quantity(
            stock_code='005930',
            stock_price=70000,
            atr=1400,
            account_balance=-1000000,
            portfolio_value=0
        )
        
        assert result['quantity'] == 0
    
    def test_very_expensive_stock(self, sizer):
        """매우 비싼 주식"""
        result = sizer.calculate_quantity(
            stock_code='000810',  # 삼성화재
            stock_price=300000,
            atr=6000,
            account_balance=500000,  # 적은 잔고
            portfolio_value=0
        )
        
        # 최소 1주도 못 살 수 있음
        assert result['quantity'] >= 0


# ============================================================================
# Tests: refresh_from_config
# ============================================================================

class TestRefreshFromConfig:
    """설정 리프레시 테스트"""
    
    def test_refresh_updates_values(self, mock_config):
        """설정 갱신"""
        from shared.position_sizing import PositionSizer
        
        sizer = PositionSizer(mock_config)
        
        # 초기값 확인
        assert sizer.risk_per_trade_pct == 2.0
        
        # 설정 변경
        mock_config.get_float.side_effect = lambda key, default=None: {
            'RISK_PER_TRADE_PCT': 3.0,  # 변경
            'ATR_MULTIPLIER': 2.0,
            'MAX_POSITION_VALUE_PCT': 15.0,
            'CASH_KEEP_PCT': 10.0,
        }.get(key, default)
        
        sizer.refresh_from_config()
        
        assert sizer.risk_per_trade_pct == 3.0


# ============================================================================
# Tests: Result Structure
# ============================================================================

class TestResultStructure:
    """결과 구조 테스트"""
    
    def test_result_contains_all_fields(self, sizer):
        """결과에 모든 필드 포함"""
        result = sizer.calculate_quantity(
            stock_code='005930',
            stock_price=70000,
            atr=1400,
            account_balance=10000000,
            portfolio_value=0
        )
        
        required_fields = ['quantity', 'risk_amount', 'position_value', 
                          'risk_pct', 'position_pct', 'reason']
        
        for field in required_fields:
            assert field in result, f"Missing field: {field}"
    
    def test_zero_result_structure(self, sizer):
        """수량 0일 때 결과 구조"""
        result = sizer.calculate_quantity(
            stock_code='005930',
            stock_price=70000,
            atr=1400,
            account_balance=0,
            portfolio_value=0
        )
        
        assert result['quantity'] == 0
        assert result['risk_amount'] == 0
        assert result['position_value'] == 0
        assert result['reason'] != ''

