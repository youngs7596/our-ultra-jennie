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

class TestJennieBrainInit:
    """JennieBrain 초기화 테스트"""
    
    @patch('shared.llm_providers.build_llm_provider')
    @patch('shared.llm_providers.ClaudeLLMProvider')
    def test_init_all_providers_success(self, mock_claude_class, mock_build_provider):
        """모든 Provider 초기화 성공"""
        from shared.llm import JennieBrain
        
        mock_gemini = MagicMock()
        mock_gemini.name = 'gemini'
        mock_build_provider.return_value = mock_gemini
        
        mock_claude = MagicMock()
        mock_claude.name = 'claude'
        mock_claude_class.return_value = mock_claude
        
        brain = JennieBrain('project', 'gemini-secret')
        
        assert brain.provider_gemini is not None
        assert brain.provider == brain.provider_gemini  # 기본 Provider
    
    @patch('shared.llm.ClaudeLLMProvider', side_effect=Exception("Claude Error"))
    @patch('shared.llm.OpenAILLMProvider', side_effect=Exception("OpenAI Error"))  
    @patch('shared.llm.build_llm_provider', side_effect=Exception("API Error"))
    def test_init_gemini_fail_graceful(self, mock_build_provider, mock_openai, mock_claude):
        """Gemini 실패 시 graceful handling"""
        from shared.llm import JennieBrain
        
        brain = JennieBrain('project', 'gemini-secret')
        
        assert brain.provider is None
        assert brain.provider_gemini is None


# ============================================================================
# Tests: get_jennies_decision
# ============================================================================

