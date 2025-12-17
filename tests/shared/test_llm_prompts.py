"""
tests/shared/test_llm_prompts.py - LLM 프롬프트 빌더 함수 Unit Tests (1단계)
==========================================================================

shared/llm_prompts.py의 프롬프트 빌더 함수들을 테스트합니다.
이 함수들은 순수 함수이므로 외부 의존성 없이 테스트 가능합니다.

실행 방법:
    pytest tests/shared/test_llm_prompts.py -v

커버리지 포함:
    pytest tests/shared/test_llm_prompts.py -v --cov=shared.llm_prompts --cov-report=term-missing
"""

import pytest
import json

# v5.1 - llm_prompts에서 직접 함수 import
from shared.llm_prompts import (
    build_buy_prompt_mean_reversion,
    build_buy_prompt_golden_cross,
    build_buy_prompt_ranking,
    build_sell_prompt,
    build_add_watchlist_prompt,
    build_analysis_prompt,
    build_parameter_verification_prompt,
    build_hunter_prompt_v5,
)


# ============================================================================
# Fixtures
# ============================================================================



@pytest.fixture
def sample_stock_snapshot():
    """BUY 프롬프트용 샘플 종목 스냅샷"""
    return {
        'code': '005930',
        'name': '삼성전자',
        'price': 70000,
        'remaining_budget': 1000000,
        'rag_context': '삼성전자가 AI 반도체 시장에서 주도권을 확보할 것으로 전망됩니다.',
        'per': 10.5,
        'pbr': 1.2,
        'market_cap': 400000000,  # 시가총액 (백만 원 단위)
    }


@pytest.fixture
def sample_portfolio_item():
    """SELL 프롬프트용 샘플 포트폴리오 아이템"""
    return {
        'code': '005930',
        'name': '삼성전자',
        'avg_price': 65000,
        'high_price': 75000,
        'quantity': 100,
    }


@pytest.fixture
def sample_watchlist_candidate():
    """ADD_WATCHLIST 프롬프트용 샘플 후보"""
    return {
        'code': '000660',
        'name': 'SK하이닉스',
        'technical_reason': '20일 이평선 골든크로스 발생',
        'news_reason': 'HBM3E 양산 본격화로 실적 개선 기대',
        'per': 8.3,
        'pbr': 1.5,
        'market_cap': 100000000,
    }


@pytest.fixture
def sample_analysis_info():
    """분석용 종목 정보"""
    return {
        'code': '035420',
        'name': 'NAVER',
        'per': 25.0,
        'pbr': 2.0,
        'market_cap': 50000000,
        'news_reason': '네이버 클라우드 AWS 협력으로 글로벌 진출 가속화',
        'momentum_score': 5.2,
    }


@pytest.fixture
def sample_ranking_candidates():
    """랭킹 결재용 후보 리스트"""
    return [
        {
            'stock_code': '005930',
            'stock_name': '삼성전자',
            'stock_info': {'per': 10.5, 'pbr': 1.2, 'calculated_quantity': 10},
            'current_price': 70000,
            'buy_signal_type': 'GOLDEN_CROSS',
            'factor_score': 850.5,
            'factors': {
                'momentum_score': 75,
                'quality_score': 80,
                'value_score': 85,
                'technical_score': 70,
            },
            'rag_context': '삼성전자 AI 반도체 수혜 기대',
        },
        {
            'stock_code': '000660',
            'stock_name': 'SK하이닉스',
            'stock_info': {'per': 8.3, 'pbr': 1.5, 'calculated_quantity': 5},
            'current_price': 150000,
            'buy_signal_type': 'RSI_OVERSOLD',
            'factor_score': 820.3,
            'factors': {
                'momentum_score': 70,
                'quality_score': 85,
                'value_score': 80,
                'technical_score': 75,
            },
            'rag_context': 'HBM 수요 증가로 실적 호조 전망',
        },
    ]


