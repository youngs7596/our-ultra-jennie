# Version: v4.0
# 작업 LLM: Claude Sonnet 4.5, Claude Opus 4.5
"""
[v4.0] SQLAlchemy ORM 모델 정의
- CONFIG_VALUE 컬럼 TEXT로 변경 (Claude Opus 4.5)
- 경쟁사 수혜 분석 시스템 테이블 추가 (Claude Opus 4.5)
  - INDUSTRY_COMPETITORS: 산업/경쟁사 매핑
  - EVENT_IMPACT_RULES: 이벤트 영향 규칙
  - SECTOR_RELATION_STATS: 섹터 관계 통계
"""

import os
from datetime import datetime

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    ForeignKey,
    Float,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()

TRADING_MODE = os.getenv("TRADING_MODE", "REAL").upper()

_MOCK_TABLES = {
    "PORTFOLIO",
    "TRADELOG",
    "NEWS_SENTIMENT",
    "AGENT_COMMANDS",
}

# Oracle은 대소문자를 구분하지 않으므로, 모든 테이블 이름을 대문자로 통일합니다.
# MariaDB는 OS에 따라 다르지만, 대문자로 통일하는 것이 안전합니다.


def resolve_table_name(base_name: str) -> str:
    """
    MOCK 모드일 때는 일부 테이블에 _MOCK 접미사를 붙입니다.
    Oracle은 대소문자를 구분하지 않으므로 대문자로 통일합니다.
    """
    upper_name = base_name.upper()
    if TRADING_MODE == "MOCK" and upper_name in _MOCK_TABLES:
        return f"{upper_name}_MOCK"
    return upper_name


class WatchList(Base):
    __tablename__ = resolve_table_name("WATCHLIST")
    __table_args__ = {"extend_existing": True}

    stock_code = Column("STOCK_CODE", String(20), primary_key=True)
    stock_name = Column("STOCK_NAME", String(120))
    filter_reason = Column("FILTER_REASON", String(255), nullable=True)
    recent_low_price = Column("RECENT_LOW_PRICE", Float, nullable=True)
    added_at = Column("ADDED_AT", DateTime, nullable=True)
    created_at = Column("CREATED_AT", DateTime, server_default=func.now())
    is_tradable = Column("IS_TRADABLE", Integer, default=1)
    per = Column("PER", Float, nullable=True)
    pbr = Column("PBR", Float, nullable=True)
    market_cap = Column("MARKET_CAP", Numeric(20, 0))
    updated_at = Column("UPDATED_AT", DateTime, onupdate=func.now())
    roe = Column("ROE", Float, nullable=True)
    sales_growth = Column("SALES_GROWTH", Float, nullable=True)
    eps_growth = Column("EPS_GROWTH", Float, nullable=True)
    financial_updated_at = Column("FINANCIAL_UPDATED_AT", DateTime, nullable=True)
    llm_score = Column("LLM_SCORE", Float, default=0)
    llm_reason = Column("LLM_REASON", Text, nullable=True)
    llm_updated_at = Column("LLM_UPDATED_AT", DateTime, nullable=True)


class Portfolio(Base):
    __tablename__ = resolve_table_name("PORTFOLIO")
    __table_args__ = {"extend_existing": True}

    id = Column("ID", Integer, primary_key=True)
    stock_code = Column("STOCK_CODE", String(20))
    stock_name = Column("STOCK_NAME", String(120))
    quantity = Column("QUANTITY", Integer)
    average_buy_price = Column("AVERAGE_BUY_PRICE", Float)
    total_buy_amount = Column("TOTAL_BUY_AMOUNT", Float)
    current_high_price = Column("CURRENT_HIGH_PRICE", Float)
    status = Column("STATUS", String(20))
    sell_state = Column("SELL_STATE", String(20))
    stop_loss_price = Column("STOP_LOSS_PRICE", Float)
    created_at = Column("CREATED_AT", DateTime, server_default=func.now())
    updated_at = Column("UPDATED_AT", DateTime, onupdate=func.now())


class TradeLog(Base):
    __tablename__ = resolve_table_name("TRADELOG")
    __table_args__ = {"extend_existing": True}

    log_id = Column("LOG_ID", Integer, primary_key=True)
    portfolio_id = Column("PORTFOLIO_ID", Integer, ForeignKey(resolve_table_name("PORTFOLIO") + ".ID"))
    stock_code = Column("STOCK_CODE", String(20), index=True)
    trade_type = Column("TRADE_TYPE", String(10))
    quantity = Column("QUANTITY", Integer)
    price = Column("PRICE", Float)
    reason = Column("REASON", Text, nullable=True)
    trade_timestamp = Column("TRADE_TIMESTAMP", DateTime, default=datetime.utcnow)
    strategy_signal = Column("STRATEGY_SIGNAL", String(100))
    key_metrics_json = Column("KEY_METRICS_JSON", Text)
    market_context_json = Column("MARKET_CONTEXT_JSON", Text)


