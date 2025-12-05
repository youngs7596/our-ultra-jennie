#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Scout v5.0 Hybrid Scoring System

세 가지 설계(Claude, Gemini, GPT)의 장점을 통합한 하이브리드 채점 시스템.

핵심 철학:
- "감(LLM)을 믿기 전에, 통계(Data)로 검증하고, 비용(Cost)을 통제한다."
- 정량 점수는 LLM과 독립적으로 계산하여 검증 가능성 확보
- LLM에게 통계 컨텍스트를 제공하여 데이터 기반 판단 유도

구성 모듈:
- quant_scorer: 정량 점수 계산 엔진 (LLM 독립, 비용 0원)
- hybrid_scorer: 정량+정성 하이브리드 점수 결합
- factor_analyzer: 오프라인 팩터 분석 배치 작업
- schema: DB 테이블 스키마 정의

사용법:
```python
from shared.hybrid_scoring import QuantScorer, HybridScorer, FactorAnalyzer

# 1. 정량 점수 계산
quant_scorer = QuantScorer(db_conn, market_regime='BULL')
quant_result = quant_scorer.calculate_total_quant_score(
    stock_code='005930',
    stock_name='삼성전자',
    daily_prices_df=df,
    ...
)

# 2. 하이브리드 점수 결합
hybrid_scorer = HybridScorer(market_regime='BULL')
hybrid_result = hybrid_scorer.calculate_hybrid_score(quant_result, llm_score=75)

# 3. 오프라인 팩터 분석 (주간 배치)
analyzer = FactorAnalyzer(db_conn)
analyzer.run_full_analysis()
```
"""

from .quant_scorer import QuantScorer, QuantScoreResult, format_quant_score_for_prompt
from .hybrid_scorer import HybridScorer, HybridScoreResult, run_hybrid_scoring_pipeline
from .factor_analyzer import FactorAnalyzer, run_weekly_factor_analysis
from .schema import (
    create_hybrid_scoring_tables,
    get_default_factor_weights,
    get_confidence_level,
    get_confidence_weight,
)

__all__ = [
    # 주요 클래스
    'QuantScorer',
    'HybridScorer', 
    'FactorAnalyzer',
    
    # 결과 데이터 클래스
    'QuantScoreResult',
    'HybridScoreResult',
    
    # 파이프라인 함수
    'run_hybrid_scoring_pipeline',
    'run_weekly_factor_analysis',
    
    # 유틸리티 함수
    'format_quant_score_for_prompt',
    'create_hybrid_scoring_tables',
    'get_default_factor_weights',
    'get_confidence_level',
    'get_confidence_weight',
]

__version__ = '5.0.0'