# ============================================================================
# Tests: 순수 함수 (_parse_llm_reason은 repository.py에서 테스트)
# ============================================================================

class TestBuyPromptMeanReversion:
    """평균 회귀 매수 프롬프트 테스트"""
    
    def test_build_buy_prompt_bb_lower(self, sample_stock_snapshot):
        """볼린저 밴드 하단 신호"""
        prompt = build_buy_prompt_mean_reversion(
            sample_stock_snapshot, 
            buy_signal_type='BB_LOWER'
        )
        
        # 필수 요소 확인
        assert '삼성전자' in prompt
        assert '005930' in prompt
        assert '70,000' in prompt  # 현재가
        assert '볼린저 밴드' in prompt
        assert 'APPROVE' in prompt or 'REJECT' in prompt
        assert '남은 예산' in prompt
    
    def test_build_buy_prompt_rsi_oversold(self, sample_stock_snapshot):
        """RSI 과매도 신호"""
        prompt = build_buy_prompt_mean_reversion(
            sample_stock_snapshot, 
            buy_signal_type='RSI_OVERSOLD'
        )
        
        assert 'RSI' in prompt
        assert '30 이하' in prompt
    
    def test_build_buy_prompt_unknown_signal(self, sample_stock_snapshot):
        """알 수 없는 신호 타입"""
        prompt = build_buy_prompt_mean_reversion(
            sample_stock_snapshot, 
            buy_signal_type='UNKNOWN_TYPE'
        )
        
        # 알 수 없는 신호도 처리 가능해야 함
        assert '검토 필요' in prompt or 'UNKNOWN_TYPE' in prompt
    
    def test_build_buy_prompt_includes_rag_context(self, sample_stock_snapshot):
        """RAG 컨텍스트 포함 확인"""
        prompt = build_buy_prompt_mean_reversion(
            sample_stock_snapshot, 
            buy_signal_type='BB_LOWER'
        )
        
        assert 'AI 반도체' in prompt  # RAG 컨텍스트 내용


class TestBuyPromptGoldenCross:
    """추세 돌파 매수 프롬프트 테스트"""
    
    def test_build_buy_prompt_golden_cross(self, sample_stock_snapshot):
        """골든 크로스 신호"""
        prompt = build_buy_prompt_golden_cross(
            sample_stock_snapshot, 
            buy_signal_type='GOLDEN_CROSS'
        )
        
        assert '삼성전자' in prompt
        assert '골든 크로스' in prompt
        assert '추세' in prompt
    
    def test_build_buy_prompt_momentum(self, sample_stock_snapshot):
        """모멘텀 신호"""
        prompt = build_buy_prompt_golden_cross(
            sample_stock_snapshot, 
            buy_signal_type='MOMENTUM'
        )
        
        assert '모멘텀' in prompt
        assert '상승세' in prompt
    
    def test_build_buy_prompt_relative_strength(self, sample_stock_snapshot):
        """상대 강도 신호"""
        prompt = build_buy_prompt_golden_cross(
            sample_stock_snapshot, 
            buy_signal_type='RELATIVE_STRENGTH'
        )
        
        assert '상대 강도' in prompt
        assert 'KOSPI' in prompt
    
    def test_build_buy_prompt_resistance_breakout(self, sample_stock_snapshot):
        """저항선 돌파 신호"""
        prompt = build_buy_prompt_golden_cross(
            sample_stock_snapshot, 
            buy_signal_type='RESISTANCE_BREAKOUT'
        )
        
        assert '저항선 돌파' in prompt or '고점' in prompt


class TestSellPrompt:
    """매도 프롬프트 테스트"""
    
    def test_build_sell_prompt(self, sample_portfolio_item):
        """매도 프롬프트 생성"""
        prompt = build_sell_prompt(sample_portfolio_item)
        
        assert '삼성전자' in prompt
        assert '005930' in prompt
        assert 'SELL' in prompt
        assert 'HOLD' in prompt
        assert '매수가' in prompt or '65,000' in prompt
        assert 'RSI' in prompt