class StockDailyPrice(Base):
    __tablename__ = resolve_table_name("STOCK_DAILY_PRICES_3Y")
    __table_args__ = {"extend_existing": True}

    stock_code = Column("STOCK_CODE", String(20), primary_key=True)
    price_date = Column("PRICE_DATE", DateTime, primary_key=True)
    open_price = Column("OPEN_PRICE", Float, nullable=True)
    close_price = Column("CLOSE_PRICE", Float)
    high_price = Column("HIGH_PRICE", Float)
    low_price = Column("LOW_PRICE", Float)
    volume = Column("VOLUME", Float)
    created_at = Column("CREATED_AT", DateTime, server_default=func.now())


# --- 아래부터 새롭게 추가된 모델들 ---

class Config(Base):
    __tablename__ = resolve_table_name("CONFIG")
    __table_args__ = {"extend_existing": True}

    config_key = Column("CONFIG_KEY", String(100), primary_key=True)  # 키 길이 확장
    config_value = Column("CONFIG_VALUE", Text, nullable=False)  # TEXT로 변경 (큰 JSON 저장용)
    description = Column("DESCRIPTION", Text, nullable=True)
    last_updated = Column("LAST_UPDATED", DateTime, server_default=func.now(), onupdate=func.now())


class BacktestTradeLog(Base):
    __tablename__ = resolve_table_name("BACKTEST_TRADELOG")
    __table_args__ = {"extend_existing": True}

    log_id = Column("LOG_ID", Integer, primary_key=True)
    trade_date = Column("TRADE_DATE", Date, nullable=False)
    stock_code = Column("STOCK_CODE", String(16), nullable=False)
    stock_name = Column("STOCK_NAME", String(128), nullable=True)
    trade_type = Column("TRADE_TYPE", String(8), nullable=False)
    quantity = Column("QUANTITY", Integer, nullable=True)
    price = Column("PRICE", Float, nullable=True)
    reason = Column("REASON", String(2000), nullable=True)
    strategy_signal = Column("STRATEGY_SIGNAL", String(64), nullable=True)
    key_metrics_json = Column("KEY_METRICS_JSON", Text, nullable=True)
    regime = Column("REGIME", String(32), nullable=True)
    created_at = Column("CREATED_AT", DateTime, server_default=func.now())


class AgentCommands(Base):
    __tablename__ = resolve_table_name("AGENT_COMMANDS")
    __table_args__ = {"extend_existing": True}

    command_id = Column("COMMAND_ID", Integer, primary_key=True)
    command_type = Column("COMMAND_TYPE", String(50), nullable=False)
    payload = Column("PAYLOAD", Text, nullable=True)
    status = Column("STATUS", String(20), default='PENDING')
    priority = Column("PRIORITY", Integer, default=5)
    requested_by = Column("REQUESTED_BY", String(100), nullable=True)
    created_at = Column("CREATED_AT", DateTime, server_default=func.now())
    processing_start = Column("PROCESSING_START", DateTime, nullable=True)
    processed_at = Column("PROCESSED_AT", DateTime, nullable=True)
    result_msg = Column("RESULT_MSG", Text, nullable=True)
    order_no = Column("ORDER_NO", String(50), nullable=True)
    retry_count = Column("RETRY_COUNT", Integer, default=0)


class OptimizationHistory(Base):
    __tablename__ = resolve_table_name("OPTIMIZATION_HISTORY")
    __table_args__ = {"extend_existing": True}

    optimization_id = Column("OPTIMIZATION_ID", Integer, primary_key=True)
    executed_at = Column("EXECUTED_AT", DateTime, server_default=func.now(), nullable=False)
    current_mdd = Column("CURRENT_MDD", Float, nullable=True)
    current_return = Column("CURRENT_RETURN", Float, nullable=True)
    new_mdd = Column("NEW_MDD", Float, nullable=True)
    new_return = Column("NEW_RETURN", Float, nullable=True)
    current_params = Column("CURRENT_PARAMS", Text, nullable=True)
    new_params = Column("NEW_PARAMS", Text, nullable=True)
    ai_decision = Column("AI_DECISION", String(20), nullable=False)
    ai_reasoning = Column("AI_REASONING", Text, nullable=True)
    ai_confidence = Column("AI_CONFIDENCE", Float, nullable=True)
    market_summary = Column("MARKET_SUMMARY", String(500), nullable=True)
    backtest_period = Column("BACKTEST_PERIOD", Integer, default=90)
    is_applied = Column("IS_APPLIED", String(1), default='N', nullable=False)
    applied_at = Column("APPLIED_AT", DateTime, nullable=True)


