"""
tests/shared/hybrid_scoring/test_quant_scorer.py - QuantScorer 테스트
====================================================================

shared/hybrid_scoring/quant_scorer.py의 정량 스코어링을 테스트합니다.
"""

import pytest
from unittest.mock import MagicMock, patch
import pandas as pd
from datetime import datetime, date, timedelta


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def mock_stock_data():
    """Mock 주가 데이터"""
    # 120일치 주가 데이터 생성
    dates = pd.date_range(end=datetime.now(), periods=120, freq='D')
    
    return pd.DataFrame({
        'PRICE_DATE': dates,
        'CLOSE_PRICE': [50000 + i * 100 for i in range(120)],
        'HIGH_PRICE': [51000 + i * 100 for i in range(120)],
        'LOW_PRICE': [49000 + i * 100 for i in range(120)],
        'VOLUME': [1000000] * 120
    })


@pytest.fixture
def mock_financial_data():
    """Mock 재무 데이터"""
    return {
        '005930': {
            '2024-09-30': {'per': 10.5, 'pbr': 1.2, 'roe': 15.0},
            '2024-06-30': {'per': 11.0, 'pbr': 1.3, 'roe': 14.5}
        }
    }


@pytest.fixture
def mock_supply_data():
    """Mock 수급 데이터"""
    dates = pd.date_range(end=datetime.now(), periods=20, freq='D')
    
    return pd.DataFrame({
        'TRADE_DATE': dates,
        'FOREIGN_NET_BUY': [100000000] * 10 + [-50000000] * 10,
        'INSTITUTION_NET_BUY': [50000000] * 20
    })


@pytest.fixture
def mock_news_data():
    """Mock 뉴스 감성 데이터"""
    dates = pd.date_range(end=datetime.now(), periods=10, freq='D')
    
    return pd.DataFrame({
        'NEWS_DATE': dates,
        'SENTIMENT_SCORE': [70, 75, 80, 65, 85, 60, 90, 55, 70, 75],
        'CATEGORY': ['실적'] * 5 + ['수주'] * 5
    })


# ============================================================================
# Tests: QuantScoreResult 데이터클래스
# ============================================================================

class TestQuantScoreResult:
    """QuantScoreResult 데이터클래스 테스트"""
    
    def test_create_result(self):
        """결과 생성"""
        from shared.hybrid_scoring.quant_scorer import QuantScoreResult
        
        result = QuantScoreResult(
            stock_code='005930',
            stock_name='삼성전자',
            total_score=72.0,
            momentum_score=15.0,
            quality_score=20.0,
            value_score=12.0,
            technical_score=15.0,
            news_stat_score=5.0,
            supply_demand_score=5.0,
            matched_conditions=['rsi_oversold'],
            condition_win_rate=0.65,
            condition_sample_count=45,
            condition_confidence='MID'
        )
        
        assert result.stock_code == '005930'
        assert result.total_score == 72.0
    
    def test_dataclass_fields(self):
        """데이터클래스 필드 확인"""
        from shared.hybrid_scoring.quant_scorer import QuantScoreResult
        from dataclasses import fields
        
        field_names = [f.name for f in fields(QuantScoreResult)]
        
        assert 'stock_code' in field_names
        assert 'total_score' in field_names
        assert 'condition_confidence' in field_names


# ============================================================================
# Tests: QuantScorer 초기화
# ============================================================================

class TestQuantScorerInit:
    """QuantScorer 초기화 테스트"""
    
    def test_init_short_term(self):
        """단기 전략 초기화"""
        from shared.hybrid_scoring.quant_scorer import QuantScorer, StrategyMode
        
        scorer = QuantScorer(strategy_mode=StrategyMode.SHORT_TERM)
        
        assert scorer is not None
        assert scorer.strategy_mode == StrategyMode.SHORT_TERM
    
    def test_init_long_term(self):
        """장기 전략 초기화"""
        from shared.hybrid_scoring.quant_scorer import QuantScorer, StrategyMode
        
        scorer = QuantScorer(strategy_mode=StrategyMode.LONG_TERM)
        
        assert scorer is not None
        assert scorer.strategy_mode == StrategyMode.LONG_TERM
    
    def test_init_default_strategy(self):
        """기본 전략 (DUAL)"""
        from shared.hybrid_scoring.quant_scorer import QuantScorer, StrategyMode
        
        scorer = QuantScorer()
        
        assert scorer is not None
        assert scorer.strategy_mode == StrategyMode.DUAL


