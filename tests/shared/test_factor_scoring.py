"""
tests/shared/test_factor_scoring.py - 팩터 스코어링 테스트
========================================================

shared/factor_scoring.py의 FactorScorer 클래스를 테스트합니다.
"""

import pytest
import pandas as pd
import numpy as np


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def scorer():
    """FactorScorer 인스턴스"""
    from shared.factor_scoring import FactorScorer
    return FactorScorer()


@pytest.fixture
def sample_daily_prices():
    """샘플 일봉 데이터 (120일)"""
    np.random.seed(42)
    base_price = 50000
    prices = [base_price]
    volumes = [1000000]
    
    for i in range(119):
        change = np.random.uniform(-0.02, 0.025)  # -2% ~ +2.5%
        prices.append(prices[-1] * (1 + change))
        volumes.append(int(np.random.uniform(800000, 1500000)))
    
    return pd.DataFrame({
        'CLOSE_PRICE': prices,
        'VOLUME': volumes
    })


@pytest.fixture
def sample_kospi_prices():
    """샘플 KOSPI 데이터 (120일)"""
    np.random.seed(123)
    base_price = 2500
    prices = [base_price]
    
    for i in range(119):
        change = np.random.uniform(-0.015, 0.015)  # -1.5% ~ +1.5%
        prices.append(prices[-1] * (1 + change))
    
    return pd.DataFrame({
        'CLOSE_PRICE': prices
    })


# ============================================================================
# Tests: calculate_momentum_score
# ============================================================================

class TestMomentumScore:
    """모멘텀 점수 계산 테스트"""
    
    def test_momentum_score_with_full_data(self, scorer, sample_daily_prices, sample_kospi_prices):
        """전체 데이터로 모멘텀 점수 계산"""
        score, factors = scorer.calculate_momentum_score(sample_daily_prices, sample_kospi_prices)
        
        assert 0 <= score <= 100
        assert 'momentum_6m_score' in factors
        assert 'momentum_1m_score' in factors
        assert 'consistency_score' in factors
    
    def test_momentum_score_range(self, scorer, sample_daily_prices, sample_kospi_prices):
        """모멘텀 점수 범위 확인"""
        score, _ = scorer.calculate_momentum_score(sample_daily_prices, sample_kospi_prices)
        
        assert 0 <= score <= 100
    
    def test_momentum_score_insufficient_data(self, scorer):
        """데이터 부족 시 중립 점수"""
        short_df = pd.DataFrame({'CLOSE_PRICE': [50000, 51000, 52000]})
        short_kospi = pd.DataFrame({'CLOSE_PRICE': [2500, 2510, 2520]})
        
        score, factors = scorer.calculate_momentum_score(short_df, short_kospi)
        
        # 데이터 부족으로 중립 점수 (30 + 10 + 10 = 50)
        assert 40 <= score <= 60
        assert factors.get('momentum_6m_note') == '데이터 부족'
    
    def test_momentum_score_no_kospi(self, scorer, sample_daily_prices):
        """KOSPI 데이터 없이 계산"""
        score, factors = scorer.calculate_momentum_score(sample_daily_prices, None)
        
        assert 0 <= score <= 100
        # KOSPI 없으면 중립 점수 사용
        assert factors['momentum_6m_score'] == 30


# ============================================================================
# Tests: calculate_quality_score
# ============================================================================