class NewsSentiment(Base):
    __tablename__ = resolve_table_name("NEWS_SENTIMENT")
    __table_args__ = {"extend_existing": True}

    id = Column("ID", Integer, primary_key=True)
    stock_code = Column("STOCK_CODE", String(20), nullable=False)
    news_title = Column("NEWS_TITLE", String(1000), nullable=True)
    sentiment_score = Column("SENTIMENT_SCORE", Float, default=50)
    sentiment_reason = Column("SENTIMENT_REASON", String(2000), nullable=True)
    source_url = Column("SOURCE_URL", String(2000), nullable=True, unique=True)
    published_at = Column("PUBLISHED_AT", DateTime, nullable=True)
    created_at = Column("CREATED_AT", DateTime, server_default=func.now())


class WatchlistHistory(Base):
    __tablename__ = resolve_table_name("WATCHLIST_HISTORY")
    __table_args__ = {"extend_existing": True}

    snapshot_date = Column("SNAPSHOT_DATE", Date, primary_key=True)
    stock_code = Column("STOCK_CODE", String(16), primary_key=True)
    stock_name = Column("STOCK_NAME", String(128), nullable=True)
    is_tradable = Column("IS_TRADABLE", Integer, default=1)
    llm_score = Column("LLM_SCORE", Float, nullable=True)
    llm_reason = Column("LLM_REASON", String(4000), nullable=True)
    created_at = Column("CREATED_AT", DateTime, server_default=func.now())


class RagCache(Base):
    __tablename__ = resolve_table_name("RAG_CACHE")
    __table_args__ = {"extend_existing": True}

    stock_code = Column("STOCK_CODE", String(20), primary_key=True)
    rag_context = Column("RAG_CONTEXT", Text, nullable=True)
    last_updated = Column("LAST_UPDATED", DateTime, server_default=func.now())


class FinancialData(Base):
    __tablename__ = resolve_table_name("FINANCIAL_DATA")
    __table_args__ = {"extend_existing": True}

    stock_code = Column("STOCK_CODE", String(16), primary_key=True)
    report_date = Column("REPORT_DATE", Date, primary_key=True)
    report_type = Column("REPORT_TYPE", String(16), primary_key=True)
    sales = Column("SALES", Float, nullable=True)
    operating_profit = Column("OPERATING_PROFIT", Float, nullable=True)
    net_income = Column("NET_INCOME", Float, nullable=True)
    total_assets = Column("TOTAL_ASSETS", Float, nullable=True)
    total_liabilities = Column("TOTAL_LIABILITIES", Float, nullable=True)
    total_equity = Column("TOTAL_EQUITY", Float, nullable=True)
    shares_outstanding = Column("SHARES_OUTSTANDING", Float, nullable=True)
    eps = Column("EPS", Float, nullable=True)
    sales_growth = Column("SALES_GROWTH", Float, nullable=True)
    eps_growth = Column("EPS_GROWTH", Float, nullable=True)
    created_at = Column("CREATED_AT", DateTime, server_default=func.now())


# ============================================================================
# [v4.0] 경쟁사 수혜 분석 시스템 모델 (Claude Opus 4.5)
# ============================================================================

class IndustryCompetitors(Base):
    """
    산업/경쟁사 매핑 테이블
    - 동일 섹터 내 경쟁 관계를 정의
    - 시장 점유율 및 상장 여부 관리
    """
    __tablename__ = resolve_table_name("INDUSTRY_COMPETITORS")
    __table_args__ = {"extend_existing": True}

    id = Column("ID", Integer, primary_key=True)
    sector_code = Column("SECTOR_CODE", String(20), nullable=False, index=True)
    sector_name = Column("SECTOR_NAME", String(100), nullable=False)
    stock_code = Column("STOCK_CODE", String(20), nullable=False, index=True)
    stock_name = Column("STOCK_NAME", String(120), nullable=False)
    market_share = Column("MARKET_SHARE", Float, nullable=True)  # 시장 점유율 (%)
    rank_in_sector = Column("RANK_IN_SECTOR", Integer, nullable=True)  # 섹터 내 순위
    is_leader = Column("IS_LEADER", Integer, default=0)  # 1=리더, 0=팔로워
    exchange = Column("EXCHANGE", String(20), default='KRX')  # KRX, NYSE, NASDAQ 등
    is_active = Column("IS_ACTIVE", Integer, default=1)
    created_at = Column("CREATED_AT", DateTime, server_default=func.now())
    updated_at = Column("UPDATED_AT", DateTime, onupdate=func.now())


