"""
shared/llm_providers.py - LLM Provider 클래스들

이 모듈은 각 LLM 서비스(Gemini, OpenAI, Claude, Ollama)에 대한 Provider 클래스를 제공합니다.

핵심 구성요소:
-------------
1. BaseLLMProvider: LLM 프로바이더 추상 베이스 클래스
2. GeminiLLMProvider: Google Gemini API 구현 (Scout 단계)
3. ClaudeLLMProvider: Anthropic Claude API 구현 (Hunter 단계)  
4. OpenAILLMProvider: OpenAI GPT API 구현 (Judge 단계)
5. OllamaLLMProvider: Local LLM API 구현 (Cost Saving)
"""

import logging
import json
import os
import re
import time
import requests
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)


class BaseLLMProvider(ABC):
    def __init__(self, safety_settings=None):
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


class OllamaLLMProvider(BaseLLMProvider):
    """
    Ollama Local LLM Provider.
    Implements defensive coding: Retries, Tag Removal, Timeouts, Keep-Alive.
    """
    def __init__(
        self, 
        model: str, 
        state_manager: Any,
        is_fast_tier: bool = False,
        is_thinking_tier: bool = False,
        host: str = "http://localhost:11434"
    ):
        super().__init__()
        self.model = model
        self.state_manager = state_manager
        self.host = os.getenv("OLLAMA_HOST", host)
        
        # [Defensive] Timeout Strategy
        if is_fast_tier:
            self.timeout = 60  # 1 min for fast tasks
        elif is_thinking_tier:
            self.timeout = 300 # 5 min for deep thinking
        else:
            self.timeout = 120 # 2 min default (Reasoning)
            
        self.max_retries = 3

    def _clean_deepseek_tags(self, text: str) -> str:
        """
        [Defensive] Remove <think>...</think> tags from DeepSeek output.
        Some models output reasoning trace which breaks JSON parsing.
        """
        # Remove multiline think tags
        cleaned = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
        return cleaned.strip()

    def _ensure_model_loaded(self):
        """
        [State Management] Ensure the model is loaded efficiently.
        """
        current = self.state_manager.get_current_model()
        if current != self.model:
            logger.info(f"🔄 [Ollama] Switching model: {current} -> {self.model} ...")
            # Note: The actual loading happens on the first inference request,
            # but we update our state manager to reflect intent.
            self.state_manager.set_current_model(self.model)

    def _call_ollama_api(self, endpoint: str, payload: Dict) -> Dict:
        """
        [Defensive] Robust API Caller with Retries
        """
        url = f"{self.host}{endpoint}"
        payload["stream"] = False
        payload["keep_alive"] = -1 # [Ops] Prevent unloading
        
        last_error = None
        
        for attempt in range(self.max_retries):
            try:
                self._ensure_model_loaded()
                response = requests.post(url, json=payload, timeout=self.timeout)
                response.raise_for_status()
                return response.json()
            except requests.exceptions.Timeout:
                logger.warning(f"⚠️ [Ollama] Timeout ({self.timeout}s) on attempt {attempt+1}/{self.max_retries}")
                last_error = TimeoutError(f"Ollama timed out after {self.timeout}s")
            except Exception as e:
                logger.warning(f"⚠️ [Ollama] Error on attempt {attempt+1}/{self.max_retries}: {e}")
                last_error = e
            
            # Exponential Backoff
            time.sleep(2 ** attempt)
            
        raise last_error

    def generate_json(
        self,
        prompt: str,
        response_schema: Dict,
        *,
        temperature: float = 0.2,
        model_name: Optional[str] = None,
        fallback_models: Optional[Sequence[str]] = None,
    ) -> Dict:
        target_model = model_name or self.model
        
        payload = {
            "model": target_model,
            "prompt": prompt,
            "format": "json",
            "options": {
                "temperature": temperature,
                "num_ctx": 8192 # Context Window
            }
        }

        try:
            result = self._call_ollama_api("/api/generate", payload)
            content = result.get("response", "")
            
            # [Defensive] Tag Removal
            content = self._clean_deepseek_tags(content)
            
            # [Defensive] JSON Parsing with basic cleanup
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                # Try to find JSON block if mixed with text
                if "```json" in content:
                    content = content.split("```json")[1].split("```")[0]
                elif "{" in content:
                    start = content.find("{")
                    end = content.rfind("}") + 1
                    content = content[start:end]
                return json.loads(content)
                
        except Exception as e:
            logger.error(f"❌ [Ollama] generate_json failed: {e}")
            raise

    def generate_chat(
        self,
        history: List[Dict],
        response_schema: Optional[Dict] = None,
        *,
        temperature: float = 0.2,
        model_name: Optional[str] = None,
        fallback_models: Optional[Sequence[str]] = None,
    ) -> Dict:
        target_model = model_name or self.model
        
        messages = []
        for h in history:
            role = h.get('role', 'user')
            if role == 'model': role = 'assistant'
            content = h.get('parts', [{}])[0].get('text', '') or h.get('content', '')
            messages.append({"role": role, "content": content})

        payload = {
            "model": target_model,
            "messages": messages,
            "options": {
                "temperature": temperature,
                "num_ctx": 8192
            }
        }
        
        if response_schema:
            payload["format"] = "json"

        try:
            result = self._call_ollama_api("/api/chat", payload)
            content = result.get("message", {}).get("content", "")
            
            # [Defensive] Tag Removal
            content = self._clean_deepseek_tags(content)

            if response_schema:
                try:
                    return json.loads(content)
                except json.JSONDecodeError:
                     if "```json" in content:
                        content = content.split("```json")[1].split("```")[0]
                     elif "{" in content:
                        start = content.find("{")
                        end = content.rfind("}") + 1
                        content = content[start:end]
                     return json.loads(content)
            
            return {"text": content}

        except Exception as e:
             logger.error(f"❌ [Ollama] generate_chat failed: {e}")
             raise


