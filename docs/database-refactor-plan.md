# database.py 대규모 분리 계획

## 목표
- `database.py` 라인 수(약 2400+)를 대폭 감소시켜 LLM/개발자 가독성 향상
- 기존 API/함수 시그니처 유지: `database.py`는 얇은 re-export/wrapper만 남김
- 도메인별 모듈로 구현 이동(책임 분리), 필요 없는/레거시 함수는 Deprecated 표시 후 후속 제거 준비

## 최신 업데이트 (2025-12-09)
- `database_price.py` 신설: `save_all_daily_prices`, `update_all_stock_fundamentals`, `get_daily_prices`, `get_daily_prices_batch` 구현 이동 완료
- `database.py`는 위 4개 함수에 대해 thin wrapper/re-export 제공, 기존 레거시 버전은 `_legacy_get_daily_prices*`로 이름 정리
- README에 진행 상황 및 분리된 모듈 안내 추가 예정

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

### 추가 분리 (완료)
- (완료) `database_price.py`: `save_all_daily_prices`, `update_all_stock_fundamentals`, `get_daily_prices`, `get_daily_prices_batch`
- (완료) `database_watchlist_legacy.py` → `trading.py`: Watchlist 관련 레거시 통합
- (완료) `database_trade_legacy.py` → `trading.py`: Trade/Portfolio 관련 레거시 통합
- (완료) `database_config.py` → `core.py`: Config/RAG 관련 통합
- (완료) `database_agent.py` → `commands.py` / `optimization.py`: Agent/Optimization 관련 분리

## 마이그레이션 결과 (2025-12-09 완료)
- `shared/database/` 패키지 생성 (core, market, trading, rag, commands, optimization)
- `shared/database/__init__.py`: Facade 구현 (기존 `shared.database` import 호환성 유지)
- 기존 `shared/database_*.py` 파일 13개 전량 삭제
- 주요 버그 수정:
  - f-string escape (`\"\"\"` → `"""`) 오류 수정
  - `get_db_connection` 등 레거시 인터페이스 복원

## 완료 후 상태
- `database.py`가 제거되고 `shared/database` 패키지로 대체됨
- 도메인별 응집도가 높아지고 파일 크기가 관리 가능한 수준으로 분할됨