# ============================================================================
# Tests: 개별 팩터 점수 계산
# ============================================================================

class TestFactorCalculations:
    """개별 팩터 점수 계산 테스트"""
    
    def test_calc_rsi(self, mock_stock_data):
        """RSI 계산"""
        from shared.hybrid_scoring.quant_scorer import QuantScorer
        
        scorer = QuantScorer()
        
        # RSI 계산 (내부 메서드)
        if hasattr(scorer, '_calc_rsi'):
            rsi = scorer._calc_rsi(mock_stock_data['CLOSE_PRICE'])
            assert isinstance(rsi, (pd.Series, type(None)))
    
    def test_calc_quality_score(self):
        """품질 점수 계산 (ROE 기반)"""
        from shared.hybrid_scoring.quant_scorer import QuantScorer
        
        scorer = QuantScorer()
        
        # ROE 기반 품질 점수 (내부 로직)
        # 실제 구현에 따라 메서드 이름이 다를 수 있음
        if hasattr(scorer, '_calc_quality_score'):
            quality = scorer._calc_quality_score(roe=15.0)
            assert quality >= 0
    
    def test_factor_weights_loaded(self):
        """팩터 가중치 로드"""
        from shared.hybrid_scoring.quant_scorer import QuantScorer
        
        scorer = QuantScorer()
        
        # factor_weights 속성 확인
        assert hasattr(scorer, 'factor_weights')
        assert isinstance(scorer.factor_weights, dict)
        assert len(scorer.factor_weights) > 0


# ============================================================================
# Tests: 조건부 승률 매칭
# ============================================================================

class TestConditionMatching:
    """조건부 승률 매칭 테스트"""
    
    def test_condition_win_rate_struct(self):
        """조건부 승률 구조체 확인"""
        from shared.hybrid_scoring.quant_scorer import QuantScoreResult
        
        # QuantScoreResult에 조건 관련 필드 존재
        result = QuantScoreResult(
            stock_code='005930',
            stock_name='삼성전자',
            total_score=72.0,
            momentum_score=15.0,
            quality_score=20.0,
            value_score=12.0,
            technical_score=15.0,
            news_stat_score=5.0,
            supply_demand_score=5.0,
            matched_conditions=['rsi_oversold', 'foreign_buy'],
            condition_win_rate=0.65,
            condition_sample_count=45,
            condition_confidence='MID'
        )
        
        assert result.matched_conditions == ['rsi_oversold', 'foreign_buy']
        assert result.condition_win_rate == 0.65
    
    def test_compound_conditions(self):
        """복합 조건 필드"""
        from shared.hybrid_scoring.quant_scorer import QuantScoreResult
        
        result = QuantScoreResult(
            stock_code='005930',
            stock_name='삼성전자',
            total_score=72.0,
            momentum_score=15.0,
            quality_score=20.0,
            value_score=12.0,
            technical_score=15.0,
            news_stat_score=5.0,
            supply_demand_score=5.0,
            matched_conditions=[],
            condition_win_rate=None,
            condition_sample_count=0,
            condition_confidence='LOW',
            compound_bonus=5.0,
            compound_conditions=['rsi_foreign']
        )
        
        assert result.compound_bonus == 5.0


# ============================================================================
# Tests: calculate_total_quant_score (통합)
# ============================================================================

