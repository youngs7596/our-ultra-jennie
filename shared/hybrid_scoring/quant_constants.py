"""
shared/hybrid_scoring/quant_constants.py

QuantScorer의 가중치/상수/섹터 매핑을 분리해 유지관리와 후속 제거를 용이하게 합니다.
"""

from enum import Enum


class StrategyMode(Enum):
    SHORT_TERM = "SHORT_TERM"
    LONG_TERM = "LONG_TERM"
    DUAL = "DUAL"


# 기본값
DEFAULT_FILTER_CUTOFF = 0.5
DEFAULT_HOLDING_DAYS = 5

# 섹터별 RSI 가중치
SECTOR_RSI_MULTIPLIER = {
    "조선운송": 1.3,
    "금융": 1.25,
    "자유소비재": 1.1,
    "정보통신": 1.1,
    "에너지화학": 1.05,
    "etc": 1.05,
    "미분류": 1.05,
    "필수소비재": 0.9,
    "건설기계": 0.7,
}

# 장기 호재 뉴스 카테고리
NEWS_LONG_TERM_POSITIVE = {"수주", "실적", "배당"}

# 단기/장기 가중치
SHORT_TERM_WEIGHTS = {
    "rsi_compound": 0.35,
    "technical_rsi": 0.15,
    "supply_demand": 0.20,
    "quality_roe": 0.10,
    "momentum": 0.05,
    "value": 0.05,
    "news": 0.05,
    "llm_qualitative": 0.05,
}

LONG_TERM_WEIGHTS = {
    "quality_roe": 0.30,
    "news_long_term": 0.25,
    "technical_rsi": 0.15,
    "value": 0.10,
    "supply_demand": 0.10,
    "momentum": 0.03,
    "llm_qualitative": 0.07,
}

# 점수 임계값
GRADE_THRESHOLDS = {
    "S": 90,
    "A": 80,
    "B": 70,
    "C": 60,
}

# 모멘텀/품질/가치 랭킹 컷오프 (예시)
RANK_CUTOFF = {
    "momentum": 0.3,
    "value": 0.4,
    "quality": 0.3,
}

# 뉴스 카테고리별 시간축 효과 (팩터 분석 결과)
NEWS_TIME_EFFECT = {
    "수주": {"d5_win_rate": 0.437, "d60_win_rate": 0.727, "d60_return": 0.1936},
    "실적": {"d5_win_rate": 0.484, "d60_win_rate": 0.648, "d60_return": 0.1403},
    "배당": {"d5_win_rate": 0.376, "d60_win_rate": 0.540, "d60_return": 0.0998},
    "신사업": {"d5_win_rate": 0.469, "d60_win_rate": 0.571, "d60_return": 0.0636},
    "M&A": {"d5_win_rate": 0.483, "d60_win_rate": 0.571, "d60_return": 0.0795},
}