class TestAddWatchlistPrompt:
    """관심 종목 편입 프롬프트 테스트"""
    
    def test_build_add_watchlist_prompt(self, sample_watchlist_candidate):
        """관심 종목 편입 프롬프트 생성"""
        prompt = build_add_watchlist_prompt(sample_watchlist_candidate)
        
        assert 'SK하이닉스' in prompt
        assert '000660' in prompt
        assert 'APPROVE' in prompt
        assert 'REJECT' in prompt
        assert 'HBM' in prompt or '골든크로스' in prompt


class TestBuyPromptRanking:
    """Top-N 랭킹 결재 프롬프트 테스트"""
    
    def test_build_buy_prompt_ranking(self, sample_ranking_candidates):
        """랭킹 프롬프트 생성"""
        prompt = build_buy_prompt_ranking(sample_ranking_candidates)
        
        # 후보 정보 포함
        assert '삼성전자' in prompt
        assert 'SK하이닉스' in prompt
        assert '005930' in prompt
        assert '000660' in prompt
        
        # 팩터 점수 포함
        assert '850' in prompt  # factor_score
        assert '모멘텀' in prompt
        assert '품질' in prompt
        
        # 결재 지침 포함
        assert 'REJECT_ALL' in prompt
        assert 'best_stock_code' in prompt
    
    def test_build_buy_prompt_ranking_single_candidate(self):
        """단일 후보 랭킹"""
        single_candidate = [{
            'stock_code': '005930',
            'stock_name': '삼성전자',
            'stock_info': {'per': 10.5, 'pbr': 1.2, 'calculated_quantity': 10},
            'current_price': 70000,
            'buy_signal_type': 'GOLDEN_CROSS',
            'factor_score': 850.5,
            'factors': {
                'momentum_score': 75,
                'quality_score': 80,
                'value_score': 85,
                'technical_score': 70,
            },
            'rag_context': None,  # RAG 없음
        }]
        
        prompt = build_buy_prompt_ranking(single_candidate)
        
        assert '삼성전자' in prompt
        assert 'Top 1' in prompt or '후보 1' in prompt
        assert '최신 뉴스 없음' in prompt  # RAG 없을 때


class TestAnalysisPrompt:
    """종목 분석 프롬프트 테스트"""
    
    def test_build_analysis_prompt(self, sample_analysis_info):
        """분석 프롬프트 생성"""
        prompt = build_analysis_prompt(sample_analysis_info)
        
        assert 'NAVER' in prompt
        assert '035420' in prompt
        assert 'PER' in prompt
        assert '점수' in prompt or 'score' in prompt
        assert '등급' in prompt or 'grade' in prompt
    
    def test_build_analysis_prompt_no_news(self):
        """뉴스 없는 경우"""
        info = {
            'code': '005930',
            'name': '삼성전자',
            'per': 10.0,
            'pbr': 1.0,
            'market_cap': 400000000,
        }
        
        prompt = build_analysis_prompt(info)
        
        assert '특별한 뉴스 없음' in prompt


class TestHunterPromptV5:
    """v5 Hunter 프롬프트 테스트 (정량 컨텍스트 포함)"""
    
    def test_build_hunter_prompt_v5_with_quant_context(self):
        """정량 컨텍스트 포함 프롬프트"""
        stock_info = {
            'name': '삼성전자',
            'code': '005930',
            'news_reason': 'AI 반도체 수혜 기대',
        }
        
        quant_context = """
        ## 정량 분석 결과
        - 정량 점수: 72점
        - 조건부 승률: 65%
        - 표본 수: 45개
        """
        
        prompt = build_hunter_prompt_v5(stock_info, quant_context)
        
        # 정량 컨텍스트 포함 확인
        assert '정량 분석' in prompt
        assert '72점' in prompt or '승률' in prompt
        assert '표본' in prompt
        
        # 뉴스 포함 확인
        assert 'AI 반도체' in prompt
    
    def test_build_hunter_prompt_v5_without_quant_context(self, sample_analysis_info):
        """정량 컨텍스트 없으면 기존 방식으로 폴백"""
        prompt = build_hunter_prompt_v5(sample_analysis_info, quant_context=None)
        
        # 기존 _build_analysis_prompt와 동일한 출력
        assert 'NAVER' in prompt
        assert 'PER' in prompt