class EventImpactRules(Base):
    """
    이벤트 영향 규칙 테이블
    - 이벤트 유형별 당사자/경쟁사 영향도 정의
    - 효과 지속 기간 설정
    """
    __tablename__ = resolve_table_name("EVENT_IMPACT_RULES")
    __table_args__ = {"extend_existing": True}

    id = Column("ID", Integer, primary_key=True)
    event_type = Column("EVENT_TYPE", String(50), nullable=False, unique=True)  # 보안사고, 리콜, 오너리스크 등
    event_keywords = Column("EVENT_KEYWORDS", Text, nullable=True)  # JSON: ["해킹", "유출", "개인정보"]
    
    # 영향도 점수 (-100 ~ +100)
    impact_on_self = Column("IMPACT_ON_SELF", Integer, default=-10)  # 당사자 영향
    impact_on_competitor = Column("IMPACT_ON_COMPETITOR", Integer, default=5)  # 경쟁사 수혜
    impact_on_supplier = Column("IMPACT_ON_SUPPLIER", Integer, default=-3)  # 협력사 영향
    
    # 효과 지속 기간 (일)
    effect_duration_days = Column("EFFECT_DURATION_DAYS", Integer, default=20)
    peak_effect_day = Column("PEAK_EFFECT_DAY", Integer, default=3)  # 영향 최대인 날
    
    # 신뢰도 및 메타데이터
    confidence_level = Column("CONFIDENCE_LEVEL", String(10), default='MID')  # HIGH, MID, LOW
    sample_count = Column("SAMPLE_COUNT", Integer, default=0)  # 과거 사례 수
    description = Column("DESCRIPTION", Text, nullable=True)
    
    is_active = Column("IS_ACTIVE", Integer, default=1)
    created_at = Column("CREATED_AT", DateTime, server_default=func.now())
    updated_at = Column("UPDATED_AT", DateTime, onupdate=func.now())


class SectorRelationStats(Base):
    """
    섹터 관계 통계 테이블 (디커플링 분석)
    - 1등 기업 급락 시 2등 기업 반응 통계
    - 과거 데이터 기반 적중률 관리
    """
    __tablename__ = resolve_table_name("SECTOR_RELATION_STATS")
    __table_args__ = {"extend_existing": True}

    id = Column("ID", Integer, primary_key=True)
    sector_code = Column("SECTOR_CODE", String(20), nullable=False, index=True)
    sector_name = Column("SECTOR_NAME", String(100), nullable=False)
    
    # 리더/팔로워 종목
    leader_stock_code = Column("LEADER_STOCK_CODE", String(20), nullable=False)
    leader_stock_name = Column("LEADER_STOCK_NAME", String(120), nullable=True)
    follower_stock_code = Column("FOLLOWER_STOCK_CODE", String(20), nullable=False)
    follower_stock_name = Column("FOLLOWER_STOCK_NAME", String(120), nullable=True)
    
    # 디커플링 통계
    decoupling_rate = Column("DECOUPLING_RATE", Float, nullable=True)  # 0.62 = 62%
    avg_benefit_return = Column("AVG_BENEFIT_RETURN", Float, nullable=True)  # 0.023 = 2.3%
    avg_leader_drop = Column("AVG_LEADER_DROP", Float, nullable=True)  # -0.05 = -5%
    
    # 표본 및 신뢰도
    sample_count = Column("SAMPLE_COUNT", Integer, default=0)
    lookback_days = Column("LOOKBACK_DAYS", Integer, default=730)  # 분석 기간
    confidence = Column("CONFIDENCE", String(10), default='MID')  # HIGH, MID, LOW
    
    # 권장 전략
    recommended_holding_days = Column("RECOMMENDED_HOLDING_DAYS", Integer, default=20)
    stop_loss_pct = Column("STOP_LOSS_PCT", Float, default=-0.03)  # -3%
    take_profit_pct = Column("TAKE_PROFIT_PCT", Float, default=0.08)  # +8%
    
    last_calculated = Column("LAST_CALCULATED", DateTime, server_default=func.now())
    created_at = Column("CREATED_AT", DateTime, server_default=func.now())
    updated_at = Column("UPDATED_AT", DateTime, onupdate=func.now())


class CompetitorBenefitEvents(Base):
    """
    경쟁사 수혜 이벤트 테이블
    - 악재 발생 기업과 수혜 기업 매핑
    - 실시간 뉴스 모니터링 결과 저장
    """
    __tablename__ = resolve_table_name("COMPETITOR_BENEFIT_EVENTS")
    __table_args__ = {"extend_existing": True}

    id = Column("ID", Integer, primary_key=True)
    
    # 악재 발생 기업
    affected_stock_code = Column("AFFECTED_STOCK_CODE", String(20), nullable=False, index=True)
    affected_stock_name = Column("AFFECTED_STOCK_NAME", String(120), nullable=True)
    
    # 이벤트 정보
    event_type = Column("EVENT_TYPE", String(50), nullable=False)  # 보안사고, 리콜 등
    event_title = Column("EVENT_TITLE", String(1000), nullable=True)
    event_severity = Column("EVENT_SEVERITY", Integer, default=0)  # 악재 심각도 (음수)
    source_url = Column("SOURCE_URL", String(2000), nullable=True)
    
    # 수혜 기업
    beneficiary_stock_code = Column("BENEFICIARY_STOCK_CODE", String(20), nullable=False, index=True)
    beneficiary_stock_name = Column("BENEFICIARY_STOCK_NAME", String(120), nullable=True)
    benefit_score = Column("BENEFIT_SCORE", Integer, default=0)  # 수혜 점수 (양수)
    
    # 섹터 정보
    sector_code = Column("SECTOR_CODE", String(20), nullable=True)
    sector_name = Column("SECTOR_NAME", String(100), nullable=True)
    
    # 상태 관리
    status = Column("STATUS", String(20), default='ACTIVE')  # ACTIVE, EXPIRED, REALIZED
    expires_at = Column("EXPIRES_AT", DateTime, nullable=True)  # 효과 만료 시점
    
    # 결과 추적 (백테스트용)
    actual_return = Column("ACTUAL_RETURN", Float, nullable=True)  # 실제 수익률
    is_success = Column("IS_SUCCESS", Integer, nullable=True)  # 1=성공, 0=실패
    
    detected_at = Column("DETECTED_AT", DateTime, server_default=func.now())
    created_at = Column("CREATED_AT", DateTime, server_default=func.now())
    updated_at = Column("UPDATED_AT", DateTime, onupdate=func.now())


