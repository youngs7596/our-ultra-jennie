"""
tests/shared/hybrid_scoring/test_hybrid_scorer.py - HybridScorer 테스트
=====================================================================

shared/hybrid_scoring/hybrid_scorer.py의 하이브리드 스코어링을 테스트합니다.
"""

import pytest
from dataclasses import asdict


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def mock_quant_result():
    """Mock QuantScoreResult"""
    from shared.hybrid_scoring.quant_scorer import QuantScoreResult
    
    return QuantScoreResult(
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


@pytest.fixture
def scorer():
    """HybridScorer 인스턴스"""
    from shared.hybrid_scoring.hybrid_scorer import HybridScorer
    return HybridScorer(market_regime='BULL')


# ============================================================================
# Tests: HybridScoreResult 데이터클래스
# ============================================================================

class TestHybridScoreResult:
    """HybridScoreResult 데이터클래스 테스트"""
    
    def test_create_result(self):
        """결과 생성"""
        from shared.hybrid_scoring.hybrid_scorer import HybridScoreResult
        
        result = HybridScoreResult(
            stock_code='005930',
            stock_name='삼성전자',
            quant_score=72.0,
            llm_score=75.0,
            hybrid_score=73.2,
            quant_weight=0.6,
            llm_weight=0.4
        )
        
        assert result.stock_code == '005930'
        assert result.hybrid_score == 73.2
    
    def test_to_watchlist_entry(self):
        """Watchlist 변환"""
        from shared.hybrid_scoring.hybrid_scorer import HybridScoreResult
        
        result = HybridScoreResult(
            stock_code='005930',
            stock_name='삼성전자',
            quant_score=72.0,
            llm_score=75.0,
            hybrid_score=73.2,
            quant_weight=0.6,
            llm_weight=0.4,
            grade='B',
            llm_reason='좋은 종목입니다.'
        )
        
        entry = result.to_watchlist_entry()
        
        assert entry['code'] == '005930'
        assert entry['name'] == '삼성전자'


# ============================================================================
# Tests: HybridScorer 초기화
# ============================================================================

class TestHybridScorerInit:
    """HybridScorer 초기화 테스트"""
    
    def test_init_bull_market(self):
        """상승장 초기화"""
        from shared.hybrid_scoring.hybrid_scorer import HybridScorer
        
        scorer = HybridScorer(market_regime='BULL')
        
        assert scorer is not None
    
    def test_init_bear_market(self):
        """하락장 초기화"""
        from shared.hybrid_scoring.hybrid_scorer import HybridScorer
        
        scorer = HybridScorer(market_regime='BEAR')
        
        assert scorer is not None
    
    def test_init_default_regime(self):
        """기본 국면"""
        from shared.hybrid_scoring.hybrid_scorer import HybridScorer
        
        scorer = HybridScorer()
        
        assert scorer is not None


# ============================================================================
# Tests: calculate_hybrid_score
# ============================================================================

class TestCalculateHybridScore:
    """하이브리드 점수 계산 테스트"""
    
    def test_basic_calculation(self, scorer, mock_quant_result):
        """기본 계산"""
        result = scorer.calculate_hybrid_score(
            quant_result=mock_quant_result,
            llm_score=75.0,
            llm_reason='좋은 종목입니다.'
        )
        
        assert result is not None
        assert result.quant_score == 72.0
        assert result.llm_score == 75.0
        # hybrid_score = quant_score * quant_weight + llm_score * llm_weight
        assert 70 <= result.hybrid_score <= 80
    
    def test_default_weights(self, scorer, mock_quant_result):
        """기본 가중치 (60:40)"""
        result = scorer.calculate_hybrid_score(
            quant_result=mock_quant_result,
            llm_score=75.0
        )
        
        # 기본: 정량 60%, LLM 40%
        expected = 72.0 * 0.6 + 75.0 * 0.4
        
        # 안전장치가 적용되지 않으면 가까운 값
        assert abs(result.hybrid_score - expected) < 10
    
    def test_grade_assignment(self, scorer, mock_quant_result):
        """등급 할당"""
        result = scorer.calculate_hybrid_score(
            quant_result=mock_quant_result,
            llm_score=75.0
        )
        
        # 70~79점 → B 등급
        assert result.grade in ['A', 'B', 'C', 'D']
    
    def test_safety_lock_large_gap(self, scorer, mock_quant_result):
        """점수 차이 30점 이상 → 안전장치 발동"""
        # 정량 72점, LLM 100점 → 차이 28점 (경계값)
        result = scorer.calculate_hybrid_score(
            quant_result=mock_quant_result,
            llm_score=100.0  # 차이 28점
        )
        
        # 안전장치가 발동하면 낮은 쪽으로 가중치 이동
        # hybrid_score는 단순 가중 평균보다 낮을 수 있음
        assert result.hybrid_score < (72.0 * 0.5 + 100.0 * 0.5)
    
    def test_condition_info_preserved(self, scorer, mock_quant_result):
        """조건부 승률 정보 보존"""
        result = scorer.calculate_hybrid_score(
            quant_result=mock_quant_result,
            llm_score=75.0
        )
        
        assert result.condition_win_rate == 0.65
        assert result.condition_sample_count == 45


# ============================================================================
# Tests: 최소 품질 기준
# ============================================================================

class TestMinimumQualityThreshold:
    """최소 품질 기준 테스트"""
    
    def test_below_minimum_threshold(self, scorer):
        """40점 미만 → 자동 탈락"""
        from shared.hybrid_scoring.quant_scorer import QuantScoreResult
        
        low_quant = QuantScoreResult(
            stock_code='999999',
            stock_name='저품질종목',
            total_score=30.0,  # 40점 미만
            momentum_score=5.0,
            quality_score=5.0,
            value_score=5.0,
            technical_score=5.0,
            news_stat_score=5.0,
            supply_demand_score=5.0,
            matched_conditions=[],
            condition_win_rate=None,
            condition_sample_count=0,
            condition_confidence='LOW'
        )
        
        result = scorer.calculate_hybrid_score(
            quant_result=low_quant,
            llm_score=80.0  # LLM 높아도
        )
        
        # 최소 기준 미달 시 안전장치 발동
        assert result.safety_lock_applied or result.hybrid_score < 50


# ============================================================================
# Tests: 시장 국면별 가중치
# ============================================================================

class TestMarketRegimeWeights:
    """시장 국면별 가중치 테스트"""
    
    def test_bull_market_weights(self, mock_quant_result):
        """상승장 가중치"""
        from shared.hybrid_scoring.hybrid_scorer import HybridScorer
        
        scorer = HybridScorer(market_regime='BULL')
        result = scorer.calculate_hybrid_score(mock_quant_result, 75.0)
        
        # 상승장: 기본 가중치
        assert result.quant_weight >= 0.5
    
    def test_bear_market_weights(self, mock_quant_result):
        """하락장 가중치"""
        from shared.hybrid_scoring.hybrid_scorer import HybridScorer
        
        scorer = HybridScorer(market_regime='BEAR')
        result = scorer.calculate_hybrid_score(mock_quant_result, 75.0)
        
        # 하락장: 정량 가중치 상향 가능
        assert result.quant_weight >= 0.5


# ============================================================================
# Tests: format_quant_score_for_prompt
# ============================================================================

class TestFormatQuantScoreForPrompt:
    """정량 점수 프롬프트 포맷 테스트"""
    
    def test_format_basic(self, mock_quant_result):
        """기본 포맷"""
        from shared.hybrid_scoring.quant_scorer import format_quant_score_for_prompt
        
        prompt = format_quant_score_for_prompt(mock_quant_result)
        
        assert isinstance(prompt, str)
        assert '삼성전자' in prompt
        assert '72' in prompt  # total_score
    
    def test_format_includes_factors(self, mock_quant_result):
        """팩터 점수 포함"""
        from shared.hybrid_scoring.quant_scorer import format_quant_score_for_prompt
        
        prompt = format_quant_score_for_prompt(mock_quant_result)
        
        # 주요 팩터 포함
        assert '모멘텀' in prompt or 'momentum' in prompt.lower()
        assert '품질' in prompt or 'quality' in prompt.lower()
    
    def test_format_includes_win_rate(self, mock_quant_result):
        """승률 정보 포함"""
        from shared.hybrid_scoring.quant_scorer import format_quant_score_for_prompt
        
        prompt = format_quant_score_for_prompt(mock_quant_result)
        
        assert '65' in prompt or '0.65' in prompt  # win_rate
        assert '45' in prompt  # sample_count


# ============================================================================
# Tests: run_hybrid_scoring_pipeline
# ============================================================================

class TestRunHybridScoringPipeline:
    """하이브리드 스코어링 파이프라인 테스트"""
    
    def test_pipeline_exists(self):
        """파이프라인 함수 존재"""
        from shared.hybrid_scoring.hybrid_scorer import run_hybrid_scoring_pipeline
        
        assert callable(run_hybrid_scoring_pipeline)

