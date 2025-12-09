"""
tests/shared/test_llm_providers.py - LLM Provider Mock 테스트 (2단계)
====================================================================

shared/llm.py의 LLM Provider 클래스들을 테스트합니다.
auth.get_secret과 API 클라이언트들을 mock하여 외부 의존성 없이 테스트합니다.

실행 방법:
    pytest tests/shared/test_llm_providers.py -v
"""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock
import json


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def mock_safety_settings():
    """안전 설정 fixture"""
    return [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    ]


@pytest.fixture
def sample_response_schema():
    """샘플 JSON 응답 스키마"""
    return {
        "type": "object",
        "properties": {
            "score": {"type": "integer"},
            "grade": {"type": "string"},
            "reason": {"type": "string"}
        },
        "required": ["score", "grade", "reason"]
    }


# ============================================================================
# Tests: GeminiLLMProvider
# ============================================================================

class TestGeminiLLMProvider:
    """Gemini LLM Provider 테스트"""
    
    @patch('shared.auth.get_secret')
    @patch('google.generativeai.configure')
    def test_init_success(self, mock_configure, mock_get_secret, mock_safety_settings):
        """초기화 성공"""
        from shared.llm import GeminiLLMProvider
        
        mock_get_secret.return_value = 'fake-gemini-api-key'
        
        provider = GeminiLLMProvider(
            project_id='test-project',
            gemini_api_key_secret='gemini-api-key',
            safety_settings=mock_safety_settings
        )
        
        assert provider is not None
        assert provider.name == 'gemini'
        mock_configure.assert_called_once_with(api_key='fake-gemini-api-key')
    
    @patch('shared.auth.get_secret')
    def test_init_missing_api_key(self, mock_get_secret, mock_safety_settings):
        """API 키 없으면 RuntimeError"""
        from shared.llm import GeminiLLMProvider
        
        mock_get_secret.return_value = None
        
        with pytest.raises(RuntimeError) as exc_info:
            GeminiLLMProvider(
                project_id='test-project',
                gemini_api_key_secret='gemini-api-key',
                safety_settings=mock_safety_settings
            )
        
        assert 'Secret' in str(exc_info.value) or '로드 실패' in str(exc_info.value)
    
    @patch('shared.auth.get_secret')
    @patch('google.generativeai.configure')
    @patch('google.generativeai.GenerativeModel')
    def test_generate_json_success(self, mock_model_class, mock_configure, mock_get_secret, 
                                    mock_safety_settings, sample_response_schema):
        """generate_json 성공"""
        from shared.llm import GeminiLLMProvider
        
        mock_get_secret.return_value = 'fake-api-key'
        
        # Mock response
        mock_response = MagicMock()
        mock_response.text = json.dumps({'score': 75, 'grade': 'B', 'reason': 'Good stock'})
        
        mock_model_instance = MagicMock()
        mock_model_instance.generate_content.return_value = mock_response
        mock_model_class.return_value = mock_model_instance
        
        provider = GeminiLLMProvider('project', 'secret', mock_safety_settings)
        result = provider.generate_json(
            "Analyze this stock",
            sample_response_schema,
            temperature=0.2
        )
        
        assert result['score'] == 75
        assert result['grade'] == 'B'
    
    @patch('shared.auth.get_secret')
    @patch('google.generativeai.configure')
    @patch('google.generativeai.GenerativeModel')
    def test_generate_json_fallback(self, mock_model_class, mock_configure, mock_get_secret,
                                     mock_safety_settings, sample_response_schema):
        """첫 번째 모델 실패 시 폴백 모델로 재시도"""
        from shared.llm import GeminiLLMProvider
        
        mock_get_secret.return_value = 'fake-api-key'
        
        # 첫 번째 호출은 실패, 두 번째는 성공
        call_count = [0]
        
        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("First model failed")
            mock_response = MagicMock()
            mock_response.text = json.dumps({'score': 60, 'grade': 'C', 'reason': 'Fallback'})
            return mock_response
        
        mock_model_instance = MagicMock()
        mock_model_instance.generate_content.side_effect = side_effect
        mock_model_class.return_value = mock_model_instance
        
        provider = GeminiLLMProvider('project', 'secret', mock_safety_settings)
        result = provider.generate_json(
            "Analyze this stock",
            sample_response_schema,
            fallback_models=['gemini-1.5-flash']
        )
        
        assert result['score'] == 60
        assert result['reason'] == 'Fallback'
    
    @patch('shared.auth.get_secret')
    @patch('google.generativeai.configure')
    @patch('google.generativeai.GenerativeModel')
    def test_generate_json_all_fail(self, mock_model_class, mock_configure, mock_get_secret,
                                     mock_safety_settings, sample_response_schema):
        """모든 모델 실패 시 RuntimeError"""
        from shared.llm import GeminiLLMProvider
        
        mock_get_secret.return_value = 'fake-api-key'
        
        mock_model_instance = MagicMock()
        mock_model_instance.generate_content.side_effect = Exception("API Error")
        mock_model_class.return_value = mock_model_instance
        
        provider = GeminiLLMProvider('project', 'secret', mock_safety_settings)
        
        with pytest.raises(RuntimeError) as exc_info:
            provider.generate_json(
                "Analyze this stock",
                sample_response_schema
            )
        
        assert 'LLM 호출 실패' in str(exc_info.value)


