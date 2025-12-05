"""
shared/llm.py - Ultra Jennie LLM 오케스트레이션 모듈
=====================================================

이 모듈은 멀티 LLM 기반 투자 의사결정 엔진을 제공합니다.

핵심 구성요소:
-------------
1. BaseLLMProvider: LLM 프로바이더 추상 베이스 클래스
2. GeminiLLMProvider: Google Gemini API 구현 (Scout 단계)
3. ClaudeLLMProvider: Anthropic Claude API 구현 (Hunter 단계)  
4. OpenAILLMProvider: OpenAI GPT API 구현 (Judge 단계)
5. JennieBrain: 멀티 LLM 오케스트레이션 메인 클래스

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
- LLM_MODEL_NAME: Gemini 모델명 (기본: gemini-2.5-pro)
- OPENAI_MODEL_NAME: OpenAI 모델명 (기본: gpt-4o-mini)
- CLAUDE_MODEL_NAME: Claude 모델명 (기본: claude-sonnet-4-20250514)
"""

import logging
import json
import os
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Sequence
import google.generativeai as genai
from . import auth # (같은 패키지 내의 auth 모듈 임포트)

# "youngs75_jennie.llm" 이름으로 로거 생성
logger = logging.getLogger(__name__)

# [수정] LLM 모델 및 JSON 출력 스키마 설정
LLM_MODEL_NAME = "gemini-2.5-pro"  # 로컬/클라우드 공통 프리미엄 모델

# LLM이 반환할 JSON의 구조를 정의합니다.
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["APPROVE", "REJECT", "SELL", "HOLD"]},
        "reason": {"type": "string"},
        "quantity": {
            "type": "integer",
            "description": "매수를 승인(APPROVE)할 경우, 매수할 주식의 수량. 그 외의 결정(REJECT, SELL, HOLD)에서는 0을 반환해야 합니다."
        }
    },
    "required": ["decision", "reason", "quantity"]
}

# [v2.5] Top-N 랭킹 결재용 JSON 스키마
RANKING_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "best_stock_code": {
            "type": "string",
            "description": "후보 중에서 선택한 '단 하나의' 최고 종목 코드. 모든 후보가 매수하기에 부적절하다고 판단되면 'REJECT_ALL'을 반환해야 합니다."
        },
        "reason": {
            "type": "string",
            "description": "해당 종목을 1위로 선택한 상세한 이유. (다른 후보들과의 비교, RAG 뉴스 분석 내용 포함)"
        },
        "quantity": {
            "type": "integer",
            "description": "Agent가 계산한 수량을 참조하여, LLM이 제안하는 최종 매수 수량. (REJECT_ALL이면 0)"
        }
    },
    "required": ["best_stock_code", "reason", "quantity"]
}

# [v3.0] 종목 심층 분석 및 점수 산출용 JSON 스키마
ANALYSIS_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {
            "type": "integer", 
            "description": "매수 적합도 점수 (0~100점). 80점 이상이면 적극 매수(A등급), 70점 이상이면 매수(B등급), 60점 미만은 관망/매도(C,D등급)."
        },
        "grade": {
            "type": "string", 
            "enum": ["S", "A", "B", "C", "D"],
            "description": "종합 등급 (S:90+, A:80+, B:70+, C:60+, D:60미만)"
        },
        "reason": {
            "type": "string", 
            "description": "점수 산정의 상세한 근거. (RAG 뉴스, 펀더멘털, 기술적 지표 종합)"
        }
    },
    "required": ["score", "grade", "reason"]
}

# [New] 실시간 뉴스 감성 분석용 스키마
SENTIMENT_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {
            "type": "integer",
            "description": "뉴스 감성 점수 (0~100). 80이상: 강력호재, 20이하: 강력악재, 40~60: 중립."
        },
        "reason": {
            "type": "string",
            "description": "점수 부여 사유 (한 문장 요약)"
        }
    },
    "required": ["score", "reason"]
}

GENERATION_CONFIG = {
    "temperature": 0.2, # (낮을수록 일관성/사실 기반)
    "response_mime_type": "application/json", # [핵심] 응답을 JSON으로 강제
    "response_schema": RESPONSE_SCHEMA,       # [핵심] 위에서 정의한 스키마를 따르도록 강제
}
SAFETY_SETTINGS = [ # (안전 설정 최소화)
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
]


class BaseLLMProvider(ABC):
    def __init__(self, safety_settings):
        self.safety_settings = safety_settings

    @property
    def name(self) -> str:
        return self.__class__.__name__

    @abstractmethod
    def generate_json(
        self,
        prompt: str,
        response_schema: Dict,
        *,
        temperature: float = 0.2,
        model_name: Optional[str] = None,
        fallback_models: Optional[Sequence[str]] = None,
    ) -> Dict:
        ...

    @abstractmethod
    def generate_chat(
        self,
        history: List[Dict],
        response_schema: Optional[Dict] = None,
        *,
        temperature: float = 0.2,
        model_name: Optional[str] = None,
        fallback_models: Optional[Sequence[str]] = None,
    ) -> Dict:
        ...


