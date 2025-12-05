"""
shared/hybrid_scoring - Ultra Jennie 하이브리드 스코어링 시스템
=============================================================

이 패키지는 정량적 팩터 분석과 LLM 정성 분석을 결합한 
하이브리드 스코어링 시스템을 제공합니다.

핵심 철학:
---------
"AI의 감(LLM)을 믿기 전에, 통계(Data)로 검증하고, 비용(Cost)을 통제한다."

- 정량 점수: LLM 독립적으로 계산 (비용 $0, 검증 가능)
- 하이브리드: 정량 60% + LLM 정성 40% 결합
- 팩터 분석: 주간 배치로 IC/IR 통계 업데이트

구성 모듈:
---------
1. quant_scorer: 정량 점수 계산 엔진
   - 모멘텀, 가치, 품질, 기술적, 수급 점수
   
2. hybrid_scorer: 하이브리드 점수 결합
   - 정량 + LLM 점수 가중 결합
   
3. factor_analyzer: 팩터 분석 배치
   - IC (Information Coefficient)
   - IR (Information Ratio)
   - 조건부 승률 분석
   
4. competitor_analyzer: 경쟁사 수혜 분석
   - 경쟁사 악재 시 반사이익 분석
   - 섹터별 디커플링 통계

사용 예시:
---------
>>> from shared.hybrid_scoring import QuantScorer, HybridScorer
>>>
>>> # 정량 점수 계산
>>> scorer = QuantScorer(db_conn, market_regime='BULL')
>>> result = scorer.calculate_total_quant_score(stock_code='005930', ...)
>>>
>>> # 하이브리드 점수 결합
>>> hybrid = HybridScorer(market_regime='BULL')
>>> final = hybrid.calculate_hybrid_score(result, llm_score=75)
"""

from .quant_scorer import QuantScorer, QuantScoreResult, format_quant_score_for_prompt
from .hybrid_scorer import HybridScorer, HybridScoreResult, run_hybrid_scoring_pipeline
from .factor_analyzer import FactorAnalyzer, run_weekly_factor_analysis
from .competitor_analyzer import (
    CompetitorAnalyzer,
    CompetitorBenefitReport,
    BenefitAnalysis,
    analyze_competitor_benefit,
    get_all_sectors,
)
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
    'CompetitorAnalyzer',  # v1.0
    
    # 결과 데이터 클래스
    'QuantScoreResult',
    'HybridScoreResult',
    'CompetitorBenefitReport',  # v1.0
    'BenefitAnalysis',  # v1.0
    
    # 파이프라인 함수
    'run_hybrid_scoring_pipeline',
    'run_weekly_factor_analysis',
    'analyze_competitor_benefit',  # v1.0
    
    # 유틸리티 함수
    'format_quant_score_for_prompt',
    'create_hybrid_scoring_tables',
    'get_default_factor_weights',
    'get_confidence_level',
    'get_confidence_weight',
    'get_all_sectors',  # v1.0
]

__version__ = '1.0.0'