# =============================================================================
# [v4.1] Hybrid Scoring 모듈용 ORM 모델 (factor_analyzer.py 리팩토링)
# =============================================================================

class StockMaster(Base):
    """
    종목 마스터 테이블
    - 종목 기본 정보 (시가총액, 섹터 등)
    """
    __tablename__ = "STOCK_MASTER"
    __table_args__ = {"extend_existing": True}

    stock_code = Column("STOCK_CODE", String(20), primary_key=True)
    stock_name = Column("STOCK_NAME", String(120), nullable=True)
    market_cap = Column("MARKET_CAP", Numeric(20, 0), nullable=True)
    sector_kospi200 = Column("SECTOR_KOSPI200", String(50), nullable=True)
    industry_code = Column("INDUSTRY_CODE", String(20), nullable=True)
    industry_name = Column("INDUSTRY_NAME", String(100), nullable=True)
    is_kospi200 = Column("IS_KOSPI200", Integer, default=0)
    is_active = Column("IS_ACTIVE", Integer, default=1)
    created_at = Column("CREATED_AT", DateTime, server_default=func.now())
    updated_at = Column("UPDATED_AT", DateTime, onupdate=func.now())


class FinancialMetricsQuarterly(Base):
    """
    분기별 재무 지표 테이블
    - PER, PBR, ROE 등 재무 데이터
    """
    __tablename__ = "FINANCIAL_METRICS_QUARTERLY"
    __table_args__ = {"extend_existing": True}

    id = Column("ID", Integer, primary_key=True)
    stock_code = Column("STOCK_CODE", String(20), nullable=False, index=True)
    quarter_date = Column("QUARTER_DATE", Date, nullable=False)
    per = Column("PER", Float, nullable=True)
    pbr = Column("PBR", Float, nullable=True)
    roe = Column("ROE", Float, nullable=True)
    eps = Column("EPS", Float, nullable=True)
    bps = Column("BPS", Float, nullable=True)
    sales_growth = Column("SALES_GROWTH", Float, nullable=True)
    operating_margin = Column("OPERATING_MARGIN", Float, nullable=True)
    created_at = Column("CREATED_AT", DateTime, server_default=func.now())
    updated_at = Column("UPDATED_AT", DateTime, onupdate=func.now())


class StockInvestorTrading(Base):
    """
    투자자별 매매 동향 테이블
    - 외국인/기관 순매수 정보
    """
    __tablename__ = "STOCK_INVESTOR_TRADING"
    __table_args__ = {"extend_existing": True}

    id = Column("ID", Integer, primary_key=True)
    stock_code = Column("STOCK_CODE", String(20), nullable=False, index=True)
    trade_date = Column("TRADE_DATE", Date, nullable=False)
    foreign_net_buy = Column("FOREIGN_NET_BUY", Numeric(20, 0), nullable=True)  # 외국인 순매수 금액
    institution_net_buy = Column("INSTITUTION_NET_BUY", Numeric(20, 0), nullable=True)  # 기관 순매수 금액
    individual_net_buy = Column("INDIVIDUAL_NET_BUY", Numeric(20, 0), nullable=True)  # 개인 순매수 금액
    foreign_holding_ratio = Column("FOREIGN_HOLDING_RATIO", Float, nullable=True)  # 외국인 보유 비율
    created_at = Column("CREATED_AT", DateTime, server_default=func.now())


class StockNewsSentiment(Base):
    """
    종목별 뉴스 감성 점수 테이블
    - 뉴스 카테고리별 감성 점수
    """
    __tablename__ = "STOCK_NEWS_SENTIMENT"
    __table_args__ = {"extend_existing": True}

    id = Column("ID", Integer, primary_key=True)
    stock_code = Column("STOCK_CODE", String(20), nullable=False, index=True)
    news_date = Column("NEWS_DATE", Date, nullable=False)
    sentiment_score = Column("SENTIMENT_SCORE", Float, nullable=True)  # -100 ~ +100
    category = Column("CATEGORY", String(50), nullable=True)  # 실적, 수주, 규제 등
    news_count = Column("NEWS_COUNT", Integer, default=1)
    created_at = Column("CREATED_AT", DateTime, server_default=func.now())