# ============================================================================
# Tests: OpenAILLMProvider
# ============================================================================

class TestOpenAILLMProvider:
    """OpenAI LLM Provider 테스트"""
    
    @patch('shared.auth.get_secret')
    def test_init_success(self, mock_get_secret, mock_safety_settings):
        """초기화 성공"""
        from shared.llm import OpenAILLMProvider
        
        mock_get_secret.return_value = 'fake-openai-api-key'
        
        with patch.object(OpenAILLMProvider, '__init__', lambda self, *args, **kwargs: None):
            provider = OpenAILLMProvider.__new__(OpenAILLMProvider)
            provider.safety_settings = mock_safety_settings
            provider.default_model = 'gpt-4o-mini'
            provider.reasoning_model = 'gpt-5-mini'
            provider.client = MagicMock()
            
            assert provider.default_model == 'gpt-4o-mini'
    
    def test_is_reasoning_model(self, mock_safety_settings):
        """Reasoning 모델 판별"""
        from shared.llm import OpenAILLMProvider
        
        # __init__ 우회
        provider = object.__new__(OpenAILLMProvider)
        provider.REASONING_MODELS = {"gpt-5-mini", "gpt-5", "o1", "o1-mini", "o3"}
        
        assert provider._is_reasoning_model('gpt-5-mini') is True
        assert provider._is_reasoning_model('o1-preview') is True
        assert provider._is_reasoning_model('gpt-4o') is False
        assert provider._is_reasoning_model('gpt-4o-mini') is False
    
    @patch('shared.auth.get_secret')
    def test_generate_json_success(self, mock_get_secret, mock_safety_settings, sample_response_schema):
        """generate_json 성공"""
        from shared.llm import OpenAILLMProvider
        
        mock_get_secret.return_value = 'fake-api-key'
        
        # Provider 인스턴스 직접 생성 (우회)
        provider = object.__new__(OpenAILLMProvider)
        provider.safety_settings = mock_safety_settings
        provider.default_model = 'gpt-4o-mini'
        provider.REASONING_MODELS = {"gpt-5-mini", "o1"}
        
        # Mock OpenAI client
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps({
            'score': 80, 'grade': 'A', 'reason': 'Excellent'
        })
        mock_client.chat.completions.create.return_value = mock_response
        provider.client = mock_client
        
        result = provider.generate_json(
            "Analyze this stock",
            sample_response_schema,
            temperature=0.2
        )
        
        assert result['score'] == 80
        assert result['grade'] == 'A'
    
    @patch('shared.auth.get_secret')
    def test_generate_json_reasoning_model_no_temperature(self, mock_get_secret, mock_safety_settings, sample_response_schema):
        """Reasoning 모델은 temperature 파라미터 없음"""
        from shared.llm import OpenAILLMProvider
        
        mock_get_secret.return_value = 'fake-api-key'
        
        provider = object.__new__(OpenAILLMProvider)
        provider.safety_settings = mock_safety_settings
        provider.default_model = 'gpt-5-mini'  # Reasoning 모델
        provider.REASONING_MODELS = {"gpt-5-mini", "o1"}
        
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps({
            'score': 70, 'grade': 'B', 'reason': 'Good'
        })
        mock_client.chat.completions.create.return_value = mock_response
        provider.client = mock_client
        
        provider.generate_json("Test", sample_response_schema, temperature=0.5)
        
        # temperature가 kwargs에 없어야 함 (reasoning model)
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert 'temperature' not in call_kwargs


# ============================================================================
# Tests: ClaudeLLMProvider
# ============================================================================