class TestQualityScore:
    """품질 점수 계산 테스트"""
    
    def test_quality_score_with_full_data(self, scorer, sample_daily_prices):
        """전체 데이터로 품질 점수 계산"""
        score, factors = scorer.calculate_quality_score(
            roe=15.0,
            sales_growth=10.0,
            eps_growth=20.0,
            daily_prices_df=sample_daily_prices
        )
        
        assert 0 <= score <= 100
        assert 'roe_score' in factors
        assert 'sales_score' in factors
        assert 'eps_score' in factors
        assert 'stability_score' in factors
    
    def test_quality_score_high_roe(self, scorer, sample_daily_prices):
        """높은 ROE"""
        score, factors = scorer.calculate_quality_score(
            roe=30.0,
            sales_growth=20.0,
            eps_growth=30.0,
            daily_prices_df=sample_daily_prices
        )
        
        assert score >= 60  # 우수 기업
    
    def test_quality_score_negative_roe(self, scorer, sample_daily_prices):
        """음수 ROE (적자 기업)"""
        score, factors = scorer.calculate_quality_score(
            roe=-10.0,
            sales_growth=-5.0,
            eps_growth=-20.0,
            daily_prices_df=sample_daily_prices
        )
        
        assert score <= 50  # 저품질
    
    def test_quality_score_none_values(self, scorer, sample_daily_prices):
        """None 값 처리"""
        score, factors = scorer.calculate_quality_score(
            roe=None,
            sales_growth=None,
            eps_growth=None,
            daily_prices_df=sample_daily_prices
        )
        
        # None이면 중립 점수 사용
        assert factors['roe_score'] == 20
        assert factors['sales_score'] == 10
        assert factors['eps_score'] == 10


# ============================================================================
# Tests: calculate_value_score
# ============================================================================

class TestValueScore:
    """가치 점수 계산 테스트"""
    
    def test_value_score_low_valuations(self, scorer):
        """저평가 종목 (낮은 PBR/PER)"""
        score, factors = scorer.calculate_value_score(pbr=0.5, per=5)
        
        assert score >= 80  # 고득점
        assert factors['pbr_score'] == 50  # 최고점
        assert factors['per_score'] == 50  # 최고점
    
    def test_value_score_high_valuations(self, scorer):
        """고평가 종목 (높은 PBR/PER)"""
        score, factors = scorer.calculate_value_score(pbr=3.0, per=30)
        
        assert score <= 20  # 저득점
    
    def test_value_score_moderate_valuations(self, scorer):
        """적정 가치 종목"""
        score, factors = scorer.calculate_value_score(pbr=1.0, per=15)
        
        # PBR 1.0 ≈ 40점, PER 15 ≈ 30점
        assert 50 <= score <= 80
    
    def test_value_score_negative_per(self, scorer):
        """음수 PER (적자 기업)"""
        score, factors = scorer.calculate_value_score(pbr=1.0, per=-10)
        
        assert factors['per_score'] == 0
        assert factors.get('per_note') == '적자 또는 데이터 없음'
    
    def test_value_score_none_values(self, scorer):
        """None 값 처리"""
        score, factors = scorer.calculate_value_score(pbr=None, per=None)
        
        assert factors['pbr_score'] == 25  # 중립
        assert factors['per_score'] == 0  # 데이터 없으면 0점


# ============================================================================
# Tests: calculate_technical_score
# ============================================================================

class TestTechnicalScore:
    """기술적 점수 계산 테스트"""
    
    def test_technical_score_with_volume(self, scorer, sample_daily_prices):
        """거래량 포함 기술적 점수"""
        score, factors = scorer.calculate_technical_score(sample_daily_prices)
        
        assert 0 <= score <= 100
        assert 'volume_score' in factors
        assert 'rsi_score' in factors
        assert 'bb_score' in factors
    
    def test_technical_score_no_volume(self, scorer):
        """거래량 없는 데이터"""
        df = pd.DataFrame({
            'CLOSE_PRICE': [50000 + i * 100 for i in range(30)]
        })
        
        score, factors = scorer.calculate_technical_score(df)
        
        assert factors['volume_score'] == 20  # 중립
    
    def test_technical_score_oversold(self, scorer):
        """과매도 상태"""
        # 급락 데이터 (RSI 낮음)
        prices = [60000 - i * 500 for i in range(30)]
        df = pd.DataFrame({
            'CLOSE_PRICE': prices,
            'VOLUME': [1000000] * 30
        })
        
        score, factors = scorer.calculate_technical_score(df)
        
        # 과매도면 RSI 점수 높음
        assert factors.get('rsi_score', 0) >= 20
    
    def test_technical_score_insufficient_data(self, scorer):
        """데이터 부족"""
        short_df = pd.DataFrame({
            'CLOSE_PRICE': [50000, 51000, 52000],
            'VOLUME': [1000000, 1100000, 1050000]
        })
        
        score, factors = scorer.calculate_technical_score(short_df)
        
        # 중립 점수
        assert 40 <= score <= 60