class StockDisclosures(Base):
    """
    종목별 공시 정보 테이블
    """
    __tablename__ = "STOCK_DISCLOSURES"
    __table_args__ = {"extend_existing": True}

    id = Column("ID", Integer, primary_key=True)
    stock_code = Column("STOCK_CODE", String(20), nullable=False, index=True)
    disclosure_date = Column("DISCLOSURE_DATE", Date, nullable=False)
    category = Column("CATEGORY", String(50), nullable=True)
    title = Column("TITLE", String(500), nullable=True)
    created_at = Column("CREATED_AT", DateTime, server_default=func.now())


class FactorMetadata(Base):
    """
    팩터별 예측력 메타데이터 테이블
    - IC, IR, 가중치 등
    - 주 1회 배치 작업으로 업데이트
    """
    __tablename__ = "FACTOR_METADATA"
    __table_args__ = {"extend_existing": True}

    id = Column("ID", Integer, primary_key=True)
    factor_key = Column("FACTOR_KEY", String(50), nullable=False)
    factor_name = Column("FACTOR_NAME", String(100), nullable=True)
    market_regime = Column("MARKET_REGIME", String(20), default='ALL')
    
    # 예측력 지표
    ic_mean = Column("IC_MEAN", Float, nullable=True)  # Information Coefficient 평균
    ic_std = Column("IC_STD", Float, nullable=True)  # IC 표준편차
    ir = Column("IR", Float, nullable=True)  # Information Ratio
    hit_rate = Column("HIT_RATE", Float, nullable=True)  # 적중률
    
    # 추천 가중치
    recommended_weight = Column("RECOMMENDED_WEIGHT", Float, default=0.10)
    
    # 표본 정보
    sample_count = Column("SAMPLE_COUNT", Integer, default=0)
    analysis_start_date = Column("ANALYSIS_START_DATE", Date, nullable=True)
    analysis_end_date = Column("ANALYSIS_END_DATE", Date, nullable=True)
    
    created_at = Column("CREATED_AT", DateTime, server_default=func.now())
    updated_at = Column("UPDATED_AT", DateTime, onupdate=func.now())


class FactorPerformance(Base):
    """
    종목/섹터별 조건부 승률 통계 테이블
    - "삼성전자 + 외국인 순매수 + 뉴스점수 70↑" → 승률 80%
    """
    __tablename__ = "FACTOR_PERFORMANCE"
    __table_args__ = {"extend_existing": True}

    id = Column("ID", Integer, primary_key=True)
    
    # 대상 식별
    target_type = Column("TARGET_TYPE", String(20), nullable=False)  # STOCK, SECTOR, ALL
    target_code = Column("TARGET_CODE", String(20), nullable=False)
    target_name = Column("TARGET_NAME", String(100), nullable=True)
    
    # 조건 정의
    condition_key = Column("CONDITION_KEY", String(100), nullable=False)
    condition_desc = Column("CONDITION_DESC", String(200), nullable=True)
    
    # 성과 통계
    win_rate = Column("WIN_RATE", Float, nullable=True)
    avg_return = Column("AVG_RETURN", Float, nullable=True)
    median_return = Column("MEDIAN_RETURN", Float, nullable=True)
    max_return = Column("MAX_RETURN", Float, nullable=True)
    min_return = Column("MIN_RETURN", Float, nullable=True)
    
    # 측정 기간
    holding_days = Column("HOLDING_DAYS", Integer, default=5)
    
    # 신뢰도
    sample_count = Column("SAMPLE_COUNT", Integer, default=0)
    confidence_level = Column("CONFIDENCE_LEVEL", String(10), default='LOW')
    
    # 최근성 가중치
    recent_win_rate = Column("RECENT_WIN_RATE", Float, nullable=True)
    recent_sample_count = Column("RECENT_SAMPLE_COUNT", Integer, nullable=True)
    
    analysis_date = Column("ANALYSIS_DATE", Date, nullable=True)
    created_at = Column("CREATED_AT", DateTime, server_default=func.now())
    updated_at = Column("UPDATED_AT", DateTime, onupdate=func.now())


