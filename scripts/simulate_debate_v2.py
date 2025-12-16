import sys
import os

# Add project root to path
sys.path.append(os.getcwd())

from shared.llm_prompts import build_debate_prompt

def test_dynamic_debate():
    print("--- [Simulation] Dynamic Debate Roles ---")
    
    # Text Case: Bio Sector (Bullish)
    stock_info = {
        'name': 'Samsung BioLogics',
        'code': '207940',
        'news_reason': 'FDA approval for new plant',
        'per': 80.5,
        'pbr': 4.2,
        'market_cap': '50T'
    }
    keywords = ["Bio-Pharma", "CMO", "Growth"]
    hunter_score = 85 # Bullish
    
    print(f"\n[Case 1] Keywords: {keywords}, Score: {hunter_score} (Bullish)")
    prompt = build_debate_prompt(stock_info, hunter_score, keywords)
    
    if "Bio-Pharma" in prompt:
         print("✅ Keyword 'Bio-Pharma' successfully injected into Prompt.")
    else:
         print("❌ Keyword Injection Failed.")
    
    # Print a snippet to visually confirm Persona
    if "**1. 준호" in prompt:
        print("\n[Snippet of Persona]")
        start = prompt.find("**1. 준호")
        end = prompt.find("**2. 민지") + 200
        print(prompt[start:end] + "...")

    # Text Case: Semiconductor (Bearish)
    stock_info2 = {
        'name': 'SK Hynix',
        'code': '000660',
        'news_reason': 'Memory chip demand slowing down',
        'per': 5.5,
        'pbr': 1.2
    }
    keywords2 = ["Semiconductor", "Memory", "Cycle"]
    hunter_score = 30 # Bearish
    
    print(f"\n[Case 2] Keywords: {keywords2}, Score: {hunter_score} (Bearish)")
    prompt2 = build_debate_prompt(stock_info2, hunter_score, keywords2)
    
    if "Semiconductor" in prompt2:
         print("✅ Keyword 'Semiconductor' successfully injected into Prompt.")
    else:
         print("❌ Keyword Injection Failed.")

if __name__ == "__main__":
    test_dynamic_debate()
