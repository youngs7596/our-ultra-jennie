# Version: v3.7
# 작업 LLM: Claude Sonnet 4.5, Claude Opus 4.5
"""
[v3.7] SQLAlchemy ORM 모델 정의
- CONFIG_VALUE 컬럼 TEXT로 변경 (Claude Opus 4.5)
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
