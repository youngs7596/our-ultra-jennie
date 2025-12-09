"""
shared/hybrid_scoring/factor_constants.py

FactorAnalyzer에서 사용하는 상수/설정 값을 모아 분리합니다.
"""

# 분석 기간 설정
DEFAULT_LOOKBACK_YEARS = 2
RECENT_MONTHS = 3
FORWARD_DAYS = [5, 10, 20]

# 팩터 정의
FACTOR_DEFINITIONS = {
    'momentum_6m': {'name': '6개월 모멘텀', 'calc_func': '_calc_momentum_6m'},
    'momentum_1m': {'name': '1개월 모멘텀', 'calc_func': '_calc_momentum_1m'},
    'value_per': {'name': 'PER (저평가)', 'calc_func': '_calc_per_factor'},
    'value_pbr': {'name': 'PBR (저평가)', 'calc_func': '_calc_pbr_factor'},
    'quality_roe': {'name': 'ROE (수익성)', 'calc_func': '_calc_roe_factor'},
    'technical_rsi_oversold': {'name': 'RSI 과매도', 'calc_func': '_calc_rsi_oversold'},
    'supply_foreign_buy': {'name': '외국인 순매수', 'calc_func': '_calc_foreign_buy'},
}

# 뉴스 카테고리 정의
NEWS_CATEGORIES = [
    '실적', '수주', '신사업', 'M&A', '배당', '규제', '경영',
]

# 시장 국면 정의
MARKET_REGIME_THRESHOLDS = {
    'BULL': 0.10,    # 6개월 수익률 +10% 이상
    'BEAR': -0.10,   # 6개월 수익률 -10% 이하
    'SIDEWAYS': 0.0, # 그 사이
}

# 시가총액 기준 그룹 분류
STOCK_GROUP_THRESHOLDS = {
    'LARGE': 10_000_000_000_000,  # 10조 이상
    'MID': 1_000_000_000_000,     # 1조 이상
    'SMALL': 0,                   # 그 외
}
