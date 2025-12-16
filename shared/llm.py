
# Version: v6.0 (Enhanced with LLM Factory) - Ultra Jennie LLM 오케스트레이션 모듈
# Jennie Brain Module - LLM Orchestrator
#
# Roles:
# 1. Sentiment: News Sentiment Analysis (FAST Tier)
# 2. Hunter: Stock Analysis (REASONING Tier)
# 3. Judge: Final Decision (THINKING Tier)

import os
import logging
import json
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Union

# [v6.0] Factory & Enum
from shared.llm_factory import LLMFactory, LLMTier
import shared.database as database
import shared.auth as auth

# [v6.0] Corrected Imports from shared modules
from shared.llm_prompts import (
    build_buy_prompt_mean_reversion,
    build_buy_prompt_golden_cross, # Used as build_buy_prompt_trend_following
    build_sell_prompt, # Used as build_sell_decision_prompt
    build_news_sentiment_prompt,
    build_debate_prompt,
    build_judge_prompt, # V4 Judge
    build_hunter_prompt_v5,
    build_judge_prompt_v5
)

from shared.llm_constants import ANALYSIS_RESPONSE_SCHEMA
# Alias for compatibility if needed, or just use ANALYSIS_RESPONSE_SCHEMA
JUDGE_RESPONSE_SCHEMA = ANALYSIS_RESPONSE_SCHEMA

# "youngs75_jennie.llm" 이름으로 로거 생성
logger = logging.getLogger(__name__)