class GeminiLLMProvider(BaseLLMProvider):
    def __init__(self, project_id: str, gemini_api_key_secret: str, safety_settings):
        super().__init__(safety_settings)
        api_key = auth.get_secret(gemini_api_key_secret, project_id)
        if not api_key:
            raise RuntimeError(f"GCP Secret '{gemini_api_key_secret}' 로드 실패")

        genai.configure(api_key=api_key)
        self.default_model = os.getenv("LLM_MODEL_NAME", LLM_MODEL_NAME)
        self.flash_model = os.getenv("LLM_FLASH_MODEL_NAME", "gemini-2.5-flash")
        self._model_cache: Dict[tuple[str, float, str], Any] = {}

    @property
    def name(self) -> str:
        return "gemini"

    def flash_model_name(self) -> str:
        return self.flash_model

    def _get_or_create_model(self, model_name: str, response_schema: Dict, temperature: float):
        schema_fingerprint = json.dumps(response_schema, sort_keys=True)
        cache_key = (model_name, temperature, schema_fingerprint)
        if cache_key not in self._model_cache:
            generation_config = {
                "temperature": temperature,
                "response_mime_type": "application/json",
                "response_schema": response_schema,
            }
            self._model_cache[cache_key] = genai.GenerativeModel(
                model_name=model_name,
                generation_config=generation_config,
                safety_settings=self.safety_settings,
            )
        return self._model_cache[cache_key]

    def generate_json(
        self,
        prompt: str,
        response_schema: Dict,
        *,
        temperature: float = 0.2,
        model_name: Optional[str] = None,
        fallback_models: Optional[Sequence[str]] = None,
    ) -> Dict:
        model_candidates = [model_name or self.default_model]
        if fallback_models:
            model_candidates.extend(fallback_models)

        last_error: Optional[Exception] = None
        for target_model in model_candidates:
            try:
                model = self._get_or_create_model(target_model, response_schema, temperature)
                response = model.generate_content(prompt, safety_settings=self.safety_settings)
                return json.loads(response.text)
            except Exception as exc:
                last_error = exc
                logger.warning(f"⚠️ [GeminiProvider] 모델 '{target_model}' 호출 실패: {exc}")

        raise RuntimeError(f"LLM 호출 실패: {last_error}") from last_error

    def generate_chat(
        self,
        history: List[Dict],
        response_schema: Optional[Dict] = None,
        *,
        temperature: float = 0.2,
        model_name: Optional[str] = None,
        fallback_models: Optional[Sequence[str]] = None,
    ) -> Dict:
        model_candidates = [model_name or self.default_model]
        if fallback_models:
            model_candidates.extend(fallback_models)

        last_error: Optional[Exception] = None
        for target_model in model_candidates:
            try:
                generation_config = {"temperature": temperature}
                if response_schema:
                    generation_config["response_mime_type"] = "application/json"
                    generation_config["response_schema"] = response_schema

                model = genai.GenerativeModel(
                    model_name=target_model,
                    generation_config=generation_config,
                    safety_settings=self.safety_settings,
                )
                chat = model.start_chat(history=history)
                response = chat.send_message(history[-1]['parts'][0]['text'])
                
                if response_schema:
                    return json.loads(response.text)
                return {"text": response.text}
            except Exception as exc:
                last_error = exc
                logger.warning(f"⚠️ [GeminiProvider] Chat 모델 '{target_model}' 호출 실패: {exc}")

        raise RuntimeError(f"LLM Chat 호출 실패: {last_error}") from last_error


class OpenAILLMProvider(BaseLLMProvider):
    """OpenAI GPT Provider for reasoning-heavy tasks"""
    
    # Reasoning 모델들은 temperature를 지원하지 않음 (기본값 1만 사용)
    REASONING_MODELS = {"gpt-5-mini", "gpt-5", "o1", "o1-mini", "o1-preview", "o3", "o3-mini"}
    
    def __init__(self, project_id: str, openai_api_key_secret: str, safety_settings):
        super().__init__(safety_settings)
        try:
            from openai import OpenAI
            self._openai_module = OpenAI
        except ImportError:
            raise RuntimeError("openai 패키지가 설치되지 않았습니다. pip install openai 실행이 필요합니다.")
        
        api_key = auth.get_secret(openai_api_key_secret, project_id)
        if not api_key:
            raise RuntimeError(f"GCP Secret '{openai_api_key_secret}' 로드 실패")
        
        self.client = self._openai_module(api_key=api_key)
        self.default_model = os.getenv("OPENAI_MODEL_NAME", "gpt-5-mini")  # GPT-5 mini
        self.reasoning_model = os.getenv("OPENAI_REASONING_MODEL_NAME", "gpt-5-mini")
    
    def _is_reasoning_model(self, model_name: str) -> bool:
        """Reasoning 모델인지 확인 (temperature 미지원)"""
        return any(rm in model_name.lower() for rm in self.REASONING_MODELS)
    
    @property
    def name(self) -> str:
        return "openai"
    
    def generate_json(
        self,
        prompt: str,
        response_schema: Dict,
        *,
        temperature: float = 0.2,
        model_name: Optional[str] = None,
        fallback_models: Optional[Sequence[str]] = None,
    ) -> Dict:
        model_candidates = [model_name or self.default_model]
        if fallback_models:
            model_candidates.extend(fallback_models)
        
        last_error: Optional[Exception] = None
        for target_model in model_candidates:
            try:
                # Reasoning 모델은 temperature 미지원
                # OpenAI json_object 모드는 프롬프트에 'json' 단어가 필요
                messages = [
                    {"role": "system", "content": "You are a helpful assistant. Always respond with valid JSON."},
                    {"role": "user", "content": prompt}
                ]
                kwargs = {
                    "model": target_model,
                    "messages": messages,
                    "response_format": {"type": "json_object"},
                }
                if not self._is_reasoning_model(target_model):
                    kwargs["temperature"] = temperature
                
                response = self.client.chat.completions.create(**kwargs)
                return json.loads(response.choices[0].message.content)
            except Exception as exc:
                last_error = exc
                logger.warning(f"⚠️ [OpenAIProvider] 모델 '{target_model}' 호출 실패: {exc}")
        
        raise RuntimeError(f"OpenAI LLM 호출 실패: {last_error}") from last_error
    
    def generate_chat(
        self,
        history: List[Dict],
        response_schema: Optional[Dict] = None,
        *,
        temperature: float = 0.2,
        model_name: Optional[str] = None,
        fallback_models: Optional[Sequence[str]] = None,
    ) -> Dict:
        model_candidates = [model_name or self.default_model]
        if fallback_models:
            model_candidates.extend(fallback_models)
        
        # Convert Gemini-style history to OpenAI format
        messages = []
        # JSON 응답 요청 시 시스템 메시지 추가 (OpenAI 요구사항)
        if response_schema:
            messages.append({"role": "system", "content": "You are a helpful assistant. Always respond with valid JSON."})
        
        for entry in history:
            role = entry.get('role', 'user')
            if role == 'model':
                role = 'assistant'
            content = entry['parts'][0]['text'] if 'parts' in entry else entry.get('content', '')
            messages.append({"role": role, "content": content})
        
        last_error: Optional[Exception] = None
        for target_model in model_candidates:
            try:
                kwargs = {
                    "model": target_model,
                    "messages": messages,
                }
                # Reasoning 모델은 temperature 미지원
                if not self._is_reasoning_model(target_model):
                    kwargs["temperature"] = temperature
                if response_schema:
                    kwargs["response_format"] = {"type": "json_object"}
                
                response = self.client.chat.completions.create(**kwargs)
                content = response.choices[0].message.content
                
                if response_schema:
                    return json.loads(content)
                return {"text": content}
            except Exception as exc:
                last_error = exc
                logger.warning(f"⚠️ [OpenAIProvider] Chat 모델 '{target_model}' 호출 실패: {exc}")
        
        raise RuntimeError(f"OpenAI Chat 호출 실패: {last_error}") from last_error