class NewsFactorStats(Base):
    """
    뉴스 카테고리별 영향도 통계 테이블
    - "수주 뉴스 발생 시 평균 +3.2%, 승률 78%"
    """
    __tablename__ = "NEWS_FACTOR_STATS"
    __table_args__ = {"extend_existing": True}

    id = Column("ID", Integer, primary_key=True)
    
    # 대상 식별
    target_type = Column("TARGET_TYPE", String(20), nullable=False)  # STOCK, SECTOR, ALL
    target_code = Column("TARGET_CODE", String(20), nullable=False)
    
    # 뉴스 카테고리
    news_category = Column("NEWS_CATEGORY", String(50), nullable=False)
    sentiment = Column("SENTIMENT", String(10), default='POSITIVE')
    
    # 성과 통계
    win_rate = Column("WIN_RATE", Float, nullable=True)
    avg_excess_return = Column("AVG_EXCESS_RETURN", Float, nullable=True)
    avg_absolute_return = Column("AVG_ABSOLUTE_RETURN", Float, nullable=True)
    
    # 측정 기간별 통계
    return_d1 = Column("RETURN_D1", Float, nullable=True)
    return_d5 = Column("RETURN_D5", Float, nullable=True)
    return_d20 = Column("RETURN_D20", Float, nullable=True)
    
    win_rate_d1 = Column("WIN_RATE_D1", Float, nullable=True)
    win_rate_d5 = Column("WIN_RATE_D5", Float, nullable=True)
    win_rate_d20 = Column("WIN_RATE_D20", Float, nullable=True)
    
    # 신뢰도
    sample_count = Column("SAMPLE_COUNT", Integer, default=0)
    confidence_level = Column("CONFIDENCE_LEVEL", String(10), default='LOW')
    
    analysis_date = Column("ANALYSIS_DATE", Date, nullable=True)
    created_at = Column("CREATED_AT", DateTime, server_default=func.now())
    updated_at = Column("UPDATED_AT", DateTime, onupdate=func.now())


class DailyQuantScore(Base):
    """
    일별 정량 점수 기록 테이블 (역추적/백테스트용)
    - Scout가 왜 이 종목을 뽑았는지 추적 가능
    """
    __tablename__ = "DAILY_QUANT_SCORE"
    __table_args__ = {"extend_existing": True}

    id = Column("ID", Integer, primary_key=True)
    
    score_date = Column("SCORE_DATE", Date, nullable=False)
    stock_code = Column("STOCK_CODE", String(20), nullable=False, index=True)
    stock_name = Column("STOCK_NAME", String(100), nullable=True)
    
    # 정량 점수 (100점 만점)
    total_quant_score = Column("TOTAL_QUANT_SCORE", Float, nullable=True)
    
    # 팩터별 점수
    momentum_score = Column("MOMENTUM_SCORE", Float, nullable=True)
    quality_score = Column("QUALITY_SCORE", Float, nullable=True)
    value_score = Column("VALUE_SCORE", Float, nullable=True)
    technical_score = Column("TECHNICAL_SCORE", Float, nullable=True)
    news_stat_score = Column("NEWS_STAT_SCORE", Float, nullable=True)
    supply_demand_score = Column("SUPPLY_DEMAND_SCORE", Float, nullable=True)
    
    # 조건부 승률 정보
    matched_condition = Column("MATCHED_CONDITION", String(200), nullable=True)
    condition_win_rate = Column("CONDITION_WIN_RATE", Float, nullable=True)
    condition_sample_count = Column("CONDITION_SAMPLE_COUNT", Integer, nullable=True)
    
    # 필터링 결과
    is_passed_filter = Column("IS_PASSED_FILTER", Integer, default=0)
    filter_rank = Column("FILTER_RANK", Integer, nullable=True)
    
    # LLM 분석 결과
    llm_score = Column("LLM_SCORE", Float, nullable=True)
    hybrid_score = Column("HYBRID_SCORE", Float, nullable=True)
    is_final_selected = Column("IS_FINAL_SELECTED", Integer, default=0)
    
    # 메타 정보
    market_regime = Column("MARKET_REGIME", String(20), nullable=True)
    created_at = Column("CREATED_AT", DateTime, server_default=func.now())


# =============================================================================
# [v6.0] Long-Term Data Strategy Models (Jennie/Minji/Junho)
# =============================================================================

