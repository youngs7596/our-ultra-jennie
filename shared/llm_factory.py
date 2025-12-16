from enum import Enum
import os
import threading
import time
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

class LLMTier(Enum):
    """
    LLM Tiers defining performance/quality trade-offs.
    """
    FAST = "FAST"           # High speed, low cost (e.g., Sentiment)
    REASONING = "REASONING" # Balanced (e.g., Hunter Summarization)
    THINKING = "THINKING"   # Deep Logic (e.g., Judge, Reports)


class ModelStateManager:
    """
    Singleton to manage Local LLM State (VRAM usage) to prevent race conditions.
    """
    _instance = None
    _lock = threading.Lock()
    _current_model: Optional[str] = None
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(ModelStateManager, cls).__new__(cls)
        return cls._instance

    def set_current_model(self, model_name: str):
        with self._lock:
            self._current_model = model_name

    def get_current_model(self) -> Optional[str]:
        with self._lock:
            return self._current_model

    def is_model_loaded(self, model_name: str) -> bool:
        with self._lock:
            return self._current_model == model_name


class LLMFactory:
    """
    Factory to create and retrieve LLM Providers based on Tiers and Configuration.
    """
    _providers: Dict[LLMTier, Any] = {}
    _state_manager = ModelStateManager()

    @staticmethod
    def _get_env_provider_type(tier: LLMTier) -> str:
        """
        Get the configured provider type (ollama, openai, claude) for a tier.
        Defaults: FAST/REASONING -> ollama, THINKING -> openai
        """
        env_key = f"TIER_{tier.value}_PROVIDER"
        default = "ollama" if tier in [LLMTier.FAST, LLMTier.REASONING] else "openai"
        return os.getenv(env_key, default).lower()

    @staticmethod
    def _get_local_model_name(tier: LLMTier) -> str:
        """Get the specific local model name for a tier."""
        env_key = f"LOCAL_MODEL_{tier.value}"
        defaults = {
            LLMTier.FAST: "qwen2.5:3b",
            LLMTier.REASONING: "qwen2.5:14b",
            LLMTier.THINKING: "deepseek-r1:32b"
        }
        return os.getenv(env_key, defaults.get(tier, "qwen2.5:14b"))

    @classmethod
    def get_provider(cls, tier: LLMTier):
        """
        Returns an initialized LLM Provider for the requested Tier.
        """
        from shared.llm_providers import (
            OllamaLLMProvider, 
            OpenAILLMProvider, 
            ClaudeLLMProvider, 
            GeminiLLMProvider
        )

        provider_type = cls._get_env_provider_type(tier)
        
        # Determine specific model name if applicable
        model_name = None
        if provider_type == "ollama":
            model_name = cls._get_local_model_name(tier)

        # Cache key could be expanded if we need multiple instances per tier with different configs
        # For now, simplistic caching per tier is fine unless we change config at runtime.
        # However, to be safe with config changes, we might instantiate fresh or check config.
        # Let's instantiate fresh for now to respect dynamic env vars, or we can singleton it.
        # Given the state manager, instantiating generic providers is cheap. 
        # OllamaProvider needs the model name.

        if provider_type == "ollama":
            return OllamaLLMProvider(
                model=model_name,
                state_manager=cls._state_manager,
                is_fast_tier=(tier == LLMTier.FAST),
                is_thinking_tier=(tier == LLMTier.THINKING)
            )
        elif provider_type == "openai":
            # Map tier to OpenAI models if needed, else use default in Provider
            # implementation or env var. For now, let's assume Provider handles default
            # or we pass it. Provider currently reads from env (OPENAI_MODEL_NAME_...)?
            # Existing OpenAILLMProvider might need updates or we rely on its defaults.
            return OpenAILLMProvider() 
        elif provider_type == "claude":
            return ClaudeLLMProvider() 
        elif provider_type == "gemini":
            return GeminiLLMProvider()
        
        raise ValueError(f"Unknown provider type: {provider_type} for tier {tier}")
