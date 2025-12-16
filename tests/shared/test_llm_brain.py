"""
tests/shared/test_llm_brain.py - JennieBrain 통합 테스트 (3단계)
================================================================

shared/llm.py의 JennieBrain 클래스를 테스트합니다.
Provider들을 mock하여 오케스트레이션 로직을 검증합니다.

실행 방법:
    pytest tests/shared/test_llm_brain.py -v
"""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock
import json


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def mock_gemini_provider():
    """Mock Gemini Provider"""
    provider = MagicMock()
    provider.name = 'gemini'
    provider.flash_model_name.return_value = 'gemini-2.5-flash'
    provider.default_model = 'gemini-2.5-flash'
    return provider


@pytest.fixture
def mock_openai_provider():
    """Mock OpenAI Provider"""
    provider = MagicMock()
    provider.name = 'openai'
    provider.reasoning_model = 'gpt-5-mini'
    provider.default_model = 'gpt-4o-mini'
    return provider


@pytest.fixture
def mock_claude_provider():
    """Mock Claude Provider"""
    provider = MagicMock()
    provider.name = 'claude'
    provider.fast_model = 'claude-haiku-4-5'
    provider.reasoning_model = 'claude-sonnet-4-5'
    return provider


@pytest.fixture
def mock_brain(mock_gemini_provider, mock_openai_provider, mock_claude_provider):
    """모든 Provider가 mock된 JennieBrain"""
    from shared.llm import JennieBrain
    
    # __init__ 우회
    brain = object.__new__(JennieBrain)
    brain.provider = mock_gemini_provider
    brain.provider_gemini = mock_gemini_provider
    brain.provider_openai = mock_openai_provider
    brain.provider_claude = mock_claude_provider
    
    # _get_provider Mocking to support v6.0 Factory calls with Tier routing
    def get_provider_side_effect(tier):
        from shared.llm_factory import LLMTier
        if tier == LLMTier.FAST:
            return mock_gemini_provider
        elif tier == LLMTier.REASONING:
            return mock_claude_provider # or mock_gemini_provider depending on config, but let's use Claude for test distinction
        elif tier == LLMTier.THINKING:
            return mock_claude_provider # Judge/Thinking usually high intelligence
        return mock_gemini_provider

    brain._get_provider = MagicMock(side_effect=get_provider_side_effect)

    return brain


@pytest.fixture
def sample_stock_info():
    """테스트용 종목 정보"""
    return {
        'code': '005930',
        'name': '삼성전자',
        'price': 70000,
        'remaining_budget': 1000000,
        'rag_context': 'AI 반도체 수혜 기대',
        'per': 10.5,
        'pbr': 1.2,
        'market_cap': 400000000,
    }


@pytest.fixture
def sample_portfolio_item():
    """테스트용 포트폴리오 아이템"""
    return {
        'id': 1,
        'code': '005930',
        'name': '삼성전자',
        'avg_price': 65000,
        'high_price': 75000,
        'quantity': 100,
    }


# ============================================================================
# Tests: JennieBrain 초기화
# ============================================================================




# ============================================================================
# Tests: get_jennies_decision
# ============================================================================