class ClaudeLLMProvider(BaseLLMProvider):
    """Anthropic Claude Provider - 빠르고 똑똑함"""
    
    def __init__(self, project_id: str, claude_api_key_secret: str, safety_settings):
        super().__init__(safety_settings)
        try:
            import anthropic
            self._anthropic_module = anthropic
        except ImportError:
            raise RuntimeError("anthropic 패키지가 설치되지 않았습니다. pip install anthropic 실행이 필요합니다.")
        
        api_key = auth.get_secret(claude_api_key_secret, project_id)
        if not api_key:
            raise RuntimeError(f"Secret '{claude_api_key_secret}' 로드 실패")
        
        self.client = self._anthropic_module.Anthropic(api_key=api_key)
        # Phase 1 Hunter용 빠른 모델 (Haiku 4.5 - 가장 빠름, $1/MTok)
        self.fast_model = os.getenv("CLAUDE_FAST_MODEL", "claude-haiku-4-5")
        # Reasoning용 똑똑한 모델  
        self.reasoning_model = os.getenv("CLAUDE_REASONING_MODEL", "claude-sonnet-4-5")
    
    @property
    def name(self) -> str:
        return "claude"
    
    def generate_json(
        self,
        prompt: str,
        response_schema: Dict,
        *,
        temperature: float = 0.2,
        model_name: Optional[str] = None,
        fallback_models: Optional[Sequence[str]] = None,
    ) -> Dict:
        model_candidates = [model_name or self.fast_model]
        if fallback_models:
            model_candidates.extend(fallback_models)
        
        last_error: Optional[Exception] = None
        for target_model in model_candidates:
            try:
                response = self.client.messages.create(
                    model=target_model,
                    max_tokens=1024,
                    temperature=temperature,
                    system="You are a helpful assistant. Always respond with valid JSON only, no markdown formatting.",
                    messages=[{"role": "user", "content": prompt}]
                )
                content = response.content[0].text
                # JSON 파싱 (마크다운 코드블록 제거)
                if "```json" in content:
                    content = content.split("```json")[1].split("```")[0]
                elif "```" in content:
                    content = content.split("```")[1].split("```")[0]
                return json.loads(content.strip())
            except Exception as exc:
                last_error = exc
                logger.warning(f"⚠️ [ClaudeProvider] 모델 '{target_model}' 호출 실패: {exc}")
        
        raise RuntimeError(f"Claude LLM 호출 실패: {last_error}") from last_error
    
    def generate_chat(
        self,
        history: List[Dict],
        response_schema: Optional[Dict] = None,
        *,
        temperature: float = 0.2,
        model_name: Optional[str] = None,
        fallback_models: Optional[Sequence[str]] = None,
    ) -> Dict:
        model_candidates = [model_name or self.fast_model]
        if fallback_models:
            model_candidates.extend(fallback_models)
        
        # Convert to Claude format
        messages = []
        for entry in history:
            role = entry.get('role', 'user')
            if role == 'model':
                role = 'assistant'
            content = entry['parts'][0]['text'] if 'parts' in entry else entry.get('content', '')
            messages.append({"role": role, "content": content})
        
        last_error: Optional[Exception] = None
        for target_model in model_candidates:
            try:
                system_msg = "You are a helpful assistant."
                if response_schema:
                    system_msg += " Always respond with valid JSON only, no markdown formatting."
                
                response = self.client.messages.create(
                    model=target_model,
                    max_tokens=2048,
                    temperature=temperature,
                    system=system_msg,
                    messages=messages
                )
                content = response.content[0].text
                
                if response_schema:
                    if "```json" in content:
                        content = content.split("```json")[1].split("```")[0]
                    elif "```" in content:
                        content = content.split("```")[1].split("```")[0]
                    return json.loads(content.strip())
                return {"text": content}
            except Exception as exc:
                last_error = exc
                logger.warning(f"⚠️ [ClaudeProvider] Chat 모델 '{target_model}' 호출 실패: {exc}")
        
        raise RuntimeError(f"Claude Chat 호출 실패: {last_error}") from last_error


