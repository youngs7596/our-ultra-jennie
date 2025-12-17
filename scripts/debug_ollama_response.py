
import sys
import os
import logging
import json

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.llm_providers import OllamaLLMProvider
from shared.llm_constants import ANALYSIS_RESPONSE_SCHEMA

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("DebugOllama")

class MockStateManager:
    def get_current_model(self):
        return "none"
    def set_current_model(self, model):
        logger.info(f"StateManager: Model switched to {model}")

def run_verification():
    print("--- [Verification] Starting LLM Response Fix Check ---")
    
    model_name = "qwen3:14b"
    
    provider = OllamaLLMProvider(
        model=model_name,
        state_manager=MockStateManager(),
        host="http://localhost:11434"
    )
    
    prompt = """당신은 데이터 기반 주식 분석 AI입니다.
## 종목 정보
종목: 삼성전자 (005930)

## 정량 분석
- 점수: 80.0
- 상태: 양호

JSON으로 응답: {"score": 숫자, "grade": "등급", "reason": "한글 이유"}
**반드시 JSON 형식만 출력하세요.**
"""

    print(f"--- Calling generate_json (FIXED) with {model_name} ---")
    
    try:
        # This calls the fixed method in shared/llm_providers.py
        # It should NOT send format='json'
        # It SHOULD handle parsing correctly
        result = provider.generate_json(
            prompt,
            ANALYSIS_RESPONSE_SCHEMA,
            temperature=0.2
        )
        
        print("\n--- Result ---")
        print(json.dumps(result, indent=2, ensure_ascii=False))
        
        if result.get("score") is not None:
            print("\n✅ VERIFICATION SUCCESS: Valid JSON received!")
        else:
            print("\n❌ VERIFICATION FAILED: 'score' is missing.")
            
    except Exception as e:
        print(f"\n❌ Exception: {e}")

if __name__ == "__main__":
    run_verification()
