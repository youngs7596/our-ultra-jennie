"""
shared/database/__init__.py - Ultra Jennie 데이터베이스 유틸리티 Facade
================================================================

이 패키지는 `shared/database/` 하위 모듈들을 통합하여 제공하는 Facade입니다.
기존 코드와의 호환성을 위해 유지됩니다.

[v5.0] 대규모 리팩터링: 도메인별 모듈을 `shared/database/` 패키지로 통합
- core.py: 기본 연결, 설정
- market.py: 시세, 종목정보, 뉴스
- trading.py: 매매, 포트폴리오, 관심종목
- rag.py: RAG 캐시
- commands.py: Agent 명령
- optimization.py: 파라미터 최적화
"""

# ============================================================================
# 1. Re-export redis_cache (하위 호환성)
# ============================================================================
from shared.redis_cache import (
    get_redis_connection,
    set_market_regime_cache,
    get_market_regime_cache,
    set_sentiment_score,
    get_sentiment_score,
    set_redis_data,
    get_redis_data,
    set_competitor_benefit_score,
    get_competitor_benefit_score,
    get_all_competitor_benefits,
    MARKET_REGIME_CACHE_KEY,
)

# ============================================================================
# 2. Re-export shared/database/ Package Modules
# ============================================================================
from .core import (
    init_connection_pool,
    get_config,
    get_all_config,
    set_config,
    _is_mariadb,
    _get_table_name,
    _is_sqlalchemy_ready,
    pool
)

from .market import (
    get_stock_by_code,
    search_stock_by_name,
    save_all_daily_prices,
    update_all_stock_fundamentals,
    get_daily_prices,
    get_daily_prices_batch,
    save_news_sentiment
)

from .trading import (
    get_active_watchlist,
    save_to_watchlist,
    save_to_watchlist_history,
    get_watchlist_history,
    get_active_portfolio,
    remove_from_portfolio,
    execute_trade_and_log,
    record_trade,
    get_today_trades,
    get_trade_log,
    was_traded_recently,
    get_recently_traded_stocks_batch,
    check_duplicate_order
)

from .rag import (
    upsert_rag_cache,
    get_rag_context_from_cache,
    get_rag_context_with_validation
)

from .commands import (
    create_agent_command,
    get_pending_agent_commands,
    update_agent_command_status,
    get_recent_agent_commands
)

from .optimization import (
    save_optimization_history,
    mark_optimization_applied,
    get_recent_optimization_history
)