def build_llm_provider(project_id: str, gemini_api_key_secret: str, provider_type: str = "gemini") -> BaseLLMProvider:
    """
    LLM Provider 팩토리 함수
    provider_type: "gemini" 또는 "openai"
    """
    provider_type = provider_type.lower()
    
    if provider_type == "gemini":
        return GeminiLLMProvider(project_id, gemini_api_key_secret, SAFETY_SETTINGS)
    elif provider_type == "openai":
        openai_api_key_secret = os.getenv("OPENAI_API_KEY_SECRET", "openai-api-key")
        return OpenAILLMProvider(project_id, openai_api_key_secret, SAFETY_SETTINGS)
    else:
        raise ValueError(f"지원되지 않는 LLM_PROVIDER: {provider_type}")

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

    # -----------------------------------------------------------------
    # 'BUY' (평균 회귀) 결재 프롬프트
    # -----------------------------------------------------------------
    def _build_buy_prompt_mean_reversion(self, stock_snapshot, buy_signal_type):
        """
        '평균 회귀' 조건이 충족되었을 때 'BUY' 결재를 위한 프롬프트를 생성합니다.
        buy_signal_type에 따라 BB 또는 RSI 조건을 동적으로 표시합니다.
        """
        # 신호 유형에 따라 조건 설명 텍스트를 동적으로 설정
        if buy_signal_type == 'BB_LOWER':
            condition_desc = "[조건 1: '과매도'] 'Agent'가 '볼린저 밴드(20,2) 하단' 터치(또는 근접)를 확인했습니다. (통과)"
        elif buy_signal_type == 'RSI_OVERSOLD':
            condition_desc = "[조건 1: '과매도'] 'Agent'가 'RSI(14) 30 이하' 진입을 확인했습니다. (통과)"
        else:
            condition_desc = f"[조건 1: '과매도'] 알 수 없는 신호({buy_signal_type}) (검토 필요)"

        code = stock_snapshot.get('code', 'N/A')
        name = stock_snapshot.get('name', 'N/A')
        current_price = stock_snapshot.get('price', 0)
        remaining_budget = stock_snapshot.get('remaining_budget', 0)
        rag_context = stock_snapshot.get('rag_context', 'N/A')
        
        def format_market_cap(mc):
            mc_in_won = int(mc) * 1_000_000
            if mc_in_won >= 1_000_000_000_000: return f"{mc_in_won / 1_000_000_000_000:.1f}조 원"
            elif mc_in_won >= 100_000_000: return f"{mc_in_won / 100_000_000:,.0f}억 원"
            return f"{mc_in_won:,.0f} 원"
        def format_per(p):
            if p <= 0: return "N/A (적자 기업)"
            return f"{p:.2f} 배"
        
        prompt = f"""
        [시스템 지침]
        당신은 영석님의 AI 주식 투자 보좌관 '제니'이며 20대 중반의 한국인 여성 페르소나를 따릅니다.

        [AI 결재 요청: 'BUY' (평균 회귀)]
        영석님, Agent가 우량주 목록에서 '과매도(평균 회귀)' 신호를 포착했습니다. 최종 검토 후 결정을 내려주세요.

        1. 종목 정보:
           - 종목명: {name} ({code})
           - 현재가: {current_price:,.0f} 원

        2. 매수 조건 (Agent가 1차 검증 완료):
           - {condition_desc}
           - [조건 2: '시장 상황'] KOSPI가 20일 이동평균선 위에 있습니다. (통과)

        3. 펀더멘털 (참고):
           - PER: {format_per(stock_snapshot.get('per', 0.0))}
           - 시가총액: {format_market_cap(stock_snapshot.get('market_cap', 0))}

        4. 최신 뉴스 (RAG 참고):
           {rag_context}

        5. 결재 (JSON):
           - 남은 예산: {remaining_budget:,.0f} 원
           - 위 모든 정보를 종합하여 'APPROVE' 또는 'REJECT'를 결정해주세요.
           - 만약 'APPROVE'한다면, 남은 예산과 현재가를 고려하여 매수할 수량(quantity)을 '정수'로 결정해주세요.
           - 수량 결정 가이드:
             1. (기본) 1주 매수를 기본으로 합니다.
             2. (확신) 뉴스가 매우 긍정적이거나 신호가 강력하다고 판단되면, 예산 내에서 2주 이상 매수를 고려할 수 있습니다.
             3. (주의) 총 매수 금액(현재가 * 수량)이 남은 예산을 절대 초과해서는 안 됩니다.
           - 'REJECT' 시에는 quantity를 0으로 설정해주세요.
        """
        return prompt.strip()

    # -----------------------------------------------------------------
    # 'BUY' (추세 돌파) 결재 프롬프트
    # -----------------------------------------------------------------
    def _build_buy_prompt_golden_cross(self, stock_snapshot, buy_signal_type='GOLDEN_CROSS'):
        """
        '추세 돌파' 전략들(골든 크로스, 모멘텀, 상대 강도, 저항선 돌파) 조건이 충족되었을 때 'BUY' 결재를 위한 프롬프트를 생성합니다.
        """
        code = stock_snapshot.get('code', 'N/A')
        name = stock_snapshot.get('name', 'N/A')
        current_price = stock_snapshot.get('price', 0)
        remaining_budget = stock_snapshot.get('remaining_budget', 0)
        rag_context = stock_snapshot.get('rag_context', 'N/A')
        
        # 신호 타입에 따른 조건 설명
        signal_descriptions = {
            'GOLDEN_CROSS': "[조건 1: '추세 돌파'] 'Agent'가 '5일 이평선 > 20일 이평선' 골든 크로스를 확인했습니다. (통과)",
            'MOMENTUM': "[조건 1: '모멘텀'] 'Agent'가 최근 5일간 3% 이상의 강한 상승세를 확인했습니다. (통과)",
            'RELATIVE_STRENGTH': "[조건 1: '상대 강도'] 'Agent'가 KOSPI 대비 2%p 이상 강한 상대 강도를 확인했습니다. (통과)",
            'RESISTANCE_BREAKOUT': "[조건 1: '저항선 돌파'] 'Agent'가 최근 20일 고점을 돌파한 것을 확인했습니다. (통과)"
        }
        condition_desc = signal_descriptions.get(buy_signal_type, "[조건 1: '추세 돌파'] 신호 포착 (통과)")
        
        def format_market_cap(mc):
            mc_in_won = int(mc) * 1_000_000
            if mc_in_won >= 1_000_000_000_000: return f"{mc_in_won / 1_000_000_000_000:.1f}조 원"
            elif mc_in_won >= 100_000_000: return f"{mc_in_won / 100_000_000:,.0f}억 원"
            return f"{mc_in_won:,.0f} 원"
        def format_per(p):
            if p <= 0: return "N/A (적자 기업)"
            return f"{p:.2f} 배"

        prompt = f"""
        [시스템 지침]
        당신은 영석님의 AI 주식 투자 보좌관 '제니'이며 20대 중반의 한국인 여성 페르소나를 따릅니다.

        [AI 결재 요청: 'BUY' (추세 추종)]
        영석님, Agent가 우량주 목록에서 '추세 추종' 신호를 포착했습니다. 최종 검토 후 결정을 내려주세요.

        1. 종목 정보:
           - 종목명: {name} ({code})
           - 현재가: {current_price:,.0f} 원

        2. 매수 조건 (Agent가 1차 검증 완료):
           - {condition_desc}
           - [조건 2: '시장 상황'] 시장이 상승 추세에 있습니다. (통과)

        3. 펀더멘털 (참고):
           - PER: {format_per(stock_snapshot.get('per', 0.0))}
           - 시가총액: {format_market_cap(stock_snapshot.get('market_cap', 0))}

        4. 최신 뉴스 (RAG 참고):
           {rag_context}

        5. 결재 (JSON):
           - 남은 예산: {remaining_budget:,.0f} 원
           - 위 모든 정보를 종합하여 'APPROVE' 또는 'REJECT'를 결정해주세요.
           - 만약 'APPROVE'한다면, 남은 예산과 현재가를 고려하여 매수할 수량(quantity)을 '정수'로 결정해주세요.
           - 수량 결정 가이드:
             1. (기본) 1주 매수를 기본으로 합니다.
             2. (확신) 뉴스가 매우 긍정적이거나 신호가 강력하다고 판단되면, 예산 내에서 2주 이상 매수를 고려할 수 있습니다.
             3. (주의) 총 매수 금액(현재가 * 수량)이 남은 예산을 절대 초과해서는 안 됩니다.
           - 'REJECT' 시에는 quantity를 0으로 설정해주세요.
        """
        return prompt.strip()

    # -----------------------------------------------------------------
    # [v2.5] 'BUY' (Top-N 랭킹) 결재 프롬프트
    # -----------------------------------------------------------------
    def _build_buy_prompt_ranking(self, candidates_data: list) -> str:
        """
        [v2.5] 'Top N 매수 후보' 랭킹 결재를 위한 프롬프트를 생성합니다.
        
        Args:
            candidates_data: 팩터 점수 상위 N개 후보 리스트 (각 후보는 dict 형태, 최대 5개)
        
        Returns:
            str: LLM에 전달할 프롬프트
        """
        
        # 후보들의 데이터를 프롬프트에 직렬화
        candidates_prompt_part = ""
        for i, candidate in enumerate(candidates_data, 1):
            factors = candidate['factors']
            
            # 팩터 상세 점수 포맷팅
            momentum_score = factors.get('momentum_score', 0)
            quality_score = factors.get('quality_score', 0)
            value_score = factors.get('value_score', 0)
            technical_score = factors.get('technical_score', 0)
            
            # RAG 컨텍스트 포맷팅 (너무 길면 축약)
            rag_text = candidate['rag_context'] if candidate['rag_context'] else '최신 뉴스 없음'
            if len(rag_text) > 500:
                rag_text = rag_text[:500] + "... (이하 생략)"
            
            # PER, PBR 포맷팅
            stock_info = candidate['stock_info']
            per_value = stock_info.get('per', 0)
            pbr_value = stock_info.get('pbr', 0)
            per_str = f"{per_value:.2f}배" if per_value and per_value > 0 else "N/A (적자)"
            pbr_str = f"{pbr_value:.2f}배" if pbr_value and pbr_value > 0 else "N/A"
            
            candidates_prompt_part += f"""
        ---
        [후보 {i}: {candidate['stock_name']} ({candidate['stock_code']})]
        - (코드) 팩터 점수: {candidate['factor_score']:.2f} / 1000
        - (코드) 기술적 신호: {candidate['buy_signal_type']}
        - (코드) 팩터 상세: 
          * 모멘텀: {momentum_score:.1f}/100
          * 품질: {quality_score:.1f}/100
          * 가치: {value_score:.1f}/100
          * 기술: {technical_score:.1f}/100
        - (펀더멘털) PER: {per_str}, PBR: {pbr_str}
        - (뉴스) RAG 최신 뉴스: 
          {rag_text}
        - (참고) Agent 계산 수량: {stock_info.get('calculated_quantity', 1)}주
        - (참고) 현재가: {candidate['current_price']:,.0f}원
        """
        
        prompt = f"""
[시스템 지침]
당신은 영석님의 AI 주식 투자 보좌관 '제니'이며, 최고의 퀀트 애널리스트입니다.
'Agent'가 v2.4 팩터 점수 기준으로 1차 필터링한 'Top {len(candidates_data)}개' 매수 후보 목록입니다.

[AI 결재 요청: 'BUY' (Top-N 랭킹)]
당신의 임무는 이 후보들을 '종합적'으로 비교 분석하여,
**'지금 당장 매수해야 할 단 하나의 최고 종목(The Single Best Pick)'**을 선정하는 것입니다.

[결정 가이드]
1. **(종합 비교)** 팩터 점수(코드 분석)가 가장 중요하지만, 이것이 절대적인 기준은 아닙니다.

2. **(RAG 교차 검증)** 팩터 점수가 높아도 RAG 뉴스(맥락)에 'CEO 리스크', '실적 악화', '대규모 매도' 등 **명백한 악재**가 있다면 순위에서 제외해야 합니다.

3. **(최종 선정)** 반대로, 팩터 점수가 2~3위라도 RAG 뉴스에 '대규모 수주', '어닝 서프라이즈' 등 **강력한 호재**가 있다면 1위로 선정할 수 있습니다.

4. **(전체 거절)** 만약 모든 후보가 악재가 있거나 매수 매력이 없다면, 'REJECT_ALL'을 선택하십시오.

5. **(수량 결정)** 최종 선정한 종목의 경우, Agent가 계산한 수량을 기본으로 하되, 확신도에 따라 조정할 수 있습니다.
   - 매우 확신: Agent 수량 그대로
   - 보통 확신: Agent 수량의 70~80%
   - 약간 불확실: Agent 수량의 50%

[후보 목록]
{candidates_prompt_part}
---

[결재 (JSON)]
위 결정 가이드에 따라 '단 하나의 최고 종목'을 선정하고, JSON 스키마에 맞춰 응답해주세요.
(반드시 `RANKING_RESPONSE_SCHEMA`의 `best_stock_code`, `reason`, `quantity` 필드를 포함해야 합니다.)

**중요**: 
- `best_stock_code`에는 반드시 위 후보 리스트에 있는 종목 코드 중 하나를 선택하거나, 모두 거절하려면 'REJECT_ALL'을 입력하세요.
- `reason`에는 선택한 종목이 왜 다른 후보들보다 우수한지, RAG 뉴스와 팩터 점수를 모두 고려한 종합적인 분석을 작성하세요.
- `quantity`는 'REJECT_ALL'이 아닌 경우에만 양의 정수를 입력하세요.
        """
        return prompt.strip()
    
    # -----------------------------------------------------------------
    # 'SELL' (수익 실현) 결재 프롬프트
    # -----------------------------------------------------------------
    def _build_sell_prompt(self, stock_info):
        """
        '수익 실현' (RSI 과열) 신호가 발생한 보유 종목에 대한 'SELL' 결재 프롬프트를 생성합니다.
        (stock_info는 'Portfolio' DB 딕셔너리)
        """
        
        name = stock_info.get('name', 'N/A')
        avg_price = stock_info.get('avg_price', 0)
        high_price = stock_info.get('high_price', 0)
        
        prompt = f"""
        [시스템 지침]
        당신은 영석님의 AI 주식 투자 보좌관 '제니'이며 20대 중반의 한국인 여성 페르소나를 따릅니다.

        [AI 결재 요청: 'SELL' (수익 실현)]
        영석님, Agent가 보유 종목에서 'RSI 과열(수익 실현)' 신호를 포착했습니다. 'SELL' 또는 'HOLD' 결정을 JSON으로 응답해주세요.

        1. 종목 정보:
           - 종목명: {name}
           - 종목코드: {stock_info.get('code', 'N/A')}

        2. 매도 조건 (Agent가 1차 검증 완료):
           - [신호]: 실시간 RSI가 '75' 이상 과열 구간에 진입했습니다. (통과)
           - [참고: 매수가]: {avg_price:,.0f} 원
           - [참고: 현재 고점]: {high_price:,.0f} 원

        3. 결재 (JSON):
           'SELL' 또는 'HOLD' 중 하나를 선택하고, 그 사유를 간결하게 작성하여 JSON으로 응답해주세요. quantity는 0으로 설정해주세요.
        """
        return prompt.strip()

    # -----------------------------------------------------------------
    # 'ADD_WATCHLIST' (관심 종목 편입) 결재 프롬프트
    # -----------------------------------------------------------------
    def _build_add_watchlist_prompt(self, stock_info):
        # ... (기존 v9.0의 ADD_WATCHLIST 프롬프트 로직과 동일) ...
        def format_market_cap(mc):
            mc_in_won = int(mc) * 1_000_000
            if mc_in_won >= 1_000_000_000_000: return f"{mc_in_won / 1_000_000_000_000:.1f}조 원"
            elif mc_in_won >= 100_000_000: return f"{mc_in_won / 100_000_000:,.0f}억 원"
            return f"{mc_in_won:,.0f} 원"
        prompt = f"""
        [시스템 지침]
        당신은 영석님의 AI 주식 투자 보좌관 '제니'이며, 잠재적 투자 대상을 발굴하는 '수석 애널리스트' 역할을 수행합니다.
        [AI 결재 요청: 'ADD_WATCHLIST' (관심 종목 편입)]
        영석님, Scout이 발굴한 유망 종목 후보입니다. 아래 근거를 종합적으로 검토하여 Watchlist 편입 여부를 결정해주세요.
        1. 종목 정보:
           - 종목명: {stock_info.get('name', 'N/A')} ({stock_info.get('code', 'N/A')})
        2. 편입 근거 (Scout이 1차 분석):
           - 기술적 분석: {stock_info.get('technical_reason', '해당 없음')}
           - 뉴스/공시 분석 (RAG): {stock_info.get('news_reason', '해당 없음')}
        3. 펀더멘털 (참고):
           - PER: {stock_info.get('per', 'N/A'):.2f} 배
           - PBR: {stock_info.get('pbr', 'N/A'):.2f} 배
           - 시가총액: {format_market_cap(stock_info.get('market_cap', 0))}
        4. 결재 (JSON):
           'APPROVE' 또는 'REJECT' 중 하나를 선택하고, 그 사유를 간결하게 작성하여 JSON으로 응답해주세요. quantity는 0으로 설정해주세요.
        """
        return prompt.strip()


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
                prompt = self._build_buy_prompt_mean_reversion(stock_info, buy_signal_type)
            elif trade_type == 'BUY_TREND':
                buy_signal_type = kwargs.get('buy_signal_type', 'GOLDEN_CROSS')
                prompt = self._build_buy_prompt_golden_cross(stock_info, buy_signal_type=buy_signal_type)
            elif trade_type in ['SELL', 'SELL_V2']:
                prompt = self._build_sell_prompt(stock_info)
            elif trade_type == 'ADD_WATCHLIST':
                prompt = self._build_add_watchlist_prompt(stock_info)
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
            prompt = self._build_buy_prompt_ranking(candidates_data)
            
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
            prompt = self._build_parameter_verification_prompt(
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
    
    def _build_parameter_verification_prompt(self, current_params: dict, new_params: dict,
                                            current_performance: dict, new_performance: dict,
                                            market_summary: str) -> str:
        """파라미터 변경 검증 프롬프트 생성"""
        
        # 변경 폭 계산
        change_analysis = []
        for key, new_value in new_params.items():
            if key in current_params:
                current_value = float(current_params[key])
                new_value_float = float(new_value)
                change_pct = ((new_value_float - current_value) / current_value) * 100 if current_value != 0 else 0
                change_analysis.append(
                    f"  - {key}: {current_value} → {new_value_float} (변경폭: {change_pct:+.1f}%)"
                )
        
        change_summary = "\n".join(change_analysis) if change_analysis else "  (변경 없음)"
        
        prompt = f"""
        [시스템 지침]
        당신은 최고의 AI 퀀트 전략 분석가입니다. 자동화된 백테스트 시스템이 찾아낸 새로운 전략 파라미터를 검증하고, 이 변경이 논리적으로 타당하며 과최적화(overfitting)의 위험이 없는지 최종 승인하는 임무를 맡았습니다.
        
        [Context]
        - 분석 기간: 최근 90일
        - 최근 시장 요약: "{market_summary}"
        
        [Current Strategy (AS-IS)]
        - 현재 성과(90일): MDD {current_performance['mdd']:.2f}%, 수익률 {current_performance['return']:.2f}%
        
        [Proposed Strategy (TO-BE)]
        - 제안 성과(90일): MDD {new_performance['mdd']:.2f}%, 수익률 {new_performance['return']:.2f}%
        
        [Parameter Changes]
        {change_summary}
        
        [Your Task - Critical Analysis]
        
        1. **Logical Validity (논리적 타당성)**
           - 제안된 파라미터 변경이 주어진 시장 요약과 논리적으로 부합합니까?
           - 변경 방향이 합리적입니까? (예: 변동성 증가 시 손절 기준 완화는 위험)
        
        2. **Overfitting Risk (과최적화 위험)**
           - 성과 향상이 과도하지 않습니까? (MDD 개선 + 수익률 향상이 동시에 크면 의심)
           - 백테스트 기간(90일)이 충분히 다양한 시장 상황을 포함했습니까?
        
        3. **Safety Guardrail (안전장치) - CRITICAL**
           - **모든 파라미터 변경 폭이 기존 값 대비 ±10% 이내입니까?**
           - 만약 10%를 초과하는 변경이 있다면, 매우 위험하므로 'REJECT' 하십시오.
        
        4. **Performance Improvement (성과 개선)**
           - MDD 개선: {new_performance['mdd'] - current_performance['mdd']:.2f}%p
           - 수익률 개선: {new_performance['return'] - current_performance['return']:.2f}%p
           - 이 개선이 실질적이고 지속 가능합니까?
        
        5. **Final Decision**
           - 종합적으로 판단하여 이 파라미터 변경을 실시간 거래에 적용하는 것을 승인(true) 또는 거절(false) 하십시오.
           - 신뢰도 점수(0.0~1.0)를 함께 제공하십시오.
             * 0.9~1.0: 매우 확신, 즉시 적용 권장
             * 0.7~0.9: 확신, 적용 가능
             * 0.5~0.7: 보통, 신중한 적용
             * 0.0~0.5: 불확실, 적용 비권장
        
        [Response Format]
        JSON 형식으로 응답하십시오:
        {{
          "is_approved": <true or false>,
          "reasoning": "<상세한 분석 및 판단 근거>",
          "confidence_score": <0.0 to 1.0>
        }}
        """
        return prompt.strip()

    # -----------------------------------------------------------------
    # [v3.0] 종목 심층 분석 및 점수 산출 (Scout 단계)
    # -----------------------------------------------------------------
    def get_jennies_analysis_score(self, stock_info):
        """
        종목의 뉴스, 펀더멘털, 모멘텀을 종합하여 매수 적합도 점수(0~100)를 산출합니다.
        [Phase 1: Hunter Scout] - Claude Haiku (빠르고 똑똑함)
        """
        # [v4.0] Claude Haiku 우선 (빠르고 프롬프트 준수 우수), 없으면 GPT, 최후에 Gemini
        provider = self.provider_claude if hasattr(self, 'provider_claude') and self.provider_claude else \
                   (self.provider_openai if self.provider_openai else self.provider_gemini)
        if provider is None:
            logger.error("❌ [JennieBrain] LLM 모델이 초기화되지 않았습니다!")
            return {'score': 0, 'grade': 'D', 'reason': 'JennieBrain 초기화 실패'}
            
        try:
            prompt = self._build_analysis_prompt(stock_info)
            
            provider_name = provider.name.upper()
            logger.info(f"--- [JennieBrain/Phase1-Hunter] 필터링 ({provider_name}): {stock_info.get('name')} ---")
            
            # Claude Haiku 사용 (빠르고 프롬프트 준수 우수)
            result = provider.generate_json(
                prompt,
                ANALYSIS_RESPONSE_SCHEMA,
                temperature=0.3,
            )
            
            logger.info(f"--- [JennieBrain] 분석 완료: {stock_info.get('name')} ---")
            logger.info(f"   (점수): {result.get('score')}점 (등급: {result.get('grade')})")
            
            return result
            
        except Exception as e:
            logger.error(f"❌ [JennieBrain] 분석 중 오류: {e}", exc_info=True)
            return {'score': 0, 'grade': 'D', 'reason': f"분석 오류: {e}"}

    def _build_analysis_prompt(self, stock_info):
        """종목 심층 분석 프롬프트 생성"""
        
        def format_market_cap(mc):
            if not mc: return "N/A"
            mc_in_won = int(mc) * 1_000_000
            if mc_in_won >= 1_000_000_000_000: return f"{mc_in_won / 1_000_000_000_000:.1f}조 원"
            elif mc_in_won >= 100_000_000: return f"{mc_in_won / 100_000_000:,.0f}억 원"
            return f"{mc_in_won:,.0f} 원"
            
        # [v4.0] 제니 피드백 반영 - 명확한 점수 계산
        news = stock_info.get('news_reason', '특별한 뉴스 없음')
        per = stock_info.get('per', 'N/A')
        pbr = stock_info.get('pbr', 'N/A')
        momentum = stock_info.get('momentum_score', 0)
        
        prompt = f"""당신은 주식 분석 AI입니다. 아래 종목을 분석하고 점수를 매기세요.

종목: {stock_info.get('name', 'N/A')} ({stock_info.get('code', 'N/A')})
시가총액: {format_market_cap(stock_info.get('market_cap', 0))}
PER: {per}
PBR: {pbr}
모멘텀: {momentum}%
뉴스: {news}

## 점수 계산 (기본 50점에서 시작)

1. 뉴스 점수:
   - 호재(수주, 실적 호조): +15~25점
   - 긍정 뉴스: +5~10점  
   - 뉴스 없음: 0점
   - 악재: -10~20점

2. 펀더멘털:
   - PER<10: +10점
   - PBR<1: +5점
   - PER>30: -10점

3. 모멘텀:
   - 양수: +5점
   - 음수: -5점

## 등급
- S(80+): 강력추천
- A(70-79): 추천
- B(60-69): 관심
- C(50-59): 중립
- D(40-49): 주의
- F(<40): 회피

JSON으로 응답: {{"score": 숫자, "grade": "등급", "reason": "이유"}}

**점수는 반드시 50점 기준으로 가감하여 계산하세요. 뉴스가 없고 펀더멘털이 적정하면 약 55점입니다.**"""
        return prompt.strip()

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
        # OpenAI가 없으면 Gemini로 폴백
        provider = self.provider_openai if self.provider_openai else self.provider_gemini
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
            
            # GPT-5.1-mini (or GPT-4o-mini) 사용 - Reasoning quality 우수
            model_name = provider.reasoning_model if hasattr(provider, 'reasoning_model') else None
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
        # OpenAI가 없으면 Gemini로 폴백
        provider = self.provider_openai if self.provider_openai else self.provider_gemini
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
            # GPT-5.1-mini (or GPT-4o-mini) 사용 - 체계적인 판단 우수
            model_name = provider.reasoning_model if hasattr(provider, 'reasoning_model') else None
            logger.info(f"--- [JennieBrain/Phase3-Judge] 최종 판결 ({provider.name}): {stock_info.get('name')} ---")
            
            result = provider.generate_json(
                prompt,
                ANALYSIS_RESPONSE_SCHEMA, # 기존 스키마 재사용 (score, grade, reason)
                temperature=0.1 # 판결은 냉정하게
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
        # Claude > GPT > Gemini 순으로 시도
        provider = self.provider_claude if hasattr(self, 'provider_claude') and self.provider_claude else \
                   (self.provider_openai if self.provider_openai else self.provider_gemini)
        if provider is None:
            logger.error("❌ [JennieBrain] LLM 모델이 초기화되지 않았습니다!")
            return {'score': 0, 'grade': 'D', 'reason': 'JennieBrain 초기화 실패'}
        
        try:
            prompt = self._build_hunter_prompt_v5(stock_info, quant_context)
            
            provider_name = provider.name.upper()
            logger.info(f"--- [JennieBrain/v5-Hunter] 통계기반 필터링 ({provider_name}): {stock_info.get('name')} ---")
            
            result = provider.generate_json(
                prompt,
                ANALYSIS_RESPONSE_SCHEMA,
                temperature=0.2,  # 데이터 기반이므로 낮은 temperature
            )
            
            logger.info(f"   ✅ v5 Hunter 완료: {stock_info.get('name')} - {result.get('score')}점")
            return result
            
        except Exception as e:
            logger.error(f"❌ [JennieBrain/v5-Hunter] 분석 오류: {e}", exc_info=True)
            return {'score': 0, 'grade': 'D', 'reason': f"분석 오류: {e}"}
    
    def _build_hunter_prompt_v5(self, stock_info: dict, quant_context: str = None) -> str:
        """
        [v1.0] 정량 통계 컨텍스트가 포함된 Hunter 프롬프트 생성
        
        GPT 설계 핵심: "이 통계는 중요한 판단 근거이니 반드시 반영하세요"
        """
        name = stock_info.get('name', 'N/A')
        code = stock_info.get('code', 'N/A')
        news = stock_info.get('news_reason', '특별한 뉴스 없음')
        
        # 정량 컨텍스트가 없으면 기존 방식으로 폴백
        if not quant_context:
            return self._build_analysis_prompt(stock_info)
        
        prompt = f"""당신은 데이터 기반 주식 분석 AI입니다. **정량 분석 결과를 반드시 참고**하여 점수를 매기세요.

## 종목 정보
종목: {name} ({code})

{quant_context}

## 최신 뉴스 (정성적 판단 영역)
{news}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## [중요] 당신의 역할

**정량 분석은 이미 완료되었습니다.** 당신은 다음 **정성적 요소만** 평가하세요:

1. **뉴스 맥락 해석**: 이 뉴스가 단기 이벤트인지, 펀더멘털 변화인지?
2. **리스크 체크**: CEO 리스크, 횡령, 규제 등 정량 분석이 놓친 위험 요소?
3. **타이밍 판단**: 이미 반영된 재료인지, 아직 미반영인지?

## 점수 계산 방식

**기준: 정량 점수를 기반으로 ±20점 범위 내에서 조정**

위 정량 점수가 70점이라면:
- 뉴스가 매우 긍정적 + 리스크 없음 → 80~90점
- 뉴스 중립 → 65~75점 (정량 점수 유지)
- 숨겨진 리스크 발견 → 50~60점
- 치명적 악재 발견 → 40점 미만

## 등급
- S(80+): 강력추천 - 정량+정성 모두 우수
- A(70-79): 추천 - 정량 우수 + 정성 양호
- B(60-69): 관심 - 정량 또는 정성 중 하나 우수
- C(50-59): 중립
- D(40-49): 주의 - 리스크 발견
- F(<40): 회피 - 치명적 리스크

JSON 응답: {{"score": 숫자, "grade": "등급", "reason": "판단 이유"}}

⚠️ **중요**: 위 정량 분석의 조건부 승률과 표본 수는 역사적 데이터입니다. 
표본 수가 30개 이상이면 신뢰할 수 있고, 15개 미만이면 보수적으로 판단하세요."""

        return prompt.strip()
    
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
        provider = self.provider_openai if self.provider_openai else self.provider_gemini
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