class GeminiLLMProvider(BaseLLMProvider):
    def __init__(self, project_id: str, gemini_api_key_secret: str, safety_settings):
        super().__init__(safety_settings)
        import google.generativeai as genai
        from . import auth
        
        api_key = auth.get_secret(gemini_api_key_secret, project_id)
        if not api_key:
            raise RuntimeError(f"GCP Secret '{gemini_api_key_secret}' 로드 실패")

        genai.configure(api_key=api_key)
        self._genai = genai
        self.default_model = os.getenv("LLM_MODEL_NAME", "gemini-2.5-flash")
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
            self._model_cache[cache_key] = self._genai.GenerativeModel(
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

                model = self._genai.GenerativeModel(
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
    
    REASONING_MODELS = {"gpt-5-mini", "gpt-5", "o1", "o1-mini", "o1-preview", "o3", "o3-mini"}
    
    def __init__(self, project_id: Optional[str] = None, openai_api_key_secret: Optional[str] = None, safety_settings=None):
        super().__init__(safety_settings)
        try:
            from openai import OpenAI
            self._openai_module = OpenAI
        except ImportError:
            raise RuntimeError("openai 패키지가 설치되지 않았습니다. pip install openai 실행이 필요합니다.")
        
        # If secrets are provided, fetch them. Ideally passed from factory or env.
        # Fallback to direct env var for simplicity in Factory pattern if secret manager is tricky without project_id
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key and project_id and openai_api_key_secret:
             from . import auth
             api_key = auth.get_secret(openai_api_key_secret, project_id)
        
        if not api_key:
             # Just log warning, might rely on env var that OpenAI client picks up automatically
             pass 
        
        self.client = self._openai_module(api_key=api_key)
        self.default_model = os.getenv("OPENAI_MODEL_NAME", "gpt-5-mini")
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
        
        messages = []
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
                kwargs = {"model": target_model, "messages": messages}
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
    
    def __init__(self, project_id: Optional[str] = None, claude_api_key_secret: Optional[str] = None, safety_settings=None):
        super().__init__(safety_settings)
        try:
            import anthropic
            self._anthropic_module = anthropic
        except ImportError:
            raise RuntimeError("anthropic 패키지가 설치되지 않았습니다. pip install anthropic 실행이 필요합니다.")
        
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key and project_id and claude_api_key_secret:
            from . import auth
            api_key = auth.get_secret(claude_api_key_secret, project_id)

        self.client = self._anthropic_module.Anthropic(api_key=api_key)
        self.fast_model = os.getenv("CLAUDE_FAST_MODEL", "claude-haiku-4-5")
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
                    max_tokens=8192,  # [v1.1] 4096→8192
                    temperature=temperature,
                    system="You are a helpful assistant. Always respond with valid JSON only, no markdown formatting.",
                    messages=[{"role": "user", "content": prompt}]
                )
                content = response.content[0].text
                raw_content = content
                if "```json" in content:
                    content = content.split("```json")[1].split("```")[0]
                elif "```" in content:
                    content = content.split("```")[1].split("```")[0]
                return json.loads(content.strip())
            except json.JSONDecodeError as je:
                logger.error(f"❌ [ClaudeProvider] JSON 파싱 실패: {je}")
                logger.error(f"   (Raw Content): {raw_content[:500]}...")
                last_error = je
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
                    max_tokens=4096,  # [v1.1] 2048→4096
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
    LLM Provider 팩토리 함수 (Legacy Support - JennieBrain will use Factory directly)
    """
    from .llm_constants import SAFETY_SETTINGS
    
    provider_type = provider_type.lower()
    
    if provider_type == "gemini":
        return GeminiLLMProvider(project_id, gemini_api_key_secret, SAFETY_SETTINGS)
    elif provider_type == "openai":
        openai_api_key_secret = os.getenv("OPENAI_API_KEY_SECRET", "openai-api-key")
        return OpenAILLMProvider(project_id, openai_api_key_secret, SAFETY_SETTINGS)
    elif provider_type == "claude":
        claude_api_key_secret = os.getenv("CLAUDE_API_KEY_SECRET", "claude-api-key")
        return ClaudeLLMProvider(project_id, claude_api_key_secret, SAFETY_SETTINGS)
    else:
        # Fallback to Ollama if someone asks for it here, though Factory is preferred
        pass

    raise ValueError(f"지원되지 않는 LLM_PROVIDER: {provider_type}")