class TestClaudeLLMProvider:
    """Claude LLM Provider 테스트"""
    
    @patch('shared.auth.get_secret')
    def test_init_success(self, mock_get_secret, mock_safety_settings):
        """초기화 성공"""
        from shared.llm_providers import ClaudeLLMProvider
        
        mock_get_secret.return_value = 'fake-claude-api-key'
        
        # __init__ 우회
        provider = object.__new__(ClaudeLLMProvider)
        provider.safety_settings = mock_safety_settings
        provider.fast_model = 'claude-haiku-4-5'
        provider.reasoning_model = 'claude-sonnet-4-5'
        provider.client = MagicMock()
        
        assert provider.fast_model == 'claude-haiku-4-5'
    
    @patch('shared.auth.get_secret')
    def test_generate_json_success(self, mock_get_secret, mock_safety_settings, sample_response_schema):
        """generate_json 성공"""
        from shared.llm_providers import ClaudeLLMProvider
        
        mock_get_secret.return_value = 'fake-api-key'
        
        provider = object.__new__(ClaudeLLMProvider)
        provider.safety_settings = mock_safety_settings
        provider.fast_model = 'claude-haiku-4-5'
        
        # Mock Claude client
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_content = MagicMock()
        mock_content.text = json.dumps({'score': 85, 'grade': 'A', 'reason': 'Great stock'})
        mock_response.content = [mock_content]
        mock_client.messages.create.return_value = mock_response
        provider.client = mock_client
        
        result = provider.generate_json(
            "Analyze this stock",
            sample_response_schema,
            temperature=0.2
        )
        
        assert result['score'] == 85
        assert result['grade'] == 'A'
    
    @patch('shared.auth.get_secret')
    def test_generate_json_with_markdown(self, mock_get_secret, mock_safety_settings, sample_response_schema):
        """마크다운 코드블록 제거"""
        from shared.llm_providers import ClaudeLLMProvider
        
        provider = object.__new__(ClaudeLLMProvider)
        provider.safety_settings = mock_safety_settings
        provider.fast_model = 'claude-haiku-4-5'
        
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_content = MagicMock()
        # 마크다운 코드블록으로 감싼 JSON
        mock_content.text = '```json\n{"score": 90, "grade": "S", "reason": "Excellent"}\n```'
        mock_response.content = [mock_content]
        mock_client.messages.create.return_value = mock_response
        provider.client = mock_client
        
        result = provider.generate_json("Test", sample_response_schema)
        
        assert result['score'] == 90
        assert result['grade'] == 'S'


# ============================================================================
# Tests: build_llm_provider Factory
# ============================================================================

class TestBuildLlmProvider:
    """LLM Provider 팩토리 함수 테스트"""
    
    @patch('shared.auth.get_secret')
    @patch('google.generativeai.configure')
    def test_build_gemini_provider(self, mock_configure, mock_get_secret, mock_safety_settings):
        """Gemini Provider 생성"""
        from shared.llm import build_llm_provider
        
        mock_get_secret.return_value = 'fake-api-key'
        
        provider = build_llm_provider('project', 'gemini-secret', 'gemini')
        
        assert provider.name == 'gemini'
    
    @patch('shared.auth.get_secret')
    @patch('openai.OpenAI')
    def test_build_openai_provider(self, mock_openai_class, mock_get_secret, mock_safety_settings, monkeypatch):
        """OpenAI Provider 생성"""
        from shared.llm_providers import build_llm_provider
        
        mock_get_secret.return_value = 'fake-api-key'
        monkeypatch.setenv('OPENAI_API_KEY_SECRET', 'openai-secret')
        
        mock_openai_class.return_value = MagicMock()
        
        provider = build_llm_provider('project', 'gemini-secret', 'openai')
        
        assert provider.name == 'openai'
    
    def test_build_unsupported_provider(self, mock_safety_settings):
        """지원되지 않는 Provider 타입"""
        from shared.llm_providers import build_llm_provider
        
        with pytest.raises(ValueError) as exc_info:
            build_llm_provider('project', 'secret', 'unsupported_provider')
        
        assert '지원되지 않는' in str(exc_info.value)


# ============================================================================
# Tests: Provider Properties
# ============================================================================

class TestProviderProperties:
    """Provider 속성 테스트"""
    
    @patch('shared.auth.get_secret')
    @patch('google.generativeai.configure')
    def test_gemini_flash_model_name(self, mock_configure, mock_get_secret, mock_safety_settings, monkeypatch):
        """Gemini flash 모델명 확인"""
        from shared.llm import GeminiLLMProvider
        
        mock_get_secret.return_value = 'fake-api-key'
        monkeypatch.setenv('LLM_FLASH_MODEL_NAME', 'gemini-custom-flash')
        
        provider = GeminiLLMProvider('project', 'secret', mock_safety_settings)
        
        assert provider.flash_model_name() == 'gemini-custom-flash'
    
    @patch('shared.auth.get_secret')
    @patch('google.generativeai.configure')
    def test_gemini_default_model_from_env(self, mock_configure, mock_get_secret, mock_safety_settings, monkeypatch):
        """환경변수에서 기본 모델명 로드"""
        from shared.llm import GeminiLLMProvider
        
        mock_get_secret.return_value = 'fake-api-key'
        monkeypatch.setenv('LLM_MODEL_NAME', 'gemini-custom-pro')
        
        provider = GeminiLLMProvider('project', 'secret', mock_safety_settings)
        
        assert provider.default_model == 'gemini-custom-pro'

