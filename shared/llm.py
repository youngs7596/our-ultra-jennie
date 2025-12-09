"""
shared/llm.py - Ultra Jennie LLM 오케스트레이션 모듈
=====================================================

이 모듈은 멀티 LLM 기반 투자 의사결정 엔진을 제공합니다.

[v5.0] Provider 클래스들이 llm_providers.py로 분리됨

핵심 구성요소:
-------------
1. JennieBrain: 멀티 LLM 오케스트레이션 메인 클래스

의사결정 파이프라인:
------------------
1. Scout (Gemini): 정량 데이터 기반 1차 스크리닝
2. Hunter (Claude): 심층 펀더멘털 분석 + 경쟁사 수혜 분석
3. Debate: Bull vs Bear AI 토론 (선택적)
4. Judge (OpenAI): 최종 승인/거부 판단

사용 예시:
---------
>>> from shared.llm import JennieBrain
>>> brain = JennieBrain()
>>> 
>>> # 종목 분석 (하이브리드 스코어링)
>>> result = brain.get_jennies_analysis_score_v5(decision_info, quant_context)
>>> print(f"Score: {result['score']}, Grade: {result['grade']}")
>>>
>>> # 뉴스 감성 분석
>>> sentiment = brain.analyze_news_sentiment(title, summary)

JSON 응답 스키마:
----------------
- RESPONSE_SCHEMA: 기본 의사결정 (decision, reason, quantity)
- ANALYSIS_RESPONSE_SCHEMA: 점수 분석 (score, grade, reason)
- SENTIMENT_RESPONSE_SCHEMA: 감성 분석 (score, reason)

환경변수:
--------
- SECRET_ID_GEMINI_API_KEY: Gemini API 키 시크릿 ID
- SECRET_ID_OPENAI_API_KEY: OpenAI API 키 시크릿 ID  
- SECRET_ID_CLAUDE_API_KEY: Claude API 키 시크릿 ID
- LLM_MODEL_NAME: Gemini 모델명 (기본: gemini-2.5-flash)
- OPENAI_MODEL_NAME: OpenAI 모델명 (기본: gpt-4o-mini)
- CLAUDE_MODEL_NAME: Claude 모델명 (기본: claude-sonnet-4-20250514)
"""

import logging
import os

# [v5.0] Provider 클래스들을 llm_providers.py에서 import
from .llm_providers import (
    BaseLLMProvider,
    GeminiLLMProvider,
    OpenAILLMProvider,
    ClaudeLLMProvider,
    build_llm_provider,
)
# [v5.1] 프롬프트 빌더 함수들을 llm_prompts.py에서 import
from .llm_prompts import (
    build_buy_prompt_mean_reversion,
    build_buy_prompt_golden_cross,
    build_buy_prompt_ranking,
    build_sell_prompt,
    build_add_watchlist_prompt,
    build_analysis_prompt,
    build_parameter_verification_prompt,
    build_news_sentiment_prompt,
    build_debate_prompt,
    build_judge_prompt,
    build_hunter_prompt_v5,
    build_judge_prompt_v5,
    build_context_analysis_prompt,
)
from .llm_constants import (
    LLM_MODEL_NAME,
    RESPONSE_SCHEMA,
    RANKING_RESPONSE_SCHEMA,
    ANALYSIS_RESPONSE_SCHEMA,
    SENTIMENT_RESPONSE_SCHEMA,
    GENERATION_CONFIG,
    SAFETY_SETTINGS,
)

# "youngs75_jennie.llm" 이름으로 로거 생성
logger = logging.getLogger(__name__)


# [v5.0] Provider 클래스들은 llm_providers.py로 이동됨
# BaseLLMProvider, GeminiLLMProvider, OpenAILLMProvider, ClaudeLLMProvider, build_llm_provider
# 위 클래스들은 .llm_providers에서 import됨


