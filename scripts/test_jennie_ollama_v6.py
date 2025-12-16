
import os
import sys
import logging
from pprint import pprint

# Add project root to path


# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.llm import JennieBrain

from shared.llm_factory import LLMFactory, LLMTier


# Setup Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_jennie_v6")

def test_hybrid_strategy():
    print("🚀 Starting JennieBrain v6 Hybrid Strategy Test...\n")
    
    # Initialize JennieBrain
    # Note: Keys are loaded from secrets.json internally or env vars.
    # Ensure .env is loaded or env vars are set.
    from dotenv import load_dotenv
    load_dotenv()
    
    brain = JennieBrain("test-project", "test-secret")
    
    # 1. Test FAST Tier (Sentiment) -> Local Qwen 2.5 3B
    print("\n[1] Testing FAST Tier (News Sentiment)...")
    try:
        sentiment = brain.analyze_news_sentiment(
            "삼성전자, 3분기 실적 '어닝 서프라이즈' 달성",
            "삼성전자가 반도체 업황 회복에 힘입어 3분기 영업이익 2.4조원을 기록, 시장 예상치를 상회했다."
        )
        print(f"✅ Result: {sentiment}")
        # Verify Provider? (Hard to verify internal state without peeking factory)
        # But logs should show "Ollama"
    except Exception as e:
        print(f"❌ FAST Tier Failed: {e}")

    # 2. Test REASONING Tier (Hunter) -> Local Qwen 2.5 14B
    print("\n[2] Testing REASONING Tier (Hunter Analysis)...")
    stock_info = {
        'name': 'SK하이닉스',
        'code': '000660',
        'per': 12.5,
        'pbr': 1.2,
        'market_cap': '100조',
        'news_reason': 'HBM3E 공급 독점 지속 전망',
        'technical_reason': '정배열 골든크로스 임박'
    }
    try:
        # Using v5 Hunter (Reasoning Tier)
        score = brain.get_jennies_analysis_score_v5(stock_info, quant_context="매출 성장률 상위 10%")
        print(f"✅ Result: {score}")
    except Exception as e:
        print(f"❌ REASONING Tier Failed: {e}")

    # 3. Test THINKING Tier (Judge) -> Cloud (OpenAI/Claude)
    print("\n[3] Testing THINKING Tier (Judge)...")
    debate_log = """
    Bull: HBM 독점은 당분간 깨질 수 없어. 압도적 기술력 차이야.
    Bear: 마이크론과 삼성의 추격이 거세다. 밸류에이션 부담도 있어.
    Bull: 하지만 AI 서버 투자는 이제 시작인걸?
    Bear: 경기 침체 오면 서버 투자부터 줄어들거야.
    """
    try:
        # Inject High Hunter Score to pass the Strategy Gate (Score >= 70)
        stock_info['hunter_score'] = 85
        judgment = brain.run_judge_scoring_v5(stock_info, debate_log)
        print(f"✅ Result: {judgment}")
    except Exception as e:
        print(f"❌ THINKING Tier Failed: {e}")

    print("\n🎉 Test Complete.")

if __name__ == "__main__":
    test_hybrid_strategy()