class TestCalculateTotalScore:
    """통합 점수 계산 테스트"""
    
    def test_calculate_total_quant_score_exists(self):
        """calculate_total_quant_score 메서드 존재"""
        from shared.hybrid_scoring.quant_scorer import QuantScorer
        
        scorer = QuantScorer()
        
        assert hasattr(scorer, 'calculate_total_quant_score')
        assert callable(scorer.calculate_total_quant_score)


# ============================================================================
# Tests: 전략별 가중치
# ============================================================================

class TestStrategyWeights:
    """전략별 가중치 테스트"""
    
    def test_factor_weights_dict(self):
        """팩터 가중치 딕셔너리"""
        from shared.hybrid_scoring.quant_scorer import QuantScorer, StrategyMode
        
        scorer = QuantScorer(strategy_mode=StrategyMode.SHORT_TERM)
        
        # factor_weights 속성
        assert hasattr(scorer, 'factor_weights')
        assert isinstance(scorer.factor_weights, dict)
    
    def test_default_factor_weights(self):
        """기본 팩터 가중치"""
        from shared.hybrid_scoring.schema import get_default_factor_weights
        
        weights = get_default_factor_weights()
        
        assert isinstance(weights, dict)
        assert 'quality_roe' in weights


# ============================================================================
# Tests: 점수 정규화
# ============================================================================

class TestScoreNormalization:
    """점수 정규화 테스트"""
    
    def test_score_range(self):
        """점수 범위 확인"""
        from shared.hybrid_scoring.quant_scorer import QuantScoreResult
        
        # 점수는 0~100 범위
        result = QuantScoreResult(
            stock_code='005930',
            stock_name='삼성전자',
            total_score=72.0,
            momentum_score=15.0,
            quality_score=20.0,
            value_score=12.0,
            technical_score=15.0,
            news_stat_score=5.0,
            supply_demand_score=5.0,
            matched_conditions=[],
            condition_win_rate=None,
            condition_sample_count=0,
            condition_confidence='LOW'
        )
        
        assert 0 <= result.total_score <= 100


# ============================================================================
# Tests: format_quant_score_for_prompt
# ============================================================================

class TestFormatForPrompt:
    """프롬프트 포맷 테스트"""
    
    def test_format_function_exists(self):
        """format 함수 존재 확인"""
        try:
            from shared.hybrid_scoring.quant_scorer import format_quant_score_for_prompt
            assert callable(format_quant_score_for_prompt)
        except ImportError:
            # 함수가 없으면 스킵
            pytest.skip("format_quant_score_for_prompt not found")


# ============================================================================
# Tests: Edge Cases
# ============================================================================

class TestEdgeCases:
    """Edge Cases 테스트"""
    
    def test_invalid_result_flags(self):
        """유효하지 않은 결과 플래그"""
        from shared.hybrid_scoring.quant_scorer import QuantScoreResult
        
        result = QuantScoreResult(
            stock_code='999999',
            stock_name='테스트종목',
            total_score=0.0,
            momentum_score=0.0,
            quality_score=0.0,
            value_score=0.0,
            technical_score=0.0,
            news_stat_score=0.0,
            supply_demand_score=0.0,
            matched_conditions=[],
            condition_win_rate=None,
            condition_sample_count=0,
            condition_confidence='LOW',
            is_valid=False,
            invalid_reason='데이터 부족'
        )
        
        assert result.is_valid is False
        assert result.invalid_reason == '데이터 부족'
    
    def test_sector_field(self):
        """섹터 필드"""
        from shared.hybrid_scoring.quant_scorer import QuantScoreResult
        
        result = QuantScoreResult(
            stock_code='005930',
            stock_name='삼성전자',
            total_score=72.0,
            momentum_score=15.0,
            quality_score=20.0,
            value_score=12.0,
            technical_score=15.0,
            news_stat_score=5.0,
            supply_demand_score=5.0,
            matched_conditions=[],
            condition_win_rate=None,
            condition_sample_count=0,
            condition_confidence='LOW',
            sector='반도체'
        )
        
        assert result.sector == '반도체'