class JennieBrain:
    """
    LLM을 사용하여 'BUY' 또는 'SELL' 신호에 대한 최종 결재를 수행합니다.
    [v4.0] Claude (빠른 필터링) + OpenAI GPT (깊이 있는 분석) 하이브리드 전략
    """
    
    def __init__(self, project_id, gemini_api_key_secret):
        try:
            # Gemini: 뉴스 감성 분석용
            self.provider_gemini = build_llm_provider(project_id, gemini_api_key_secret, "gemini")
            logger.info("--- [JennieBrain] Gemini Provider 로드 완료 ---")
            
            # [v4.0] Claude: Phase 1 Hunter (빠르고 똑똑함)
            try:
                claude_api_key_secret = os.getenv("CLAUDE_API_KEY_SECRET", "claude-api-key")
                self.provider_claude = ClaudeLLMProvider(project_id, claude_api_key_secret, SAFETY_SETTINGS)
                logger.info("--- [JennieBrain] Claude Provider 로드 완료 (Phase 1 Hunter용) ---")
            except Exception as e:
                logger.warning(f"⚠️ [JennieBrain] Claude Provider 로드 실패 (GPT로 폴백): {e}")
                self.provider_claude = None
            
            # OpenAI GPT: Reasoning-heavy tasks (Debate, Judge)
            try:
                self.provider_openai = build_llm_provider(project_id, gemini_api_key_secret, "openai")
                logger.info("--- [JennieBrain] OpenAI Provider 로드 완료 ---")
            except Exception as e:
                logger.warning(f"⚠️ [JennieBrain] OpenAI Provider 로드 실패 (Gemini로 폴백): {e}")
                self.provider_openai = None
            
            # 기본 Provider (하위 호환성)
            self.provider = self.provider_gemini
            
        except Exception as e:
            logger.critical(f"❌ [JennieBrain] 초기화 실패: {e}")
            self.provider = None
            self.provider_gemini = None
            self.provider_openai = None
            self.provider_claude = None


    # [v5.1] 프롬프트 빌더 메서드들은 llm_prompts.py로 이동됨
    # build_buy_prompt_mean_reversion, build_buy_prompt_golden_cross, build_buy_prompt_ranking,
    # build_sell_prompt, build_add_watchlist_prompt 등 - llm_prompts에서 import하여 사용


    # [v5.1] 위 프롬프트 빌더 메서드들(build_buy_*, build_sell_*, build_add_watchlist_*)은 
    # llm_prompts.py로 이동됨 - 약 300라인 감소

    # -----------------------------------------------------------------
    # '제니' 결재 실행
    # -----------------------------------------------------------------
    def get_jennies_decision(self, trade_type, stock_info, **kwargs):
        """
        LLM을 호출하여 최종 결재를 받습니다.
        'BUY_MR'의 경우, buy_signal_type을 추가로 받아 프롬프트에 전달합니다.
        """
        
        if self.provider is None:
            logger.error("❌ [JennieBrain] 모델이 초기화되지 않았습니다!")
            return {"decision": "REJECT", "reason": "JennieBrain 초기화 실패", "quantity": 0}

        try:
            # 1. 상황에 맞는 프롬프트 생성
            if trade_type == 'BUY_MR':
                buy_signal_type = kwargs.get('buy_signal_type', 'UNKNOWN')
                prompt = build_buy_prompt_mean_reversion(stock_info, buy_signal_type)
            elif trade_type == 'BUY_TREND':
                buy_signal_type = kwargs.get('buy_signal_type', 'GOLDEN_CROSS')
                prompt = build_buy_prompt_golden_cross(stock_info, buy_signal_type=buy_signal_type)
            elif trade_type in ['SELL', 'SELL_V2']:
                prompt = build_sell_prompt(stock_info)
            elif trade_type == 'ADD_WATCHLIST':
                prompt = build_add_watchlist_prompt(stock_info)
            else:
                logger.error(f"❌ [JennieBrain] 알 수 없는 요청 타입: {trade_type}")
                return {"decision": "REJECT", "reason": "알 수 없는 요청 타입", "quantity": 0}

            logger.info(f"--- [JennieBrain] LLM 결재 요청 ({trade_type}) ---")
            
            # 2. '제니'의 뇌(LLM) 호출
            decision_json = self.provider.generate_json(
                prompt,
                RESPONSE_SCHEMA,
                temperature=GENERATION_CONFIG.get("temperature", 0.2),
            )
            
            logger.info(f"--- [JennieBrain] LLM 결재 완료 ---")
            logger.info(f"   (결정): {decision_json.get('decision')}")
            logger.info(f"   (수량): {decision_json.get('quantity', 0)}")
            logger.info(f"   (사유): {decision_json.get('reason')}")
            
            return decision_json

        except Exception as e:
            logger.error(f"❌ [JennieBrain] LLM 결재 중 오류: {e}", exc_info=True)
            return {"decision": "REJECT", "reason": f"LLM 결재 오류: {e}", "quantity": 0}
    
    # -----------------------------------------------------------------
    # [v2.5] Top-N 랭킹 결재 실행
    # -----------------------------------------------------------------
    def get_jennies_ranking_decision(self, candidates_data: list):
        """
        [v2.5] 팩터 점수 상위 N개 후보 리스트를 LLM에 전달하여 최종 1개 종목을 선정받습니다.
        
        Args:
            candidates_data: 팩터 점수 상위 N개 후보 리스트 (각 후보는 dict 형태, 최대 5개)
                - stock_code, stock_name, stock_info, current_price, realtime_snapshot,
                  daily_prices_df, buy_signal_type, key_metrics_dict, factor_score,
                  factors, rag_context 등 포함
        
        Returns:
            dict: {
                'best_stock_code': str,  # 선정된 종목 코드 또는 'REJECT_ALL'
                'reason': str,
                'quantity': int
            }
        """
        
        if self.provider is None:
            logger.error("❌ [JennieBrain] 모델이 초기화되지 않았습니다!")
            return {"best_stock_code": "REJECT_ALL", "reason": "JennieBrain 초기화 실패", "quantity": 0}
        
        try:
            # 1. 랭킹 프롬프트 생성
            prompt = build_buy_prompt_ranking(candidates_data)
            
            logger.info(f"--- [JennieBrain] Top-{len(candidates_data)} 랭킹 결재 요청 ---")
            
            # 2. 랭킹 전용 Generation Config 생성
            ranking_config = {
                "temperature": 0.3,  # 약간 높여서 비교 분석 유도
                "response_mime_type": "application/json",
                "response_schema": RANKING_RESPONSE_SCHEMA,
            }
            
            # 4. '제니'의 뇌(LLM) 호출
            decision_json = self.provider.generate_json(
                prompt,
                RANKING_RESPONSE_SCHEMA,
                temperature=ranking_config["temperature"],
            )
            
            logger.info(f"--- [JennieBrain] Top-{len(candidates_data)} 랭킹 결재 완료 ---")
            logger.info(f"   (선정): {decision_json.get('best_stock_code')}")
            logger.info(f"   (수량): {decision_json.get('quantity', 0)}")
            logger.info(f"   (사유): {decision_json.get('reason')[:100]}..." if len(decision_json.get('reason', '')) > 100 else f"   (사유): {decision_json.get('reason')}")
            
            return decision_json
            
        except Exception as e:
            logger.error(f"❌ [JennieBrain] Top-N 랭킹 결재 중 오류: {e}", exc_info=True)
            return {"best_stock_code": "REJECT_ALL", "reason": f"LLM 랭킹 결재 오류: {e}", "quantity": 0}
    
    # -----------------------------------------------------------------
    # [v2.2] 파라미터 변경 검증
    # -----------------------------------------------------------------
    def verify_parameter_change(self, current_params: dict, new_params: dict,
                                current_performance: dict, new_performance: dict,
                                market_summary: str) -> dict:
        """
        [v2.2] 자동 파라미터 최적화 시 LLM을 통한 검증
        """
        if self.provider is None:
            logger.error("❌ [JennieBrain] 모델이 초기화되지 않았습니다!")
            return {
                'is_approved': False,
                'reasoning': 'JennieBrain 초기화 실패',
                'confidence_score': 0.0
            }
        
        try:
            prompt = build_parameter_verification_prompt(
                current_params, new_params,
                current_performance, new_performance,
                market_summary
            )
            
            logger.info("--- [JennieBrain] 파라미터 변경 검증 요청 ---")
            
            # JSON 스키마 정의 (검증 전용)
            verification_schema = {
                "type": "object",
                "properties": {
                    "is_approved": {
                        "type": "boolean",
                        "description": "파라미터 변경 승인 여부"
                    },
                    "reasoning": {
                        "type": "string",
                        "description": "승인/거부 사유 (상세)"
                    },
                    "confidence_score": {
                        "type": "number",
                        "description": "신뢰도 점수 (0.0~1.0)"
                    }
                },
                "required": ["is_approved", "reasoning", "confidence_score"]
            }
            
            # 임시 GenerationConfig (검증 전용)
            verification_config = {
                "temperature": 0.3,  # 약간 높여서 분석적 사고 유도
                "response_mime_type": "application/json",
                "response_schema": verification_schema,
            }
            
            result = self.provider.generate_json(
                prompt,
                verification_schema,
                temperature=verification_config["temperature"],
            )
            
            logger.info(f"--- [JennieBrain] 파라미터 검증 완료 ---")
            logger.info(f"   (승인): {result.get('is_approved')}")
            logger.info(f"   (신뢰도): {result.get('confidence_score'):.2f}")
            logger.info(f"   (사유): {result.get('reasoning')[:100]}...")
            
            return result
            
        except Exception as e:
            logger.error(f"❌ [JennieBrain] 파라미터 검증 중 오류: {e}", exc_info=True)
            return {
                'is_approved': False,
                'reasoning': f'검증 오류: {str(e)}',
                'confidence_score': 0.0
            }
    
    # [v5.1] _build_parameter_verification_prompt는 llm_prompts.py로 이동됨

    # -----------------------------------------------------------------
    # [v3.0] 종목 심층 분석 및 점수 산출 (Scout 단계)
    # -----------------------------------------------------------------
    def get_jennies_analysis_score(self, stock_info):
        """
        종목의 뉴스, 펀더멘털, 모멘텀을 종합하여 매수 적합도 점수(0~100)를 산출합니다.
        [Phase 1: Hunter Scout] - Claude Haiku 우선, 실패 시 OpenAI/Gemini 폴백
        """
        # [v4.2] Dream Team Config: Hunter = Gemini 2.5 Flash
        # 물량 공세가 가능한 Gemini Flash를 최우선으로 사용
        providers = []
        if self.provider_gemini:
            providers.append(('GEMINI', self.provider_gemini))
        if hasattr(self, 'provider_claude') and self.provider_claude:
            providers.append(('CLAUDE', self.provider_claude))
        if self.provider_openai:
            providers.append(('OPENAI', self.provider_openai))
        
        if not providers:
            logger.error("❌ [JennieBrain] LLM 모델이 초기화되지 않았습니다!")
            return {'score': 0, 'grade': 'D', 'reason': 'JennieBrain 초기화 실패'}
        
        prompt = build_analysis_prompt(stock_info)
        last_error = None
        
        for provider_name, provider in providers:
            try:
                logger.info(f"--- [JennieBrain/Phase1-Hunter] 필터링 ({provider_name}): {stock_info.get('name')} ---")
                
                # [v4.2] Gemini인 경우 Flash 모델 강제 사용
                model_name = None
                if provider_name == 'GEMINI' and hasattr(provider, 'flash_model_name'):
                    model_name = provider.flash_model_name()
                
                result = provider.generate_json(
                    prompt,
                    ANALYSIS_RESPONSE_SCHEMA,
                    temperature=0.3,
                    model_name=model_name
                )
                
                logger.info(f"--- [JennieBrain] 분석 완료 ({provider_name}): {stock_info.get('name')} ---")
                
                # [v4.5] 점수 범위 제한 (LLM이 100점 초과 반환하는 경우 방지)
                raw_score = result.get('score', 0)
                capped_score = min(100, max(0, raw_score))
                if raw_score != capped_score:
                    logger.warning(f"   ⚠️ 점수 보정: {raw_score}점 → {capped_score}점")
                result['score'] = capped_score
                
                logger.info(f"   (점수): {result.get('score')}점 (등급: {result.get('grade')})")
                
                return result
                
            except Exception as e:
                last_error = e
                logger.warning(f"⚠️ [JennieBrain] {provider_name} 실패, 폴백 시도: {e}")
                continue
        
        logger.error(f"❌ [JennieBrain] 모든 LLM 실패: {last_error}", exc_info=True)
        return {'score': 0, 'grade': 'D', 'reason': f"분석 오류: {last_error}"}

    # [v5.1] _build_analysis_prompt는 llm_prompts.py로 이동됨

    # -----------------------------------------------------------------
    # [New] 실시간 뉴스 감성 분석 (Crawler용)
    # -----------------------------------------------------------------
    def analyze_news_sentiment(self, news_title, news_summary):
        """
        실시간으로 뉴스의 감성 점수(0~100)를 산출합니다.
        [뉴스 감성 분석] - Gemini-2.5-Flash (빠르고 정확)
        
        Args:
            news_title (str): 뉴스 제목
            news_summary (str): 뉴스 요약 (또는 본문 일부)
            
        Returns:
            dict: {'score': 85, 'reason': '...'}
        """
        if self.provider_gemini is None:
            return {'score': 50, 'reason': '모델 미초기화 (기본값)'}

        try:
            prompt = f"""
            [금융 뉴스 감성 분석]
            당신은 '금융 전문가'입니다. 아래 뉴스를 보고 해당 종목에 대한 호재/악재 여부를 점수로 판단해주세요.
            
            - 뉴스 제목: {news_title}
            - 뉴스 내용: {news_summary}
            
            [채점 기준]
            - 80 ~ 100점 (강력 호재): 실적 서프라이즈, 대규모 수주, 신기술 개발, 인수합병, 배당 확대
            - 60 ~ 79점 (호재): 긍정적 전망 리포트, 목표가 상향
            - 40 ~ 59점 (중립): 단순 시황, 일반적인 소식, 이미 반영된 뉴스
            - 20 ~ 39점 (악재): 실적 부진, 목표가 하향
            - 0 ~ 19점 (강력 악재): 어닝 쇼크, 유상증자(악재성), 횡령/배임, 계약 해지, 규제 강화
            
            [출력 형식]
            JSON으로 응답: {{ "score": 점수(int), "reason": "판단 이유(한 문장)" }}
            """
            
            # Gemini-Flash 사용 (빠르고 비용 효율적)
            logger.debug(f"--- [JennieBrain/News-Sentiment] Gemini-Flash로 감성 분석 ---")
            result = self.provider_gemini.generate_json(
                prompt,
                SENTIMENT_RESPONSE_SCHEMA,
                temperature=0.1,
                model_name=self.provider_gemini.flash_model_name(),
                fallback_models=["gemini-1.5-flash"],
            )
            return result
            
        except Exception as e:
            logger.error(f"❌ [JennieBrain] 감성 분석 오류: {e}")
            return {'score': 50, 'reason': f"분석 오류: {e}"}

    # -----------------------------------------------------------------
    # [v4.0] Debate (Bull vs Bear) 세션 실행
    # -----------------------------------------------------------------
    def run_debate_session(self, stock_info: dict) -> str:
        """
        한 종목에 대해 Bull(낙관론자)과 Bear(비관론자)가 토론하는 시뮬레이션을 수행하고,
        토론 로그(텍스트)를 반환합니다.
        [Phase 2: Debate] - GPT-5.1-mini (깊이 있는 분석 및 토론)
        """
        # [v4.2] Dream Team Config: Phase 2 Debate (Bull vs Bear)
        # 1순위: Claude (Haiku) - 말이 유려하고 빠름
        # 2순위: Gemini (Flash) - 빠름
        # 3순위: OpenAI (Mini) - 폴백
        
        provider = None
        if hasattr(self, 'provider_claude') and self.provider_claude:
            provider = self.provider_claude
        elif self.provider_gemini:
            provider = self.provider_gemini
        elif self.provider_openai:
            provider = self.provider_openai
            
        if provider is None:
            return "Debate Skipped (Model Error)"

        # 1. 기본 정보 포맷팅
        name = stock_info.get('name', 'N/A')
        code = stock_info.get('code', 'N/A')
        tech_reason = stock_info.get('technical_reason', 'N/A')
        news_reason = stock_info.get('news_reason', 'N/A')
        
        # 2. System Prompt (토론 사회자 역할은 코드에서 제어, LLM은 각 턴의 발화 생성)
        # 하지만 여기서는 Chat 모드를 사용하여 LLM이 'Bull'과 'Bear' 역할을 번갈아 수행하게 하거나,
        # 단일 프롬프트로 "Bull과 Bear의 대화를 생성해줘"라고 요청하는 것이 비용/속도 면에서 효율적일 수 있음.
        # **Scout Job의 특성상 단일 호출로 대화록을 생성하는 것이 낫습니다.**
        
        # [v4.0] 제니 피드백 반영 - 더 치열한 Debate
        prompt = f"""
        [Roleplay Simulation: 치열한 Bull vs Bear Debate]
        당신은 주식 투자 토론의 '서기'입니다. 
        주어진 종목에 대해 'Bull'과 'Bear'가 **치열하게 싸우는** 시나리오를 작성해주세요.
        **서로 양보하지 마세요. 끝까지 자기 주장을 고수하세요.**

        [종목 정보]
        - 종목: {name} ({code})
        - 재료/뉴스: {news_reason}
        - 펀더멘털: PER {stock_info.get('per', 'N/A')}, PBR {stock_info.get('pbr', 'N/A')}
        - 시가총액: {stock_info.get('market_cap', 'N/A')}

        [캐릭터 설정 - 극단적으로!]
        
        **Bull (공격적 성장주 펀드매니저)**:
        - 당신은 레버리지를 즐기는 공격적인 펀드매니저입니다.
        - 미래 가치와 성장 잠재력을 숫자로 증명하세요.
        - "지금 안 사면 후회한다"는 논리로 밀어붙이세요.
        - 호재를 과대평가하고, 악재는 "이미 반영됐다"고 무시하세요.
        
        **Bear (회의적인 공매도 세력)**:
        - 당신은 숏 포지션을 잡은 헤지펀드 매니저입니다.
        - 아주 작은 악재라도 침소봉대해서 공격하세요.
        - "이 뉴스는 이미 가격에 반영됐다", "고점이다"라고 주장하세요.
        - 거시경제 리스크, 금리, 환율, 경쟁사 위협을 들이대세요.
        - 호재가 있어도 "지속 가능하지 않다"고 깎아내리세요.

        [작성 지침]
        1. 총 4턴의 대화를 주고받으세요.
        2. **절대 합의하지 마세요.** 끝까지 평행선을 달리세요.
        3. 서로의 주장을 날카롭게 반박하세요.
        4. 구체적인 숫자와 논리로 싸우세요.
        5. 한국어로 자연스럽게 대화하듯 작성하세요.
        
        [출력 예시]
        Bull: 이 종목 PER 8배야. 업종 평균 15배 대비 거의 반값이라고! 지금 안 사면 바보지.
        Bear: PER가 낮은 건 시장이 성장성을 안 믿는다는 거야. 밸류 트랩일 수 있어.
        Bull: 뭔 소리야, 이번 분기 수주 3조 터졌잖아. 실적 서프라이즈 확정이야!
        Bear: 수주? 그거 마진 얼마나 남는데? 원가 상승으로 다 까먹을 걸?
        ...
        """
        
        try:
            # Chat 모드 대신 일반 generate_content 사용 (토론 스크립트 생성)
            # JSON 스키마 없이 자유 텍스트 생성
            # Provider에 generate_text 메서드가 없으므로 generate_json의 기반이 되는 로직을 활용하거나,
            # 임시로 JSON으로 래핑해서 받음 -> { "debate_log": "..." }
            
            DEBATE_SCHEMA = {
                "type": "object",
                "properties": {
                    "debate_log": {"type": "string", "description": "Bull과 Bear의 전체 토론 내용"}
                },
                "required": ["debate_log"]
            }
            
            # [v4.2] Dream Team Config
            # Claude: Haiku (Fast)
            # Gemini: Flash
            # OpenAI: Mini
            model_name = None
            if provider.name == 'claude':
                model_name = getattr(provider, 'fast_model', None)
            elif provider.name == 'gemini':
                model_name = provider.flash_model_name()
            # OpenAI는 기본 Mini 사용

            logger.info(f"--- [JennieBrain/Phase2-Debate] 깊이 있는 토론 ({provider.name}): {stock_info.get('name')} ---")
            
            result = provider.generate_json(
                prompt, 
                DEBATE_SCHEMA,
                temperature=0.7, # 창의적인 토론을 위해 온도 높임
                model_name=model_name
            )
            return result.get("debate_log", "토론 생성 실패")
            
        except Exception as e:
            logger.error(f"❌ [Debate] 토론 생성 실패: {e}")
            return f"Debate Error: {e}"

    # -----------------------------------------------------------------
    # [v4.0] Judge (Supreme Jennie) 최종 판결
    # -----------------------------------------------------------------
    def run_judge_scoring(self, stock_info: dict, debate_log: str) -> dict:
        """
        Debate 로그와 종목 정보를 바탕으로 'Judge(재판관)'가 최종 점수와 승인을 결정합니다.
        [Phase 3: Judge] - GPT-5.1-mini (체계적인 최종 판단)
        """
        # [v4.2] Dream Team Config: Phase 3 Judge (Supreme Jennie)
        # 1순위: Claude (Sonnet) - 냉철한 판단
        # 2순위: Gemini (Pro) - 차선
        # 3순위: OpenAI (Mini) - 폴백
        
        provider = None
        if hasattr(self, 'provider_claude') and self.provider_claude:
            provider = self.provider_claude
        elif self.provider_gemini:
            provider = self.provider_gemini
        elif self.provider_openai:
            provider = self.provider_openai

        if provider is None:
             return {'score': 0, 'grade': 'D', 'reason': 'JennieBrain 초기화 실패'}

        name = stock_info.get('name', 'N/A')
        
        # [v3.9] Judge에게도 뉴스 정보 직접 전달
        news_reason = stock_info.get('news_reason', 'N/A')
        per = stock_info.get('per', 'N/A')
        pbr = stock_info.get('pbr', 'N/A')
        market_cap = stock_info.get('market_cap', 'N/A')
        
        # [v4.0] Judge 프롬프트 - 기본 50점 기준 명시
        prompt = f"""당신은 주식 투자 최종 판결자입니다. Bull과 Bear의 토론을 듣고 최종 점수를 매기세요.

## 종목 정보
- 종목: {name}
- PER: {per}, PBR: {pbr}
- 시가총액: {market_cap}

## 최신 뉴스/재료
{news_reason}

## Debate Log (Bull vs Bear 토론)
{debate_log}

## 점수 계산 (기본 50점에서 시작)

1. **토론 결과 가감점**:
   - Bull이 구체적 수치로 압승: +20~30점
   - Bull이 논리적 우세: +10~15점
   - 팽팽함 (무승부): 0점
   - Bear가 우세: -10~15점
   - Bear가 치명적 약점 지적 (적자, 고PER, 악재): -20~30점

2. **펀더멘털 가감점**:
   - PER<10, PBR<1 (저평가): +10점
   - PER>30 (고평가): -10점

3. **뉴스 가감점**:
   - 확실한 호재: +10점
   - 악재: -15점

## 등급
- S(80+): 강력매수
- A(70-79): 매수추천
- B(60-69): 관심
- C(50-59): 중립
- D(40-49): 주의
- F(<40): 회피

JSON 응답: {{"score": 숫자, "grade": "등급", "reason": "판결 이유"}}

**중요: 기본 50점에서 시작하여 토론 결과에 따라 가감하세요. Bull과 Bear가 팽팽하면 50~55점입니다.**"""
        
        try:
            # [v4.2] Dream Team Config
            # Claude: Sonnet (Reasoning)
            # Gemini: Pro (Default)
            # OpenAI: Mini
            model_name = None
            if provider.name == 'claude':
                model_name = getattr(provider, 'reasoning_model', None)
            elif provider.name == 'gemini':
                model_name = None # Default (Pro) 사용
                
            logger.info(f"--- [JennieBrain/Phase3-Judge] 최종 판결 ({provider.name}): {stock_info.get('name')} ---")
            
            result = provider.generate_json(
                prompt,
                ANALYSIS_RESPONSE_SCHEMA, # 기존 스키마 재사용 (score, grade, reason)
                temperature=0.1, # 판결은 냉정하게
                model_name=model_name
            )
            return result
        except Exception as e:
            logger.error(f"❌ [Judge] 판결 실패: {e}")
            return {'score': 0, 'grade': 'D', 'reason': f"판결 오류: {e}"}

    # =================================================================
    # [v1.0] Scout Hybrid Scoring - 정량 통계 컨텍스트 주입
    # =================================================================
    
    def get_jennies_analysis_score_v5(self, stock_info: dict, quant_context: str = None) -> dict:
        """
        [v1.0] Scout Hybrid Scoring - 정량 통계 컨텍스트가 포함된 Hunter 분석
        
        기존 get_jennies_analysis_score와 달리, QuantScorer의 정량 분석 결과를
        프롬프트에 포함하여 LLM이 데이터 기반 판단을 하도록 유도합니다.
        
        Args:
            stock_info: 종목 정보 딕셔너리
            quant_context: QuantScorer에서 생성한 정량 분석 컨텍스트 문자열
        
        Returns:
            {'score': int, 'grade': str, 'reason': str}
        """
        # [v4.2] Dream Team Config: Hunter Scoring (Gemini Flash)
        provider = None
        if self.provider_gemini:
            provider = self.provider_gemini
        elif hasattr(self, 'provider_claude') and self.provider_claude:
            provider = self.provider_claude
        elif self.provider_openai:
            provider = self.provider_openai

        if provider is None:
            logger.error("❌ [JennieBrain] LLM 모델이 초기화되지 않았습니다!")
            return {'score': 0, 'grade': 'D', 'reason': 'JennieBrain 초기화 실패'}
        
        try:
            prompt = build_hunter_prompt_v5(stock_info, quant_context)
            
            provider_name = provider.name.upper()
            logger.info(f"--- [JennieBrain/v5-Hunter] 통계기반 필터링 ({provider_name}): {stock_info.get('name')} ---")
            
            # Gemini Flash 강제 사용
            model_name = None
            if provider.name == 'gemini':
                model_name = provider.flash_model_name()
            
            result = provider.generate_json(
                prompt,
                ANALYSIS_RESPONSE_SCHEMA,
                temperature=0.2,  # 데이터 기반이므로 낮은 temperature
                model_name=model_name
            )
            
            logger.info(f"   ✅ v5 Hunter 완료: {stock_info.get('name')} - {result.get('score')}점")
            return result
            
        except Exception as e:
            logger.error(f"❌ [JennieBrain/v5-Hunter] 분석 오류: {e}", exc_info=True)
            return {'score': 0, 'grade': 'D', 'reason': f"분석 오류: {e}"}
    
    # [v5.1] _build_hunter_prompt_v5는 llm_prompts.py로 이동됨

    
    def run_judge_scoring_v5(self, stock_info: dict, debate_log: str, quant_context: str = None) -> dict:
        """
        [v1.0] Scout Hybrid Scoring - 정량 컨텍스트 포함 Judge 판결
        
        기존 run_judge_scoring에 정량 분석 결과를 추가하여
        더 균형 잡힌 최종 판결을 내립니다.
        
        Args:
            stock_info: 종목 정보
            debate_log: Bull vs Bear 토론 로그
            quant_context: QuantScorer의 정량 분석 컨텍스트
        
        Returns:
            {'score': int, 'grade': str, 'reason': str}
        """
        # [v4.2] Dream Team Config: Judge (Claude Sonnet)
        provider = None
        if hasattr(self, 'provider_claude') and self.provider_claude:
            provider = self.provider_claude
        elif self.provider_gemini:
            provider = self.provider_gemini
        elif self.provider_openai:
            provider = self.provider_openai
            
        if provider is None:
            return {'score': 0, 'grade': 'D', 'reason': 'Model Error'}
        
        name = stock_info.get('name', 'N/A')
        news_reason = stock_info.get('news_reason', 'N/A')
        per = stock_info.get('per', 'N/A')
        pbr = stock_info.get('pbr', 'N/A')
        
        # 정량 컨텍스트가 없으면 기존 방식으로 폴백
        if not quant_context:
            return self.run_judge_scoring(stock_info, debate_log)
        
        prompt = f"""당신은 주식 투자 최종 판결자입니다. 
**정량 분석 결과**와 **Bull vs Bear 토론**을 종합하여 최종 점수를 매기세요.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## [핵심] 정량 분석 결과 (반드시 참고!)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{quant_context}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## 종목 기본 정보
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

종목: {name}
PER: {per}, PBR: {pbr}

## 최신 뉴스/재료
{news_reason}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## Bull vs Bear 토론 로그
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{debate_log[:2000]}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## 최종 점수 계산 (하이브리드 방식)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**기본 점수 = 정량 점수 (위 분석 결과 참조)**

토론 결과에 따라 가감:
- Bull이 논리적 압승 + 데이터 뒷받침: +10~15점
- 팽팽한 토론: ±0점
- Bear가 치명적 약점 지적: -10~20점

## 등급
- S(80+): 정량+정성 모두 우수 → 강력매수
- A(70-79): 정량 우수 + 토론에서 Bull 우세 → 매수추천
- B(60-69): 정량 양호 + 토론 팽팽 → 관심
- C(50-59): 중립
- D(40-49): 정량 부족 또는 토론에서 Bear 우세 → 주의
- F(<40): 정량+정성 모두 부정적 → 회피

JSON 응답: {{"score": 숫자, "grade": "등급", "reason": "판결 이유"}}

⚠️ **중요**: 정량 점수와 조건부 승률을 무시하지 마세요. 이것은 과거 데이터 기반의 객관적 지표입니다."""

        try:
            logger.info(f"--- [JennieBrain/v5-Judge] 하이브리드 판결 ({provider.name}): {name} ---")
            
            result = provider.generate_json(
                prompt,
                ANALYSIS_RESPONSE_SCHEMA,
                temperature=0.1
            )
            
            logger.info(f"   ✅ v5 Judge 완료: {name} - {result.get('score')}점 ({result.get('grade')})")
            return result
            
        except Exception as e:
            logger.error(f"❌ [JennieBrain/v5-Judge] 판결 실패: {e}")
            return {'score': 0, 'grade': 'D', 'reason': f"판결 오류: {e}"}
    
    # -----------------------------------------------------------------
    # [v1.0] 정량 컨텍스트 기반 분석 (Claude Opus 4.5 피드백 반영)
    # -----------------------------------------------------------------
    def analyze_with_context(self, 
                             stock_code: str,
                             stock_name: str,
                             quant_context: str,
                             news_summary: str = "",
                             fundamentals: dict = None) -> dict:
        """
        [v1.0] HybridScorer용 정량 컨텍스트 포함 LLM 분석
        
        Claude Opus 4.5 피드백: "analyze_with_context 메서드가 기존 JennieBrain에 있는지 확인 필요"
        
        Args:
            stock_code: 종목 코드
            stock_name: 종목명
            quant_context: QuantScorer가 생성한 정량 분석 요약 (format_quant_score_for_prompt)
            news_summary: 최근 뉴스 요약 (선택)
            fundamentals: 펀더멘털 데이터 dict (선택)
        
        Returns:
            {'score': float, 'reason': str, 'grade': str}
        """
        # Claude Haiku 우선 (빠르고 프롬프트 준수 우수)
        provider = self.provider_claude if hasattr(self, 'provider_claude') and self.provider_claude else \
                   (self.provider_openai if self.provider_openai else self.provider_gemini)
        
        if provider is None:
            logger.error("❌ [JennieBrain/v1.0] LLM 모델이 초기화되지 않았습니다!")
            return {'score': 50, 'grade': 'C', 'reason': 'JennieBrain 초기화 실패'}
        
        # 펀더멘털 정보 포맷팅
        fundamentals_str = ""
        if fundamentals:
            fundamentals_str = f"""
[펀더멘털 정보]
- PER: {fundamentals.get('per', 'N/A')}
- PBR: {fundamentals.get('pbr', 'N/A')}
- ROE: {fundamentals.get('roe', 'N/A')}%
- 시가총액: {fundamentals.get('market_cap', 'N/A')}
"""
        
        prompt = f"""당신은 한국 주식 투자 전문가입니다.
아래의 정량 분석 결과와 뉴스/펀더멘털 정보를 종합하여 매수 적합도 점수(0~100)를 산출하세요.

{quant_context}

{fundamentals_str}

[최근 뉴스 요약]
{news_summary if news_summary else '최근 뉴스 없음'}

## 판단 기준

⚠️ **중요**: 위 정량 분석 결과의 승률과 조건부 통계는 과거 데이터 기반의 객관적 지표입니다.
이 통계를 무시하지 말고 반드시 판단의 핵심 근거로 활용하세요.

1. **정량 점수 참조** (60점 만점 중 정량이 차지하는 비중)
   - 정량 점수 70점 이상: 기본적으로 긍정적
   - 정량 점수 50점 미만: 신중한 접근 필요

2. **조건부 승률 참조**
   - 승률 70% 이상: 강력한 매수 신호
   - 승률 50-70%: 보통
   - 승률 50% 미만: 약세 신호
   - 표본 수 30개 미만: 통계 신뢰도 낮음, 보수적 판단

3. **뉴스 맥락 분석**
   - 정량이 좋아도 치명적 악재(횡령, 분식회계)가 있으면 감점
   
   ⚠️ **역신호 경고 (v1.0 팩터 분석 결과)**:
   - 뉴스 호재 전체 승률: 47.3% (동전 던지기보다 낮음!)
   - 수주 뉴스 승률: 43.7% (역신호! 반대로 하면 56.3%)
   - 배당 뉴스 승률: 37.6% (강한 역신호! 반대로 하면 62.4%)
   - **"뉴스 보고 매수하면 고점에 물린다"** - 이미 가격에 반영됨
   - 호재 뉴스가 있어도 추격매수 금지, 보수적 판단 권장

## 점수 구간
- A(80-100): 정량+정성 모두 우수 → 강력 매수
- B(65-79): 정량 좋고 정성 무난 → 매수 추천
- C(50-64): 중립
- D(40-49): 정량 부족 또는 악재 → 주의
- F(<40): 정량+정성 모두 부정적 → 회피

JSON 응답: {{"score": 숫자, "grade": "등급", "reason": "판단 이유 (2-3문장)"}}"""

        try:
            logger.info(f"--- [JennieBrain/v1.0] 정량 컨텍스트 분석 ({provider.name}): {stock_name} ---")
            
            result = provider.generate_json(
                prompt,
                ANALYSIS_RESPONSE_SCHEMA,
                temperature=0.2
            )
            
            logger.info(f"   ✅ v1.0 분석 완료: {stock_name} - {result.get('score')}점 ({result.get('grade')})")
            return result
            
        except Exception as e:
            logger.error(f"❌ [JennieBrain/v1.0] 분석 실패: {e}")
            return {'score': 50, 'grade': 'C', 'reason': f"분석 오류: {e}"}

    # =================================================================
    # [v1.0] 경쟁사 수혜 분석 (Competitor Benefit Analysis)
    # Claude, Gemini, GPT 3자 합의 기반 설계
    # =================================================================
    
    def analyze_competitor_benefit(self, 
                                    target_stock_code: str,
                                    target_stock_name: str,
                                    sector: str,
                                    recent_news: str) -> dict:
        """
        [v1.0] 경쟁사 악재로 인한 반사이익 분석
        
        예: 쿠팡 개인정보 유출 → 네이버/컬리 수혜 분석
        
        Args:
            target_stock_code: 분석 대상 종목 코드
            target_stock_name: 분석 대상 종목명
            sector: 섹터 코드 (ECOMMERCE, SEMICONDUCTOR 등)
            recent_news: 최근 뉴스 요약 (경쟁사 뉴스 포함)
        
        Returns:
            {
                'competitor_events': [{'company': str, 'event_type': str, ...}],
                'total_benefit_score': int,
                'analysis_reason': str
            }
        """
        try:
            from prompts.competitor_benefit_prompt import (
                build_competitor_event_detection_prompt,
                COMPETITOR_GROUPS,
                EVENT_IMPACT_RULES
            )
        except ImportError:
            logger.warning("⚠️ [JennieBrain/v1.0] competitor_benefit_prompt 모듈 로드 실패")
            return {'competitor_events': [], 'total_benefit_score': 0, 'analysis_reason': '모듈 로드 실패'}
        
        # Claude Haiku 우선 (빠르고 프롬프트 준수 우수)
        provider = self.provider_claude if hasattr(self, 'provider_claude') and self.provider_claude else \
                   (self.provider_openai if self.provider_openai else self.provider_gemini)
        
        if provider is None:
            logger.error("❌ [JennieBrain/v1.0] LLM 모델이 초기화되지 않았습니다!")
            return {'competitor_events': [], 'total_benefit_score': 0, 'analysis_reason': 'LLM 미초기화'}
        
        # 프롬프트 생성
        prompt = build_competitor_event_detection_prompt(
            target_stock_code=target_stock_code,
            target_stock_name=target_stock_name,
            sector=sector,
            recent_news=recent_news
        )
        
        # JSON 스키마 정의
        COMPETITOR_EVENT_SCHEMA = {
            "type": "object",
            "properties": {
                "competitor_events": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "company": {"type": "string"},
                            "event_type": {"type": "string"},
                            "summary": {"type": "string"},
                            "severity": {"type": "string"},
                            "benefit_score": {"type": "integer"}
                        }
                    }
                },
                "total_benefit_score": {"type": "integer"},
                "analysis_reason": {"type": "string"}
            },
            "required": ["competitor_events", "total_benefit_score", "analysis_reason"]
        }
        
        try:
            logger.info(f"--- [JennieBrain/v1.0] 경쟁사 수혜 분석 ({provider.name}): {target_stock_name} ---")
            
            result = provider.generate_json(
                prompt,
                COMPETITOR_EVENT_SCHEMA,
                temperature=0.2
            )
            
            # 결과 로깅
            events = result.get('competitor_events', [])
            total_benefit = result.get('total_benefit_score', 0)
            
            if events:
                logger.info(f"   🎯 경쟁사 악재 감지: {len(events)}건")
                for event in events:
                    logger.info(f"      - {event.get('company')}: {event.get('event_type')} (+{event.get('benefit_score', 0)}점)")
                logger.info(f"   📊 총 수혜 점수: +{total_benefit}점")
            else:
                logger.info(f"   ℹ️ 경쟁사 악재 없음 (수혜 점수: 0)")
            
            return result
            
        except Exception as e:
            logger.error(f"❌ [JennieBrain/v1.0] 경쟁사 수혜 분석 실패: {e}")
            return {'competitor_events': [], 'total_benefit_score': 0, 'analysis_reason': f"분석 오류: {e}"}
    
    def get_beneficiary_recommendations(self,
                                         event_company: str,
                                         event_type: str,
                                         event_summary: str,
                                         sector: str) -> dict:
        """
        [v1.0] 악재 발생 시 수혜 종목 추천
        
        Args:
            event_company: 악재 발생 기업
            event_type: 악재 유형
            event_summary: 악재 요약
            sector: 섹터 코드
        
        Returns:
            {
                'beneficiaries': [{'stock_code': str, 'stock_name': str, 'benefit_score': int, ...}],
                'top_pick': str,
                'holding_period': str,
                'risk_note': str
            }
        """
        try:
            from prompts.competitor_benefit_prompt import build_beneficiary_recommendation_prompt
        except ImportError:
            logger.warning("⚠️ [JennieBrain/v1.0] competitor_benefit_prompt 모듈 로드 실패")
            return {'beneficiaries': [], 'top_pick': None, 'holding_period': 'N/A', 'risk_note': '모듈 로드 실패'}
        
        provider = self.provider_claude if hasattr(self, 'provider_claude') and self.provider_claude else \
                   (self.provider_openai if self.provider_openai else self.provider_gemini)
        
        if provider is None:
            return {'beneficiaries': [], 'top_pick': None, 'holding_period': 'N/A', 'risk_note': 'LLM 미초기화'}
        
        prompt = build_beneficiary_recommendation_prompt(
            event_company=event_company,
            event_type=event_type,
            event_summary=event_summary,
            sector=sector
        )
        
        BENEFICIARY_SCHEMA = {
            "type": "object",
            "properties": {
                "beneficiaries": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "stock_code": {"type": "string"},
                            "stock_name": {"type": "string"},
                            "benefit_score": {"type": "integer"},
                            "reason": {"type": "string"},
                            "strategy": {"type": "string"}
                        }
                    }
                },
                "top_pick": {"type": "string"},
                "holding_period": {"type": "string"},
                "risk_note": {"type": "string"}
            },
            "required": ["beneficiaries", "top_pick", "holding_period", "risk_note"]
        }
        
        try:
            logger.info(f"--- [JennieBrain/v1.0] 수혜 종목 추천: {event_company} {event_type} ---")
            
            result = provider.generate_json(
                prompt,
                BENEFICIARY_SCHEMA,
                temperature=0.3
            )
            
            # 결과 로깅
            beneficiaries = result.get('beneficiaries', [])
            top_pick = result.get('top_pick')
            
            if beneficiaries:
                logger.info(f"   🎯 수혜 종목 {len(beneficiaries)}개 추천")
                logger.info(f"   🏆 Top Pick: {top_pick}")
                logger.info(f"   📅 권장 보유: {result.get('holding_period')}")
            
            return result
            
        except Exception as e:
            logger.error(f"❌ [JennieBrain/v1.0] 수혜 종목 추천 실패: {e}")
            return {'beneficiaries': [], 'top_pick': None, 'holding_period': 'N/A', 'risk_note': f"분석 오류: {e}"}
    
    def _inject_competitor_benefit_context(self, base_prompt: str, competitor_benefit_score: int, competitor_reason: str) -> str:
        """
        [v1.0] 기존 프롬프트에 경쟁사 수혜 컨텍스트 주입
        
        Args:
            base_prompt: 기존 분석 프롬프트
            competitor_benefit_score: 경쟁사 수혜 점수
            competitor_reason: 경쟁사 수혜 사유
        
        Returns:
            경쟁사 수혜 컨텍스트가 추가된 프롬프트
        """
        if competitor_benefit_score <= 0:
            return base_prompt
        
        competitor_context = f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## [추가 가산점] 경쟁사 악재로 인한 반사이익
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🎯 **경쟁사 수혜 가산점: +{competitor_benefit_score}점**

📋 사유: {competitor_reason}

⚠️ 이 가산점은 경쟁사의 고유 악재로 인한 반사이익입니다.
   기존 점수에 추가로 반영하세요.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
        
        # 프롬프트 시작 부분에 컨텍스트 추가
        return competitor_context + "\n" + base_prompt

