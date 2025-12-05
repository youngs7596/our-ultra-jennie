"""
LLM 요청에 사용하는 프롬프트 템플릿 모음.

[v5.2] 경쟁사 수혜 분석 시스템
- competitor_benefit_prompt: 경쟁사 악재 감지 및 수혜 분석
"""

from prompts.competitor_benefit_prompt import (
    COMPETITOR_GROUPS,
    EVENT_IMPACT_RULES,
    get_competitor_benefit_prompt,
    build_competitor_event_detection_prompt,
    build_beneficiary_recommendation_prompt,
    detect_event_type_from_text,
    get_competitors_for_company,
    calculate_competitor_benefit,
)

__all__ = [
    # 경쟁사 수혜 분석
    'COMPETITOR_GROUPS',
    'EVENT_IMPACT_RULES',
    'get_competitor_benefit_prompt',
    'build_competitor_event_detection_prompt',
    'build_beneficiary_recommendation_prompt',
    'detect_event_type_from_text',
    'get_competitors_for_company',
    'calculate_competitor_benefit',
]