class JennieBrain:
    """
    LLM을 사용하여 'BUY' 또는 'SELL' 신호에 대한 최종 결재를 수행합니다.
    [v6.0] LLM Factory Pattern 도입 - Hybrid Strategy (Local/Cloud)
    """
    
    def __init__(self, project_id=None, gemini_api_key_secret=None):
        # [v6.0] Factory를 통해 Provider는 필요할 때 동적으로 가져옵니다.
        # 기존 __init__에서의 복잡한 초기화는 제거하고, Factory에 위임합니다.
        logger.info("--- [JennieBrain] v6.0 Initialized (Factory Pattern) ---")
        
        # 레거시 호환성을 위해 필드만 남겨둠 (실제로는 사용하지 않음)
        self.provider_gemini = None 
        self.provider_claude = None
        self.provider_openai = None

    def _get_provider(self, tier: LLMTier):
        """Helper to get provider from Factory with error handling"""
        try:
            return LLMFactory.get_provider(tier)
        except Exception as e:
            logger.error(f"❌ [JennieBrain] Provider 로드 실패 ({tier}): {e}")
            return None

    # -----------------------------------------------------------------
    # '제니' 결재 실행
    # -----------------------------------------------------------------
    def get_jennies_decision(self, trade_type, stock_info, **kwargs):
        """
        LLM을 호출하여 최종 결재를 받습니다.
        Trade Decision = Critical Task -> THINKING Tier
        """
        provider = self._get_provider(LLMTier.THINKING)
        if provider is None:
            return {"decision": "REJECT", "reason": "JennieBrain 초기화 실패 (Thinking Tier)", "quantity": 0}

        try:
            if trade_type == 'BUY_MR':
                buy_signal_type = kwargs.get('buy_signal_type', 'UNKNOWN')
                prompt = build_buy_prompt_mean_reversion(stock_info, buy_signal_type)
            elif trade_type == 'BUY_TREND':
                buy_signal_type = kwargs.get('buy_signal_type', 'GOLDEN_CROSS')
                # Use aliased function
                prompt = build_buy_prompt_golden_cross(stock_info, buy_signal_type)
            elif trade_type == 'SELL':
                market_status = kwargs.get('market_status', 'N/A') # build_sell_prompt expects stock_info mainly
                # build_sell_prompt signature: (stock_info). market_status implies prompt builder change?
                # Assuming build_sell_prompt only takes stock_info for now based on outline.
                prompt = build_sell_prompt(stock_info)
            else:
                return {"decision": "REJECT", "reason": "알 수 없는 거래 유형", "quantity": 0}

            logger.info(f"--- [JennieBrain] 결재 요청 ({trade_type}) via {provider.name} ---")
            
            # JSON 응답 스키마
            DECISION_SCHEMA = {
                "type": "object",
                "properties": {
                    "decision": {"type": "string", "enum": ["APPROVE", "REJECT", "HOLD"]},
                    "reason": {"type": "string"},
                    "quantity": {"type": "integer"}
                },
                "required": ["decision", "reason", "quantity"]
            }

            result = provider.generate_json(
                prompt,
                DECISION_SCHEMA,
                temperature=0.1
            )
            
            logger.info(f"   👑 제니의 결재: {result.get('decision')} ({result.get('reason')})")
            return result

        except Exception as e:
            logger.error(f"❌ [JennieBrain] 결재 중 오류: {e}")
            return {"decision": "REJECT", "reason": f"System Error: {e}", "quantity": 0}

    # -----------------------------------------------------------------
    # 뉴스 감성 분석
    # -----------------------------------------------------------------
    def analyze_news_sentiment(self, title, description):
        """
        뉴스 제목과 요약을 분석하여 긍정/부정 점수를 매깁니다.
        High Volume / Low Risk -> FAST Tier (Local LLM)
        """
        provider = self._get_provider(LLMTier.FAST)
        if provider is None:
             return {'score': 50, 'reason': '모델 미초기화 (기본값)'}

        try:
            # build_news_sentiment_prompt args: news_title, news_summary
            prompt = build_news_sentiment_prompt(title, description)
            # logger.debug(f"--- [JennieBrain] 뉴스 분석 via {provider.name} ---")
            
            result = provider.generate_json(
                prompt,
                ANALYSIS_RESPONSE_SCHEMA,
                temperature=0.0 # Deterministic
            )
            return result
        except Exception as e:
            logger.warning(f"⚠️ [News] Local LLM failed: {e}. Attempting Cloud Fallback...")
            try:
                # Fallback to THINKING Tier (Cloud)
                fallback_provider = self._get_provider(LLMTier.THINKING)
                result = fallback_provider.generate_json(
                    prompt,
                    ANALYSIS_RESPONSE_SCHEMA,
                    temperature=0.0
                )
                logger.info(f"   ✅ [News] Cloud Fallback Success via {fallback_provider.name}")
                return result
            except Exception as fb_e:
                logger.error(f"❌ [News] Fallback failed: {fb_e}")
                return {'score': 50, 'reason': f'분석 실패 (Local+Cloud): {e}'}

    # -----------------------------------------------------------------
    # 토론 (Bull vs Bear)
    # -----------------------------------------------------------------
    def run_debate_session(self, stock_info: dict, analysis_context: str = "", hunter_score: int = 0) -> str:
        """
        Bull vs Bear 토론 생성 (Dynamic Role Allocation)
        Complex Creative Task -> REASONING Tier
        """
        provider = self._get_provider(LLMTier.REASONING)
        if provider is None:
             return "Debate Skipped (Model Error)"

        try:
            # [v6.0] Extract keywords for Dynamic Debate Context
            keywords = stock_info.get('dominant_keywords', [])
            
            # Pass hunter_score and keywords to build_debate_prompt
            prompt = build_debate_prompt(stock_info, hunter_score, keywords) 
            
            # [v6.0 Fix] generate_chat requires list of dicts, not str
            chat_history = [{"role": "user", "content": prompt}]
            
            logger.info(f"--- [JennieBrain/Debate] 토론 시작 via {provider.name} (HunterScore: {hunter_score}, KW: {keywords}) ---")
            
            result = provider.generate_chat(chat_history, temperature=0.7)
            # If result is dict (e.g. from structured output), extracting text. 
            # generate_chat usually returns dict with 'text' or json. 
            # But the caller expects str. 
            if isinstance(result, dict):
                return result.get('text') or result.get('content') or str(result)
            return str(result)

        except Exception as e:
            logger.warning(f"⚠️ [Debate] Local LLM failed: {e}. Attempting Cloud Fallback...")
            try:
                fallback_provider = self._get_provider(LLMTier.THINKING)
                if fallback_provider is None:
                    raise ValueError("Fallback provider (Thinking Tier) not available")

                logger.info(f"--- [JennieBrain/Debate] Cloud Fallback via {fallback_provider.name} ---")
                chat_history = [{"role": "user", "content": prompt}]
                result = fallback_provider.generate_chat(chat_history, temperature=0.7)
                
                if isinstance(result, dict):
                    return result.get('text') or result.get('content') or str(result)
                return str(result)
            except Exception as fb_e:
                logger.error(f"❌ [Debate] Fallback failed: {fb_e}")
                return f"Debate Error: {e}"

    # -----------------------------------------------------------------
    # Check if stock exists (Legacy helper, optional)
    # -----------------------------------------------------------------
    def verify_parameter_change(self, stock_info: dict, param_name: str, old_val, new_val) -> dict:
        # Simple task -> FAST Tier
        provider = self._get_provider(LLMTier.FAST)
        if not provider: return {"authorized": False}
        return {"authorized": True, "reason": "Auto-approved by FAST tier"}

    # -----------------------------------------------------------------
    # [v4.0] Judge (Supreme Jennie) 최종 판결
    # -----------------------------------------------------------------
    def run_judge_scoring(self, stock_info: dict, debate_log: str) -> dict:
        """
        Judge Scoring = Critical Decision -> THINKING Tier
        """
        provider = self._get_provider(LLMTier.THINKING)
        if provider is None:
             return {'score': 0, 'grade': 'D', 'reason': 'Provider Error'}

        try:
            prompt = build_judge_prompt(stock_info, debate_log)
            logger.info(f"--- [JennieBrain/Judge] 판결 via {provider.name} ---")
            
            result = provider.generate_json(
                prompt,
                JUDGE_RESPONSE_SCHEMA, 
                temperature=0.1
            )
            return result
        except Exception as e:
            logger.error(f"❌ [Judge] 판결 실패: {e}")
            return {'score': 0, 'grade': 'D', 'reason': f"오류: {e}"}

    # -----------------------------------------------------------------
    # [v1.0] Scout Hybrid Scoring
    # -----------------------------------------------------------------
    def get_jennies_analysis_score_v5(self, stock_info: dict, quant_context: str = None) -> dict:
        """
        v5 Hunter = Reasoning Task -> REASONING Tier
        """
        provider = self._get_provider(LLMTier.REASONING)
        if provider is None:
            return {'score': 0, 'grade': 'D', 'reason': 'Provider Error'}
        
        try:
            prompt = build_hunter_prompt_v5(stock_info, quant_context)
            logger.info(f"--- [JennieBrain/v5-Hunter] 분석 via {provider.name} ---")
            
            result = provider.generate_json(
                prompt,
                ANALYSIS_RESPONSE_SCHEMA,
                temperature=0.2
            )
            logger.info(f"   ✅ v5 Hunter 완료: {stock_info.get('name')} - {result.get('score')}점")
            return result
        except Exception as e:
            logger.warning(f"⚠️ [v5-Hunter] Local LLM failed: {e}. Attempting Cloud Fallback...")
            try:
                fallback_provider = self._get_provider(LLMTier.THINKING)
                if fallback_provider is None:
                    raise ValueError("Fallback provider (Thinking Tier) not available")

                logger.info(f"--- [JennieBrain/v5-Hunter] Cloud Fallback via {fallback_provider.name} ---")
                result = fallback_provider.generate_json(
                    prompt,
                    ANALYSIS_RESPONSE_SCHEMA,
                    temperature=0.2
                )
                return result
            except Exception as fb_e:
                logger.error(f"❌ [v5-Hunter] Fallback failed: {fb_e}")
                return {'score': 0, 'grade': 'D', 'reason': f"오류(Local+Cloud): {e}"}

    def run_judge_scoring_v5(self, stock_info: dict, debate_log: str, quant_context: str = None) -> dict:
        """
        v5 Judge = Critical Decision -> THINKING Tier
        [Strategy Gate]: Hunter score < 70 (Grade B) will be auto-rejected to save Cloud costs and avoid weak signals.
        """
        # 1. Strategy Gate Check (Junho's Condition)
        hunter_score = stock_info.get('hunter_score', 0)
        # Default Threshold: 70 (B Grade)
        JUDGE_THRESHOLD = 70 
        
        if hunter_score < JUDGE_THRESHOLD:
            logger.info(f"🚫 [Gatekeeper] Judge Skipped. Hunter Score {hunter_score} < {JUDGE_THRESHOLD}. Auto-Reject.")
            return {
                'score': hunter_score, 
                'grade': 'D', 
                'reason': f"Hunter Score({hunter_score}) failed to meet Judge Threshold({JUDGE_THRESHOLD}). Auto-Rejected."
            }

        provider = self._get_provider(LLMTier.THINKING)
        if provider is None:
            return {'score': 0, 'grade': 'D', 'reason': 'Provider Error'}
            
        try:
            # 2. Structured Logging (Minji's Request)
            call_reason = "HighConviction_Verification"
            logger.info(json.dumps({
                "event": "ThinkingTier_Call",
                "tier": "THINKING",
                "task": "Judge_v5",
                "model": provider.client.__class__.__name__ if hasattr(provider, 'client') else provider.name,
                "reason": call_reason,
                "input_score": hunter_score
            }))
            
            prompt = build_judge_prompt_v5(stock_info, debate_log, quant_context)
            logger.info(f"--- [JennieBrain/v5-Judge] 판결 via {provider.name} (Why: {call_reason}) ---")
            
            result = provider.generate_json(
                prompt,
                ANALYSIS_RESPONSE_SCHEMA,
                temperature=0.1
            )
            return result
        except Exception as e:
            logger.error(f"❌ [v5-Judge] 오류: {e}")
            return {'score': 0, 'grade': 'D', 'reason': f"오류: {e}"}

    # -----------------------------------------------------------------
    # [New] Daily Briefing (Centralized from reporter.py)
    # -----------------------------------------------------------------
    def generate_daily_briefing(self, market_summary: str, execution_log: str) -> str:
        """
        Generate Daily Briefing Report.
        Task Type: REASONING or THINKING (depending on desired quality).
        Let's use THINKING for high quality report.
        """
        provider = self._get_provider(LLMTier.THINKING) 
        if provider is None:
            return "보고서 생성 실패: 모델 초기화 오류"

        prompt = f"""
        당신은 프로페셔널 주식 투자 보고서 작성자입니다.
        오늘의 시장 상황과 자동매매 수행 로그를 바탕으로 '일일 브리핑 리포트'를 작성해주세요.

        [시장 요약]
        {market_summary}

        [오늘의 매매 수행 로그]
        {execution_log}

        [작성 가이드]
        1. 톤앤매너: 전문적이고 신뢰감 있게, 그러나 격려하는 말투.
        2. 구조: 시장 현황 -> 매매 성과 -> 향후 전략 -> 마무리 인사.
        3. 텔레그램 메신저용으로 Markdown 포맷을 사용하여 가독성 좋게 작성.
        """
        try:
            logger.info(f"--- [JennieBrain/Briefing] 리포트 생성 via {provider.name} ---")
            # [v6.0 Fix] generate_chat requires list of dicts
            chat_history = [{"role": "user", "content": prompt}]
            result = provider.generate_chat(chat_history, temperature=0.7)
            
            if isinstance(result, dict):
                return result.get('text') or result.get('content') or str(result)
            return str(result)
        except Exception as e:
            logger.error(f"❌ [Briefing] 실패: {e}")
            return "보고서 생성 중 오류가 발생했습니다."

    # -----------------------------------------------------------------
    # Legacy V1 Methods (Placeholders to prevent import errors)
    # -----------------------------------------------------------------
    def detect_competitor_events(self, target_stock_code: str, target_stock_name: str, sector: str, recent_news: List[Dict]) -> dict:
        return {'competitor_events': [], 'total_benefit_score': 0, 'analysis_reason': 'Legacy method placeholder'}

    def get_beneficiary_recommendations(self, event_company: str, event_type: str, event_summary: str, sector: str) -> dict:
        return {'beneficiaries': [], 'top_pick': None, 'holding_period': 'N/A', 'risk_note': 'Legacy method placeholder'}