class LLMDecisionLedger(Base):
    """
    [Priority 1] The Brain's Memory
    - LLM의 모든 의사결정 과정, 토론 내용, 판단 근거를 기록
    - 나중에 Fine-tuning 및 성과 분석에 사용 (Why we acted/didn't act)
    """
    __tablename__ = "LLM_DECISION_LEDGER"
    __table_args__ = {"extend_existing": True}

    log_id = Column("LOG_ID", Integer, primary_key=True)
    timestamp = Column("TIMESTAMP", DateTime, default=datetime.utcnow) # UTC Standard
    
    # Context
    stock_code = Column("STOCK_CODE", String(20), nullable=False, index=True)
    stock_name = Column("STOCK_NAME", String(100), nullable=True)
    hunter_score = Column("HUNTER_SCORE", Float, nullable=True)
    market_regime = Column("MARKET_REGIME", String(50), nullable=True) # Bull/Bear/Neutral
    dominant_keywords_json = Column("DOMINANT_KEYWORDS_JSON", Text, nullable=True) # ["Lithium", "War"]
    
    # Process (The Debate)
    debate_log = Column("DEBATE_LOG", Text, nullable=True) # Full Text of Debate
    counter_position_logic = Column("COUNTER_POSITION_LOGIC", Text, nullable=True) # Summary of Opposing View
    
    # Ops Data (Junho's Request)
    thinking_called = Column("THINKING_CALLED", Integer, default=0) # 1=True
    thinking_reason = Column("THINKING_REASON", String(100), nullable=True)
    cost_estimate = Column("COST_ESTIMATE", Float, default=0.0)
    gate_result = Column("GATE_RESULT", String(20), nullable=True) # PASS / REJECT
    
    # Outcome
    final_decision = Column("FINAL_DECISION", String(20), nullable=False) # BUY / SELL / HOLD / NO_DECISION
    final_reason = Column("FINAL_REASON", String(2000), nullable=True) # 1-line reason
    
    # Schema Version
    schema_v = Column("SCHEMA_V", String(10), default="1.0")

    created_at = Column("CREATED_AT", DateTime, server_default=func.now())


class ShadowRadarLog(Base):
    """
    [Priority 2] The Shadow Radar (Missed Opportunities)
    - 필터링되어 탈락했으나, 나중에 급등/급변한 종목들의 기록
    - '나중에 쫄보 필터 튜닝'을 위한 데이터
    """
    __tablename__ = "SHADOW_RADAR_LOG"
    __table_args__ = {"extend_existing": True}

    log_id = Column("LOG_ID", Integer, primary_key=True)
    timestamp = Column("TIMESTAMP", DateTime, default=datetime.utcnow)
    
    stock_code = Column("STOCK_CODE", String(20), nullable=False, index=True)
    stock_name = Column("STOCK_NAME", String(100), nullable=True)
    
    # Why Missed?
    rejection_stage = Column("REJECTION_STAGE", String(50), nullable=True) # Hunter / Gate / Judge
    rejection_reason = Column("REJECTION_REASON", String(1000), nullable=True)
    hunter_score_at_time = Column("HUNTER_SCORE_AT_TIME", Float, nullable=True)
    
    # Trigger (Why we are looking back?)
    trigger_type = Column("TRIGGER_TYPE", String(50), nullable=True) # PRICE_SURGE / VOL_SHOCK
    trigger_value = Column("TRIGGER_VALUE", Float, nullable=True) # e.g. +15.5%
    
    schema_v = Column("SCHEMA_V", String(10), default="1.0")
    created_at = Column("CREATED_AT", DateTime, server_default=func.now())


class MarketFlowSnapshot(Base):
    """
    [Priority 2.5/3] Market Flow (Trend Foundations)
    - 일별/장중 수급 데이터 (외인, 기관, 프로그램)
    """
    __tablename__ = "MARKET_FLOW_SNAPSHOT"
    __table_args__ = {"extend_existing": True}

    snapshot_id = Column("SNAPSHOT_ID", Integer, primary_key=True)
    timestamp = Column("TIMESTAMP", DateTime, default=datetime.utcnow) # UTC
    
    stock_code = Column("STOCK_CODE", String(20), nullable=False, index=True)
    
    # Price
    price = Column("PRICE", Float, nullable=True)
    volume = Column("VOLUME", Float, nullable=True)
    
    # Flow (Net Buy Amount/Volume)
    foreign_net_buy = Column("FOREIGN_NET_BUY", Float, nullable=True)
    institution_net_buy = Column("INSTITUTION_NET_BUY", Float, nullable=True)
    program_net_buy = Column("PROGRAM_NET_BUY", Float, nullable=True)
    
    # Meta
    data_type = Column("DATA_TYPE", String(20), default="DAILY") # DAILY / INTRADAY
    schema_v = Column("SCHEMA_V", String(10), default="1.0")
    
    created_at = Column("CREATED_AT", DateTime, server_default=func.now())


class StockMinutePrice(Base):
    """
    [Priority 3] Targeted Intraday Data
    - 1-minute OHLCV data for Shadow Radar candidates
    """
    __tablename__ = "STOCK_MINUTE_PRICE"
    __table_args__ = {"extend_existing": True}

    price_time = Column("PRICE_TIME", DateTime, primary_key=True) # UTC or KST? Usually KST in DB, but we use UTC for timestamp. Let's assume this is the candle time.
    stock_code = Column("STOCK_CODE", String(20), primary_key=True, index=True)
    
    open_price = Column("OPEN_PRICE", Float)
    high_price = Column("HIGH_PRICE", Float)
    low_price = Column("LOW_PRICE", Float)
    close_price = Column("CLOSE_PRICE", Float)
    volume = Column("VOLUME", Float) # Candle volume
    
    accum_volume = Column("ACCUM_VOLUME", Float, nullable=True) # Cumulative volume for the day
    
    created_at = Column("CREATED_AT", DateTime, server_default=func.now())