# ============================================================================
# Tests: calculate_final_score
# ============================================================================

class TestFinalScore:
    """최종 점수 계산 테스트"""
    
    def test_final_score_strong_bull(self, scorer):
        """급등장 가중치"""
        score, weights = scorer.calculate_final_score(
            momentum_score=80,
            quality_score=70,
            value_score=60,
            technical_score=50,
            market_regime='STRONG_BULL'
        )
        
        # 급등장: 모멘텀 40% 가중
        assert weights['applied_weights']['momentum'] == 0.40
        assert 0 <= score <= 1000
    
    def test_final_score_bear(self, scorer):
        """하락장 가중치"""
        score, weights = scorer.calculate_final_score(
            momentum_score=50,
            quality_score=80,
            value_score=70,
            technical_score=60,
            market_regime='BEAR'
        )
        
        # 하락장: 품질/가치 가중
        assert weights['applied_weights']['quality'] == 0.35
        assert weights['applied_weights']['value'] == 0.30
    
    def test_final_score_default(self, scorer):
        """기본 가중치 (BULL/SIDEWAYS)"""
        score, weights = scorer.calculate_final_score(
            momentum_score=60,
            quality_score=60,
            value_score=60,
            technical_score=60,
            market_regime='BULL'
        )
        
        # 균등 가중
        assert weights['applied_weights']['momentum'] == 0.30
        assert weights['applied_weights']['quality'] == 0.30
    
    def test_final_score_perfect(self, scorer):
        """만점"""
        score, weights = scorer.calculate_final_score(
            momentum_score=100,
            quality_score=100,
            value_score=100,
            technical_score=100,
            market_regime='BULL'
        )
        
        assert score == 1000
    
    def test_final_score_zero(self, scorer):
        """0점"""
        score, weights = scorer.calculate_final_score(
            momentum_score=0,
            quality_score=0,
            value_score=0,
            technical_score=0,
            market_regime='BULL'
        )
        
        assert score == 0
    
    def test_final_score_contribution_breakdown(self, scorer):
        """기여도 분석"""
        score, weights = scorer.calculate_final_score(
            momentum_score=80,
            quality_score=70,
            value_score=60,
            technical_score=50,
            market_regime='BULL'
        )
        
        # 기여도 합계 = 최종 점수
        total_contribution = (
            weights['momentum_contribution'] +
            weights['quality_contribution'] +
            weights['value_contribution'] +
            weights['technical_contribution']
        )
        
        assert abs(total_contribution - score) < 0.01


# ============================================================================
# Tests: Edge Cases
# ============================================================================

class TestEdgeCases:
    """Edge Cases 테스트"""
    
    def test_extreme_roe(self, scorer, sample_daily_prices):
        """극단적인 ROE"""
        # 매우 높은 ROE
        score, _ = scorer.calculate_quality_score(
            roe=100.0, sales_growth=50.0, eps_growth=100.0,
            daily_prices_df=sample_daily_prices
        )
        assert score <= 100
        
        # 매우 낮은 ROE
        score, _ = scorer.calculate_quality_score(
            roe=-50.0, sales_growth=-30.0, eps_growth=-80.0,
            daily_prices_df=sample_daily_prices
        )
        assert score >= 0
    
    def test_zero_pbr(self, scorer):
        """PBR 0 처리"""
        score, factors = scorer.calculate_value_score(pbr=0, per=10)
        
        # PBR 0은 유효하지 않으므로 중립
        assert factors['pbr_score'] == 25
    
    def test_empty_dataframe(self, scorer):
        """빈 데이터프레임"""
        empty_df = pd.DataFrame({'CLOSE_PRICE': [], 'VOLUME': []})
        
        score, factors = scorer.calculate_technical_score(empty_df)
        
        # 에러 없이 중립 점수 반환
        assert 40 <= score <= 60

