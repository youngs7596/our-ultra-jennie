# database.py 대규모 분리 계획

## 목표
- `database.py` 라인 수(약 2400+)를 대폭 감소시켜 LLM/개발자 가독성 향상
- 기존 API/함수 시그니처 유지: `database.py`는 얇은 re-export/wrapper만 남김
- 도메인별 모듈로 구현 이동(책임 분리), 필요 없는/레거시 함수는 Deprecated 표시 후 후속 제거 준비

## 대상 함수(라인 기준: grep -n '^def ' shared/database.py | head -n 100)
- 커넥션/풀: `_get_table_name`, `_is_sqlalchemy_ready`, `init_connection_pool`, `get_connection`, `release_connection`, `close_pool`, `is_pool_initialized`, `reset_pool`, `get_db_connection_context`, `get_db_connection`
- 시세/펀더멘털/일봉: `save_all_daily_prices`, `update_all_stock_fundamentals`, `get_daily_prices`, `get_daily_prices_batch`
- 워치리스트: `save_to_watchlist`, `save_to_watchlist_history`, `get_watchlist_history`, `get_all_stock_codes`, `get_active_watchlist`, `_get_active_watchlist_legacy`
- 트레이드/포트폴리오: `get_today_total_buy_amount*`, `get_today_buy_count*`, `get_trade_logs*`, `get_stock_sector`, `_get_active_portfolio_impl*`, `get_active_portfolio`, `update_portfolio_status`, `execute_trade_and_log*`, `get_trade_log`, `remove_from_portfolio`, `check_duplicate_order`
- 설정/RAG: `get_config`, `get_all_config`, `set_config`, `upsert_rag_cache`, `get_rag_context_from_cache`, `get_rag_context_with_validation`, `was_traded_recently`, `get_recently_traded_stocks_batch`
- Agent/Optimization: `create_agent_command`, `get_pending_agent_commands`, `update_agent_command_status`, `get_recent_agent_commands`, `save_optimization_history`, `mark_optimization_applied`, `get_recent_optimization_history`

## 모듈 분리 계획 (re-export 유지)
- `database_base.py` (완료): 테이블 네이밍/초기화/타입 헬퍼, 커넥션 풀
- `database_portfolio.py` (완료): watchlist/portfolio CRUD 일부
- `database_tradelog.py` (완료): record_trade, get_today_trades
- `database_marketdata.py` (완료): 일봉 조회 등
- `database_master.py` (완료): 종목 마스터 조회/검색
- `database_news.py` (완료): 뉴스 감성 저장

### 추가 분리 (이번 단계)
- `database_price.py`: `save_all_daily_prices`, `update_all_stock_fundamentals`, `get_daily_prices`, `get_daily_prices_batch`
- `database_watchlist_legacy.py`: `save_to_watchlist_history`, `get_watchlist_history`, `get_all_stock_codes`, `_get_active_watchlist_legacy`
- `database_trade_legacy.py`: `get_today_total_buy_amount*`, `get_today_buy_count*`, `get_trade_logs*`, `_get_active_portfolio_impl*`, `execute_trade_and_log*`, `get_trade_log`, `remove_from_portfolio`, `check_duplicate_order`, `get_stock_sector`
- `database_config.py`: `get_config`, `get_all_config`, `set_config`, `upsert_rag_cache`, `get_rag_context_from_cache`, `get_rag_context_with_validation`, `was_traded_recently`, `get_recently_traded_stocks_batch`
- `database_agent.py`: `create_agent_command`, `get_pending_agent_commands`, `update_agent_command_status`, `get_recent_agent_commands`, `save_optimization_history`, `mark_optimization_applied`, `get_recent_optimization_history`

## 마이그레이션 방식
- 구현을 새 모듈로 옮기고, `database.py`에서는 동일한 함수명을 import/re-export
- 외부 호출 경로/시그니처 유지
- 레거시 함수는 그대로 보존하되 Deprecated 주석으로 후속 제거 준비

## 완료 후 기대 상태
- `database.py`는 import/re-export만 포함하는 얇은 파일로 축소
- 도메인별 구현이 각 모듈로 분리되어 가독성/변경 용이성 향상

## 주의사항
- 대량 이동이므로 오타/누락 주의 (단계별로 실행)
- 기능 변경 없음: 로직 그대로 이동만 수행
- 필요시 추가 모듈 명칭 조정 가능 (price/trade/config/agent 등)