class TestGetJenniesDecision:
    """get_jennies_decision 오케스트레이션 테스트"""
    
    def test_buy_mr_decision_approve(self, mock_brain, mock_claude_provider, sample_stock_info):
        """BUY_MR 결재 승인"""
        mock_claude_provider.generate_json.return_value = {
            'decision': 'APPROVE',
            'reason': '볼린저 밴드 하단 터치, 매수 적합',
            'quantity': 10
        }
        
        result = mock_brain.get_jennies_decision(
            'BUY_MR',
            sample_stock_info,
            buy_signal_type='BB_LOWER'
        )
        
        assert result['decision'] == 'APPROVE'
        assert result['quantity'] == 10
        mock_claude_provider.generate_json.assert_called_once()
    
    def test_buy_mr_decision_reject(self, mock_brain, mock_claude_provider, sample_stock_info):
        """BUY_MR 결재 거절"""
        mock_claude_provider.generate_json.return_value = {
            'decision': 'REJECT',
            'reason': '시장 불안정으로 매수 부적합',
            'quantity': 0
        }
        
        result = mock_brain.get_jennies_decision(
            'BUY_MR',
            sample_stock_info,
            buy_signal_type='RSI_OVERSOLD'
        )
        
        assert result['decision'] == 'REJECT'
        assert result['quantity'] == 0
    
    def test_buy_trend_decision(self, mock_brain, mock_claude_provider, sample_stock_info):
        """BUY_TREND 결재"""
        mock_claude_provider.generate_json.return_value = {
            'decision': 'APPROVE',
            'reason': '골든 크로스 확인',
            'quantity': 5
        }
        
        result = mock_brain.get_jennies_decision(
            'BUY_TREND',
            sample_stock_info,
            buy_signal_type='GOLDEN_CROSS'
        )
        
        assert result['decision'] == 'APPROVE'
    
    def test_sell_decision(self, mock_brain, mock_claude_provider, sample_portfolio_item):
        """SELL 결재"""
        mock_claude_provider.generate_json.return_value = {
            'decision': 'SELL',
            'reason': 'RSI 과열로 수익 실현',
            'quantity': 0
        }
        
        result = mock_brain.get_jennies_decision(
            'SELL',
            sample_portfolio_item
        )
        
        assert result['decision'] == 'SELL'
    
    def test_unknown_trade_type(self, mock_brain, sample_stock_info):
        """알 수 없는 거래 타입"""
        result = mock_brain.get_jennies_decision(
            'UNKNOWN_TYPE',
            sample_stock_info
        )
        
        assert result['decision'] == 'REJECT'
        assert '알 수 없는' in result['reason']
    

    



# ============================================================================
# Tests: analyze_news_sentiment
# ============================================================================

class TestAnalyzeNewsSentiment:
    """뉴스 감성 분석 테스트"""
    
    def test_sentiment_positive_news(self, mock_brain):
        """긍정적 뉴스"""
        mock_brain.provider_gemini.generate_json.return_value = {
            'score': 85,
            'reason': '대규모 수주 발표로 강력 호재'
        }
        mock_brain.provider_gemini.flash_model_name.return_value = 'gemini-2.5-flash'
        
        result = mock_brain.analyze_news_sentiment(
            "삼성전자, AI 반도체 10조원 수주 계약",
            "글로벌 빅테크 기업들과 AI 반도체 공급 계약 체결"
        )
        
        assert result['score'] == 85
        assert '호재' in result['reason']
    
    def test_sentiment_negative_news(self, mock_brain):
        """부정적 뉴스"""
        mock_brain.provider_gemini.generate_json.return_value = {
            'score': 20,
            'reason': '실적 쇼크로 강력 악재'
        }
        mock_brain.provider_gemini.flash_model_name.return_value = 'gemini-2.5-flash'
        
        result = mock_brain.analyze_news_sentiment(
            "삼성전자, 3분기 실적 어닝쇼크",
            "영업이익 전년 대비 80% 감소"
        )
        
        assert result['score'] == 20
        assert '악재' in result['reason']
    
    def test_sentiment_neutral_news(self, mock_brain):
        """중립 뉴스"""
        mock_brain.provider_gemini.generate_json.return_value = {
            'score': 50,
            'reason': '일반적인 시황 뉴스'
        }
        mock_brain.provider_gemini.flash_model_name.return_value = 'gemini-2.5-flash'
        
        result = mock_brain.analyze_news_sentiment(
            "반도체 업종 동향",
            "업종 전반 보합세"
        )
        
        assert result['score'] == 50

# ============================================================================
# Tests: JennieBrain 초기화 (v6.0 Factory Pattern)
# ============================================================================

class TestJennieBrainInit:
    """JennieBrain 초기화 테스트 (Factory Pattern)"""
    
    def test_init_basic(self):
        """기본 초기화 확인"""
        from shared.llm import JennieBrain
        brain = JennieBrain()
        assert brain is not None
        # v6.0에서는 __init__에서 provider를 미리 로드하지 않음

# ============================================================================
# Tests: run_debate_session (v6.0)
# ============================================================================

