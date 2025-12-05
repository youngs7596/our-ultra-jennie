"""
prompts - Ultra Jennie LLM 프롬프트 템플릿 패키지
===============================================

이 패키지는 LLM 호출에 사용하는 프롬프트 템플릿을 제공합니다.

구성 모듈:
---------
- competitor_benefit_prompt: 경쟁사 수혜 분석 프롬프트
  - 경쟁사 악재 감지
  - 반사이익 종목 추천
  - 섹터별 경쟁 관계 정의

주요 함수:
---------
- get_competitor_benefit_prompt(): 경쟁사 수혜 분석 기본 프롬프트
- build_competitor_event_detection_prompt(): 이벤트 감지 프롬프트
- build_beneficiary_recommendation_prompt(): 수혜주 추천 프롬프트
- detect_event_type_from_text(): 뉴스 텍스트에서 이벤트 유형 감지
- calculate_competitor_benefit(): 경쟁사 수혜 점수 계산

사용 예시:
---------
>>> from prompts import get_competitor_benefit_prompt, detect_event_type_from_text
>>>
>>> event = detect_event_type_from_text("쿠팡 개인정보 3370만건 유출")
>>> print(f"이벤트 유형: {event}")  # '보안사고'
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