class TestGetJenniesDecision:
    """get_jennies_decision 오케스트레이션 테스트"""
    
    def test_buy_mr_decision_approve(self, mock_brain, sample_stock_info):
        """BUY_MR 결재 승인"""
        mock_brain.provider.generate_json.return_value = {
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
        mock_brain.provider.generate_json.assert_called_once()
    
    def test_buy_mr_decision_reject(self, mock_brain, sample_stock_info):
        """BUY_MR 결재 거절"""
        mock_brain.provider.generate_json.return_value = {
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
    
    def test_buy_trend_decision(self, mock_brain, sample_stock_info):
        """BUY_TREND 결재"""
        mock_brain.provider.generate_json.return_value = {
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
    
    def test_sell_decision(self, mock_brain, sample_portfolio_item):
        """SELL 결재"""
        mock_brain.provider.generate_json.return_value = {
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
    
    def test_provider_none_error(self, sample_stock_info):
        """Provider가 None인 경우"""
        from shared.llm import JennieBrain
        
        brain = object.__new__(JennieBrain)
        brain.provider = None
        
        result = brain.get_jennies_decision('BUY_MR', sample_stock_info)
        
        assert result['decision'] == 'REJECT'
        assert '초기화 실패' in result['reason']
    
    def test_llm_error_handling(self, mock_brain, sample_stock_info):
        """LLM 호출 에러 핸들링"""
        mock_brain.provider.generate_json.side_effect = Exception("API Timeout")
        
        result = mock_brain.get_jennies_decision(
            'BUY_MR',
            sample_stock_info,
            buy_signal_type='BB_LOWER'
        )
        
        assert result['decision'] == 'REJECT'
        assert 'LLM 결재 오류' in result['reason']


# ============================================================================
# Tests: get_jennies_ranking_decision
# ============================================================================

class TestGetJenniesRankingDecision:
    """Top-N 랭킹 결재 테스트"""
    
    def test_ranking_decision_select_best(self, mock_brain):
        """최고 종목 선정"""
        candidates = [
            {'stock_code': '005930', 'stock_name': '삼성전자', 'factor_score': 850,
             'stock_info': {'per': 10, 'pbr': 1, 'calculated_quantity': 10},
             'current_price': 70000, 'buy_signal_type': 'GOLDEN_CROSS',
             'factors': {'momentum_score': 80, 'quality_score': 85, 'value_score': 80, 'technical_score': 75},
             'rag_context': 'Good news'},
            {'stock_code': '000660', 'stock_name': 'SK하이닉스', 'factor_score': 800,
             'stock_info': {'per': 8, 'pbr': 1.5, 'calculated_quantity': 5},
             'current_price': 150000, 'buy_signal_type': 'RSI_OVERSOLD',
             'factors': {'momentum_score': 75, 'quality_score': 80, 'value_score': 85, 'technical_score': 70},
             'rag_context': None},
        ]
        
        mock_brain.provider.generate_json.return_value = {
            'best_stock_code': '005930',
            'reason': '삼성전자가 팩터 점수와 뉴스 모두 우수',
            'quantity': 10
        }
        
        result = mock_brain.get_jennies_ranking_decision(candidates)
        
        assert result['best_stock_code'] == '005930'
        assert result['quantity'] == 10
    
    def test_ranking_decision_reject_all(self, mock_brain):
        """모든 후보 거절"""
        candidates = [
            {'stock_code': '005930', 'stock_name': '삼성전자', 'factor_score': 500,
             'stock_info': {'per': 30, 'pbr': 3, 'calculated_quantity': 1},
             'current_price': 70000, 'buy_signal_type': 'WEAK',
             'factors': {'momentum_score': 40, 'quality_score': 45, 'value_score': 50, 'technical_score': 35},
             'rag_context': 'Bad news'},
        ]
        
        mock_brain.provider.generate_json.return_value = {
            'best_stock_code': 'REJECT_ALL',
            'reason': '모든 후보가 악재 보유',
            'quantity': 0
        }
        
        result = mock_brain.get_jennies_ranking_decision(candidates)
        
        assert result['best_stock_code'] == 'REJECT_ALL'
        assert result['quantity'] == 0


# ============================================================================
# Tests: get_jennies_analysis_score
# ============================================================================

class TestGetJenniesAnalysisScore:
    """종목 분석 점수 테스트"""
    
    def test_analysis_score_claude_success(self, mock_brain, sample_stock_info):
        """Claude Provider로 분석 성공"""
        mock_brain.provider_claude.generate_json.return_value = {
            'score': 75,
            'grade': 'B',
            'reason': '펀더멘털 양호, 뉴스 긍정적'
        }
        
        result = mock_brain.get_jennies_analysis_score(sample_stock_info)
        
        assert result['score'] == 75
        assert result['grade'] == 'B'
        mock_brain.provider_claude.generate_json.assert_called_once()
    
    def test_analysis_score_fallback_to_openai(self, mock_brain, sample_stock_info):
        """Claude 실패 시 OpenAI로 폴백"""
        mock_brain.provider_claude.generate_json.side_effect = Exception("Claude Error")
        mock_brain.provider_openai.generate_json.return_value = {
            'score': 70,
            'grade': 'B',
            'reason': 'OpenAI 분석'
        }
        
        result = mock_brain.get_jennies_analysis_score(sample_stock_info)
        
        assert result['score'] == 70
        mock_brain.provider_openai.generate_json.assert_called_once()
    
    def test_analysis_score_all_fail(self, mock_brain, sample_stock_info):
        """모든 Provider 실패"""
        mock_brain.provider_claude.generate_json.side_effect = Exception("Claude Error")
        mock_brain.provider_openai.generate_json.side_effect = Exception("OpenAI Error")
        mock_brain.provider_gemini.generate_json.side_effect = Exception("Gemini Error")
        
        result = mock_brain.get_jennies_analysis_score(sample_stock_info)
        
        assert result['score'] == 0
        assert result['grade'] == 'D'
        assert '오류' in result['reason'] or '실패' in result['reason']
    
    def test_analysis_score_no_providers(self):
        """Provider가 모두 None인 경우"""
        from shared.llm import JennieBrain
        
        brain = object.__new__(JennieBrain)
        brain.provider_claude = None
        brain.provider_openai = None
        brain.provider_gemini = None
        
        result = brain.get_jennies_analysis_score({'name': 'Test'})
        
        assert result['score'] == 0
        assert result['grade'] == 'D'


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
    
    def test_sentiment_provider_none(self):
        """Provider None인 경우"""
        from shared.llm import JennieBrain
        
        brain = object.__new__(JennieBrain)
        brain.provider_gemini = None
        
        result = brain.analyze_news_sentiment("Test", "Test")
        
        assert result['score'] == 50  # 기본값
        assert '미초기화' in result['reason'] or '기본값' in result['reason']


# ============================================================================
# Tests: run_debate_session
# ============================================================================

class TestRunDebateSession:
    """Bull vs Bear 토론 테스트"""
    
    def test_debate_session_openai(self, mock_brain, sample_stock_info):
        """Claude로 토론 (OpenAI는 폴백)"""
        sample_stock_info['news_reason'] = 'AI 반도체 호재'
        
        # [v4.2] Claude가 1순위
        mock_brain.provider_claude.generate_json.return_value = {
            'debate_log': 'Bull: 이 종목 PER 8배야!\nBear: 밸류트랩일 수 있어.\nBull: 수주 3조야!'
        }
        
        result = mock_brain.run_debate_session(sample_stock_info)
        
        assert 'Bull' in result
        assert 'Bear' in result
    
    def test_debate_session_gemini_fallback(self, mock_brain, sample_stock_info):
        """Claude 없으면 Gemini로 폴백"""
        mock_brain.provider_claude = None
        sample_stock_info['news_reason'] = 'Test news'
        
        mock_brain.provider_gemini.generate_json.return_value = {
            'debate_log': 'Gemini Debate Log'
        }
        
        result = mock_brain.run_debate_session(sample_stock_info)
        
        assert result == 'Gemini Debate Log'
    
    def test_debate_session_no_provider(self):
        """Provider 모두 None"""
        from shared.llm import JennieBrain
        
        brain = object.__new__(JennieBrain)
        brain.provider_openai = None
        brain.provider_gemini = None
        
        result = brain.run_debate_session({'name': 'Test'})
        
        assert 'Skipped' in result or 'Error' in result


# ============================================================================
# Tests: run_judge_scoring
# ============================================================================

class TestRunJudgeScoring:
    """Judge 최종 판결 테스트"""
    
    def test_judge_scoring_openai(self, mock_brain, sample_stock_info):
        """Claude로 판결 (OpenAI는 폴백)"""
        sample_stock_info['news_reason'] = 'Good news'
        debate_log = "Bull: Good!\nBear: Bad!\nBull: Very good!"
        
        # [v4.2] Claude가 1순위
        mock_brain.provider_claude.generate_json.return_value = {
            'score': 75,
            'grade': 'B',
            'reason': 'Bull이 우세, 매수 추천'
        }
        
        result = mock_brain.run_judge_scoring(sample_stock_info, debate_log)
        
        assert result['score'] == 75
        assert result['grade'] == 'B'
    
    def test_judge_scoring_gemini_fallback(self, mock_brain, sample_stock_info):
        """Claude 없으면 Gemini로 폴백"""
        mock_brain.provider_claude = None
        sample_stock_info['news_reason'] = 'Test'
        
        mock_brain.provider_gemini.generate_json.return_value = {
            'score': 65,
            'grade': 'C',
            'reason': 'Gemini Judge'
        }
        
        result = mock_brain.run_judge_scoring(sample_stock_info, "Debate log")
        
        assert result['score'] == 65


# ============================================================================
# Tests: v5 Hybrid Scoring
# ============================================================================

class TestV5HybridScoring:
    """v5 하이브리드 스코어링 테스트"""
    
    def test_analysis_score_v5_with_quant(self, mock_brain, sample_stock_info):
        """정량 컨텍스트 포함 분석 - Gemini 우선"""
        quant_context = "정량 점수: 72점\n조건부 승률: 65%"
        
        # [v4.2] Gemini가 1순위
        mock_brain.provider_gemini.generate_json.return_value = {
            'score': 78,
            'grade': 'B',
            'reason': '정량 72점 + 뉴스 긍정'
        }
        
        result = mock_brain.get_jennies_analysis_score_v5(sample_stock_info, quant_context)
        
        assert result['score'] == 78
    
    def test_analysis_score_v5_without_quant(self, mock_brain, sample_stock_info):
        """정량 컨텍스트 없으면 기존 방식 - Gemini 우선"""
        mock_brain.provider_gemini.generate_json.return_value = {
            'score': 70,
            'grade': 'B',
            'reason': '기존 분석'
        }
        
        result = mock_brain.get_jennies_analysis_score_v5(sample_stock_info, quant_context=None)
        
        assert result['score'] == 70
    
    def test_judge_scoring_v5_with_quant(self, mock_brain, sample_stock_info):
        """v5 Judge with 정량 컨텍스트 - Claude 우선"""
        sample_stock_info['news_reason'] = 'News'
        quant_context = "정량 점수: 80점"
        debate_log = "Bull vs Bear"
        
        # [v4.2] Claude가 1순위
        mock_brain.provider_claude.generate_json.return_value = {
            'score': 82,
            'grade': 'A',
            'reason': '정량 + 토론 종합'
        }
        
        result = mock_brain.run_judge_scoring_v5(sample_stock_info, debate_log, quant_context)
        
        assert result['score'] == 82
        assert result['grade'] == 'A'


# ============================================================================
# Tests: verify_parameter_change
# ============================================================================

class TestVerifyParameterChange:
    """파라미터 변경 검증 테스트"""
    
    def test_verify_approved(self, mock_brain):
        """변경 승인"""
        current = {'take_profit': 0.08, 'stop_loss': -0.05}
        new = {'take_profit': 0.09, 'stop_loss': -0.045}
        current_perf = {'mdd': -15.0, 'return': 12.0}
        new_perf = {'mdd': -12.0, 'return': 14.0}
        
        mock_brain.provider.generate_json.return_value = {
            'is_approved': True,
            'reasoning': '성과 개선이 타당함',
            'confidence_score': 0.85
        }
        
        result = mock_brain.verify_parameter_change(
            current, new, current_perf, new_perf, "시장 변동성 높음"
        )
        
        assert result['is_approved'] is True
        assert result['confidence_score'] >= 0.8
    
    def test_verify_rejected(self, mock_brain):
        """변경 거절"""
        current = {'take_profit': 0.08}
        new = {'take_profit': 0.20}  # 너무 큰 변경
        current_perf = {'mdd': -10.0, 'return': 10.0}
        new_perf = {'mdd': -5.0, 'return': 30.0}  # 과최적화 의심
        
        mock_brain.provider.generate_json.return_value = {
            'is_approved': False,
            'reasoning': '변경폭 10% 초과, 과최적화 위험',
            'confidence_score': 0.9
        }
        
        result = mock_brain.verify_parameter_change(
            current, new, current_perf, new_perf, ""
        )
        
        assert result['is_approved'] is False
    
    def test_verify_provider_none(self):
        """Provider None인 경우"""
        from shared.llm import JennieBrain
        
        brain = object.__new__(JennieBrain)
        brain.provider = None
        
        result = brain.verify_parameter_change({}, {}, {}, {}, "")
        
        assert result['is_approved'] is False
        assert result['confidence_score'] == 0.0


# ============================================================================
# Tests: analyze_with_context (v1.0)
# ============================================================================

class TestAnalyzeWithContext:
    """정량 컨텍스트 기반 분석 테스트"""
    
    def test_analyze_with_context_success(self, mock_brain):
        """정량 컨텍스트 분석 성공"""
        quant_context = "정량 점수: 75점\n조건부 승률: 68%"
        
        mock_brain.provider_claude.generate_json.return_value = {
            'score': 77,
            'grade': 'B',
            'reason': '정량 분석 우수'
        }
        
        result = mock_brain.analyze_with_context(
            stock_code='005930',
            stock_name='삼성전자',
            quant_context=quant_context,
            news_summary='AI 반도체 호재',
            fundamentals={'per': 10.5, 'pbr': 1.2}
        )
        
        assert result['score'] == 77
        assert result['grade'] == 'B'
    
    def test_analyze_with_context_no_news(self, mock_brain):
        """뉴스 없는 경우"""
        mock_brain.provider_claude.generate_json.return_value = {
            'score': 65,
            'grade': 'C',
            'reason': '뉴스 없음, 정량만 참고'
        }
        
        result = mock_brain.analyze_with_context(
            stock_code='005930',
            stock_name='삼성전자',
            quant_context="정량 점수: 60점",
            news_summary="",  # 빈 뉴스
            fundamentals=None
        )
        
        assert result['score'] == 65