class TestRunDebateSession:
    """Bull vs Bear 토론 테스트 (REASONING Tier)"""
    
    def test_debate_session_success(self, mock_brain, mock_claude_provider, sample_stock_info):
        """토론 세션 성공"""
        sample_stock_info['dominant_keywords'] = ['AI', 'HBM']
        
        # REASONING Tier uses mock_claude_provider in our fixture
        mock_claude_provider.generate_chat.return_value = {
            'text': "Bull: AI 호재! Bear: 거품이야."
        }
        
        result = mock_brain.run_debate_session(sample_stock_info, hunter_score=80)
        
        assert "Bull" in result
        
        # Verify Provider Tier (REASONING)
        from shared.llm_factory import LLMTier
        mock_brain._get_provider.assert_any_call(LLMTier.REASONING)

    def test_debate_session_provider_error(self, mock_brain, sample_stock_info):
        """Provider 로드 실패"""
        mock_brain._get_provider.side_effect = None # Break the side effect
        mock_brain._get_provider.return_value = None
        
        result = mock_brain.run_debate_session(sample_stock_info)
        assert "Skipped" in result

# ============================================================================
# Tests: run_judge_scoring (v6.0)
# ============================================================================

class TestRunJudgeScoring:
    """Judge 최종 판결 테스트 (THINKING Tier)"""
    
    def test_judge_scoring_success(self, mock_brain, mock_claude_provider, sample_stock_info):
        """판결 성공"""
        debate_log = "Bull vs Bear Debate Log"
        sample_stock_info['dominant_keywords'] = ['AI']
        
        # THINKING Tier uses mock_claude_provider
        mock_claude_provider.generate_json.return_value = {
            'score': 85,
            'grade': 'B',
            'reason': '토론 결과 긍정적'
        }
        
        result = mock_brain.run_judge_scoring(sample_stock_info, debate_log)
        
        assert result['score'] == 85
        assert result['grade'] == 'B'
        
        from shared.llm_factory import LLMTier
        mock_brain._get_provider.assert_any_call(LLMTier.THINKING)

    def test_judge_scoring_v5_fallback(self, mock_brain, mock_claude_provider, sample_stock_info):
        """정량 컨텍스트 포함 시 v5 로직 (REASONING)"""
        debate_log = "Debate"
        quant_context = "Quant Score: 80"
        sample_stock_info['hunter_score'] = 80 # Pass Gatekeeper
        
        # Judge V5 uses THINKING Tier (Claude)
        mock_claude_provider.generate_json.return_value = {
            'score': 82,
            'grade': 'A',
            'reason': 'V5 Logic'
        }
        
        result = mock_brain.run_judge_scoring_v5(sample_stock_info, debate_log, quant_context)
        
        assert result['score'] == 82
        
        from shared.llm_factory import LLMTier
        mock_brain._get_provider.assert_any_call(LLMTier.THINKING)


# ============================================================================
# Tests: v5 Hybrid Scoring
# ============================================================================

class TestV5HybridScoring:
    """v5 하이브리드 스코어링 테스트 (FAST/REASONING)"""
    
    def test_analysis_score_v5_fast(self, mock_brain, mock_claude_provider, sample_stock_info):
        """REASONING Tier 사용 확인 (v5 Hunter)"""
        # v5 Hunter uses REASONING Tier (Claude in mock)
        mock_claude_provider.generate_json.return_value = {
            'score': 75,
            'grade': 'B',
            'reason': 'Fast Analysis'
        }
        
        result = mock_brain.get_jennies_analysis_score_v5(sample_stock_info, quant_context=None)
        
        assert result['score'] == 75
        
        from shared.llm_factory import LLMTier
        mock_brain._get_provider.assert_any_call(LLMTier.REASONING)

# ============================================================================
# Tests: generate_daily_briefing
# ============================================================================

class TestGenerateDailyBriefing:
    """데일리 브리핑 생성 테스트 (REASONING Tier)"""
    
    def test_daily_briefing_success(self, mock_brain, mock_claude_provider):
        """브리핑 생성 성공"""
        market_data = "KOSPI 2500"
        execution_log = "Bought Samsung"
        
        # Briefing uses THINKING Tier (Claude)
        mock_claude_provider.generate_chat.return_value = {
            'text': "Overall Market: Bullish..."
        }
        
        # Mock default model for tokens
        mock_claude_provider.default_model = 'claude-3-opus'
        
        result = mock_brain.generate_daily_briefing(market_data, execution_log)
        
        assert "Bullish" in result
        
        from shared.llm_factory import LLMTier
        mock_brain._get_provider.assert_any_call(LLMTier.THINKING)
    







    