class TestParameterVerificationPrompt:
    """파라미터 변경 검증 프롬프트 테스트"""
    
    def test_build_parameter_verification_prompt(self):
        """파라미터 검증 프롬프트 생성"""
        current_params = {'take_profit': 0.08, 'stop_loss': -0.05}
        new_params = {'take_profit': 0.09, 'stop_loss': -0.045}
        current_perf = {'mdd': -15.0, 'return': 12.0}
        new_perf = {'mdd': -12.0, 'return': 14.0}
        market_summary = "최근 시장은 변동성이 높은 상태"
        
        prompt = build_parameter_verification_prompt(
            current_params, new_params,
            current_perf, new_perf,
            market_summary
        )
        
        # 파라미터 변경 정보 포함
        assert 'take_profit' in prompt
        assert 'stop_loss' in prompt
        
        # 성과 비교 포함
        assert 'MDD' in prompt
        assert '수익률' in prompt
        
        # 판단 지침 포함
        assert 'overfitting' in prompt.lower() or '과최적화' in prompt
        assert '10%' in prompt  # 변경폭 제한




class TestFormatHelpers:
    """포맷 헬퍼 함수 테스트 (프롬프트 내부)"""
    
    def test_format_market_cap_trillion(self, sample_stock_snapshot):
        """시가총액 조 단위 포맷"""
        # market_cap을 조 단위로 설정
        sample_stock_snapshot['market_cap'] = 400000000  # 400조 (백만원 * 1,000,000)
        
        prompt = build_buy_prompt_mean_reversion(
            sample_stock_snapshot, 
            buy_signal_type='BB_LOWER'
        )
        
        # '조' 또는 '억' 단위 포함 확인
        assert '조' in prompt or '억' in prompt
    
    def test_format_per_negative(self, sample_stock_snapshot):
        """PER 음수(적자 기업) 포맷"""
        sample_stock_snapshot['per'] = -5.0
        
        prompt = build_buy_prompt_mean_reversion(
            sample_stock_snapshot, 
            buy_signal_type='BB_LOWER'
        )
        
        assert 'N/A' in prompt or '적자' in prompt


class TestEdgeCases:
    """Edge Cases 테스트"""
    
    def test_empty_stock_info(self):
        """빈 종목 정보"""
        empty_info = {}
        
        # 에러 없이 프롬프트 생성 가능해야 함
        prompt = build_buy_prompt_mean_reversion(
            empty_info, 
            buy_signal_type='BB_LOWER'
        )
        
        assert prompt is not None
        assert 'N/A' in prompt
    
    def test_very_long_rag_context(self, sample_ranking_candidates):
        """매우 긴 RAG 컨텍스트 (500자 초과)"""
        sample_ranking_candidates[0]['rag_context'] = 'A' * 1000  # 1000자
        
        prompt = build_buy_prompt_ranking(sample_ranking_candidates)
        
        # 축약되어야 함
        assert '생략' in prompt or len(prompt) < 50000
    
    def test_special_characters_in_name(self):
        """종목명에 특수문자"""
        info = {
            'code': '123456',
            'name': 'LG화학 (우)',
            'price': 50000,
            'remaining_budget': 500000,
            'rag_context': 'Test',
            'per': 10.0,
            'market_cap': 100000000,
        }
        
        prompt = build_buy_prompt_mean_reversion(info, 'BB_LOWER')
        
        assert 'LG화학' in prompt

