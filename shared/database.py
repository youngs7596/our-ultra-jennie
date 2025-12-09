"""
shared/database.py - Ultra Jennie 데이터베이스 유틸리티 모듈
==========================================================

이 모듈은 MariaDB 및 Redis와의 연동을 담당합니다.

[v5.0] 대규모 리팩터링: 도메인별 모듈로 분리
- database_config.py: CONFIG 테이블 관련 함수
- database_rag.py: RAG 캐시 관련 함수
- database_commands.py: Agent 명령 관련 함수
- database_optimization.py: 파라미터 최적화 이력 관련 함수
- database_watchlist.py: Watchlist 관련 함수
- database_trade.py: 거래 실행/로깅 관련 함수

핵심 기능:
---------
1. DB 연결 관리: MariaDB 연결 풀 관리
2. Redis 캐시: 시장 국면, 토큰 등 실시간 데이터 캐싱
3. Watchlist 관리: 관심 종목 CRUD
4. Portfolio 관리: 보유 종목 CRUD
5. Trade Log: 거래 이력 기록
6. 주가 데이터: 일봉/분봉 조회

환경변수:
--------
- DB_TYPE: 데이터베이스 타입 (MARIADB)
- MARIADB_HOST: MariaDB 호스트
- MARIADB_PORT: MariaDB 포트 (기본: 3306)
- MARIADB_USER: MariaDB 사용자
- MARIADB_PASSWORD: MariaDB 비밀번호
- MARIADB_DBNAME: MariaDB 데이터베이스명
- REDIS_URL: Redis 연결 URL (기본: redis://localhost:6379)
- TRADING_MODE: 거래 모드 (REAL/MOCK) - 테이블 suffix 결정
"""

import logging
import pandas as pd
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta

from shared.db import connection as sa_connection
from shared.db import repository as sa_repository

# ============================================================================
# Re-export: Redis 함수들 (하위 호환성)
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
# Re-export: 기존 분리 모듈들 (하위 호환성)
# ============================================================================
from shared.database_base import (
    _is_mariadb,
    _get_param_placeholder,
    _get_table_name,
    _is_sqlalchemy_ready,
    init_connection_pool as _base_init_connection_pool,
)
from shared.database_portfolio import (
    get_active_watchlist,
    get_active_portfolio as _portfolio_get_active_portfolio,
)
from shared.database_tradelog import record_trade, get_today_trades
from shared.database_marketdata import get_daily_prices
from shared.database_price import (
    save_all_daily_prices,
    update_all_stock_fundamentals as _price_update_all_stock_fundamentals,
    get_daily_prices as _price_get_daily_prices,
    get_daily_prices_batch as _price_get_daily_prices_batch,
)
from shared.database_master import (
    get_stock_by_code,
    search_stock_by_name,
)
from shared.database_news import save_news_sentiment

# ============================================================================
# Re-export: 새로 분리된 모듈들 (v5.0)
# ============================================================================
from shared.database_config import (
    get_config,
    get_all_config,
    set_config,
)
from shared.database_rag import (
    upsert_rag_cache,
    get_rag_context_from_cache,
    get_rag_context_with_validation,
)
from shared.database_commands import (
    create_agent_command,
    get_pending_agent_commands,
    update_agent_command_status,
    get_recent_agent_commands,
)
from shared.database_optimization import (
    save_optimization_history,
    mark_optimization_applied,
    get_recent_optimization_history,
)
from shared.database_watchlist import (
    save_to_watchlist,
    save_to_watchlist_history,
    get_watchlist_history,
)
from shared.database_trade import (
    execute_trade_and_log,
    get_trade_log,
    was_traded_recently,
    get_recently_traded_stocks_batch,
    check_duplicate_order,
    remove_from_portfolio,
)

logger = logging.getLogger(__name__)


# ============================================================================
# Thin wrappers (API 호환용)
# ============================================================================
def update_all_stock_fundamentals(connection, all_fundamentals_params):
    return _price_update_all_stock_fundamentals(connection, all_fundamentals_params)


def get_daily_prices_batch(connection, stock_codes: list, limit: int = 120, table_name: str = "STOCK_DAILY_PRICES_3Y"):
    return _price_get_daily_prices_batch(connection, stock_codes, limit=limit, table_name=table_name)


def get_active_portfolio(connection):
    """보유 포트폴리오 조회"""
    return _get_active_portfolio_impl(connection)


# ============================================================================
# 연결 관리 함수
# ============================================================================
pool = None  # 전역 연결 풀 변수


def init_connection_pool(db_user=None, db_password=None, db_service_name=None, wallet_path=None, min_sessions=2, max_sessions=5, increment=1):
    """MariaDB에서는 SQLAlchemy 엔진만 초기화합니다."""
    global pool
    
    min_pool_size = int(os.getenv("DB_POOL_MIN", min_sessions))
    max_pool_size = int(os.getenv("DB_POOL_MAX", max_sessions))

    logger.info(f"--- [DB Pool] MariaDB SQLAlchemy 엔진 초기화 (pool_size: {min_pool_size}~{max_pool_size}) ---")

    sa_connection.ensure_engine_initialized(
        db_user=db_user,
        db_password=db_password,
        db_service_name=db_service_name,
        wallet_path=wallet_path,
        min_sessions=min_pool_size,
        max_sessions=max_pool_size,
    )
    
    pool = True
    logger.info("✅ [DB Pool] MariaDB SQLAlchemy 엔진 초기화 완료!")


def get_connection(max_retries=3, retry_delay=1, validate_connection=True):
    """MariaDB 연결을 가져옵니다."""
    import time
    
    engine = sa_connection.get_engine()
    if engine is not None:
        for attempt in range(1, max_retries + 1):
            try:
                conn = engine.raw_connection()
                if validate_connection:
                    conn.ping(reconnect=True)
                return conn
            except Exception as e:
                logger.warning(f"⚠️ [DB] SQLAlchemy 연결 획득 시도 {attempt}/{max_retries} 실패: {e}")
                if attempt < max_retries:
                    time.sleep(retry_delay)
        return None
    
    global pool
    if not pool:
        logger.error("❌ [DB Pool] 연결 풀이 초기화되지 않았습니다.")
        return None
    
    import pymysql
    from shared.auth import get_secret
    
    for attempt in range(1, max_retries + 1):
        try:
            host = os.getenv("MARIADB_HOST") or get_secret("mariadb-host") or "localhost"
            port = int(os.getenv("MARIADB_PORT", "3306"))
            user = os.getenv("MARIADB_USER") or get_secret("mariadb-user") or "root"
            password = os.getenv("MARIADB_PASSWORD") or get_secret("mariadb-password") or ""
            dbname = os.getenv("MARIADB_DBNAME") or get_secret("mariadb-database") or "jennie_db"
            
            conn = pymysql.connect(
                host=host, port=port, user=user, password=password,
                database=dbname, charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor
            )
            if validate_connection:
                conn.ping(reconnect=True)
            return conn
        except Exception as e:
            logger.warning(f"⚠️ [DB Pool] 연결 획득 시도 {attempt}/{max_retries} 실패: {e}")
            if attempt < max_retries:
                time.sleep(retry_delay)
            else:
                logger.error(f"❌ [DB Pool] 연결 가져오기 최종 실패: {e}")
    return None


def release_connection(connection):
    """MariaDB 연결을 닫습니다."""
    if connection:
        try:
            connection.close()
        except Exception as e:
            logger.warning(f"⚠️ [DB Pool] 연결 닫기 중 오류: {e}")


def close_pool():
    """연결 풀 종료"""
    global pool
    pool = None
    logger.info("--- [DB Pool] MariaDB 연결 풀 플래그가 리셋되었습니다. ---")


def is_pool_initialized():
    """연결 풀이 초기화되었는지 확인"""
    global pool
    engine = sa_connection.get_engine()
    return pool is not None or engine is not None


def reset_pool():
    """연결 풀을 강제로 재초기화"""
    global pool
    logger.warning("⚠️ [DB Pool] MariaDB 연결 풀 재초기화...")
    pool = None
    logger.info("✅ [DB Pool] 연결 풀 재초기화 완료")


@contextmanager
def get_db_connection_context():
    """연결 풀에서 연결을 가져와서 자동으로 반환하는 컨텍스트 매니저"""
    conn = None
    
    if not is_pool_initialized():
        raise Exception("DB Connection Pool이 초기화되지 않았습니다.")
    
    try:
        conn = get_connection()
        if not conn:
            raise Exception("DB 연결을 가져올 수 없습니다.")
    except Exception as e:
        error_str = str(e)
        if "Broken pipe" in error_str or "Errno 32" in error_str or "DPY-1001" in error_str or "not connected" in error_str:
            logger.error(f"❌ [DB Pool] 연결 끊김 감지 - 연결 풀 재초기화가 필요합니다.")
            reset_pool()
            raise Exception("DB 연결이 끊어졌습니다.")
        else:
            raise
    
    if not conn:
        raise Exception(f"DB 연결을 가져올 수 없습니다.")
    
    try:
        yield conn
    finally:
        if conn:
            release_connection(conn)
            logger.debug("🔧 [DB Pool] 연결 반납 완료")


def get_db_connection(db_user=None, db_password=None, db_service_name=None, wallet_path=None):
    """MariaDB 연결을 반환합니다."""
    try:
        sa_connection.ensure_engine_initialized()
        engine = sa_connection.get_engine()
        if engine is None:
            raise RuntimeError("SQLAlchemy 엔진이 초기화되지 않았습니다.")
        
        connection = engine.raw_connection()
        host = os.getenv("MARIADB_HOST", "localhost")
        port = os.getenv("MARIADB_PORT", "3306")
        dbname = os.getenv("MARIADB_DBNAME", "jennie_db")
        logger.info(f"✅ DB: MariaDB 연결 성공! ({host}:{port}/{dbname})")
        return connection
    except Exception as e:
        logger.error(f"❌ DB: MariaDB 연결 실패! (에러: {e})")
        return None


# ============================================================================
# Portfolio 관련 함수
# ============================================================================
def _get_active_portfolio_impl(connection):
    """get_active_portfolio의 실제 구현"""
    if _is_sqlalchemy_ready():
        try:
            with sa_connection.session_scope(readonly=True) as session:
                return sa_repository.get_active_portfolio(session)
        except Exception as exc:
            logger.error("❌ [SQLAlchemy] Active Portfolio 조회 실패 - legacy로 fallback: %s", exc)
    return _get_active_portfolio_impl_legacy(connection)


def _get_active_portfolio_impl_legacy(connection):
    portfolio = []
    cursor = None
    try:
        cursor = connection.cursor()
        portfolio_table = _get_table_name("Portfolio")
        
        sql = f"""
        SELECT id, stock_code, stock_name, quantity, average_buy_price, current_high_price,
               SELL_STATE, STOP_LOSS_PRICE, CREATED_AT
        FROM {portfolio_table} 
        WHERE status = 'HOLDING'
        ORDER BY id ASC
        """
        cursor.execute(sql)
        for row in cursor:
            if isinstance(row, dict):
                portfolio.append({
                    "id": row['id'], "code": row['stock_code'], "name": row['stock_name'], 
                    "quantity": row['quantity'], "avg_price": float(row['average_buy_price']), 
                    "high_price": float(row['current_high_price']),
                    "sell_state": row['SELL_STATE'],
                    "stop_loss_price": float(row['STOP_LOSS_PRICE']) if row['STOP_LOSS_PRICE'] else 0.0,
                    "created_at": row['CREATED_AT']
                })
            else:
                portfolio.append({
                    "id": row[0], "code": row[1], "name": row[2], "quantity": row[3],
                    "avg_price": float(row[4]), "high_price": float(row[5]),
                    "sell_state": row[6],
                    "stop_loss_price": float(row[7]) if row[7] is not None else 0.0,
                    "created_at": row[8]
                })
        
        logger.info(f"✅ DB(Legacy): 보유(ACTIVE) 포트폴리오 {len(portfolio)}개 로드 성공!")
        return portfolio
    except Exception as e:
        logger.error(f"❌ DB(Legacy): get_active_portfolio 실패! (에러: {e})")
        return []
    finally:
        if cursor: cursor.close()


def update_portfolio_status(connection, portfolio_id, status):
    """Portfolio 상태 변경"""
    cursor = None
    try:
        cursor = connection.cursor()
        portfolio_table = _get_table_name("Portfolio")
        
        if _is_mariadb():
            sql = f"UPDATE {portfolio_table} SET STATUS = %s, SELL_STATE = 'SOLD' WHERE id = %s"
            cursor.execute(sql, (status, portfolio_id))
        else:
            sql = f"UPDATE {portfolio_table} SET STATUS = :1, SELL_STATE = 'SOLD' WHERE id = :2"
            cursor.execute(sql, [status, portfolio_id])
        
        connection.commit()
        logger.info(f"✅ DB: Portfolio 상태 업데이트 완료 (ID: {portfolio_id}, Status: {status})")
        return True
    except Exception as e:
        logger.error(f"❌ DB: update_portfolio_status 실패! (에러: {e})")
        if connection: connection.rollback()
        return False
    finally:
        if cursor: cursor.close()


# ============================================================================
# 종목 조회 관련 함수
# ============================================================================
def get_all_stock_codes(connection):
    """전체 종목 코드 리스트를 조회합니다."""
    codes = []
    cursor = None
    try:
        cursor = connection.cursor()
        
        try:
            if _is_mariadb():
                cursor.execute("SELECT STOCK_CODE FROM STOCK_MASTER WHERE IS_ACTIVE = 1")
            else:
                cursor.execute("SELECT STOCK_CODE FROM STOCK_MASTER WHERE IS_ACTIVE = 1")
            
            rows = cursor.fetchall()
            if rows:
                codes = [row['STOCK_CODE'] if isinstance(row, dict) else row[0] for row in rows]
                logger.info(f"✅ DB: STOCK_MASTER에서 {len(codes)}개 종목 로드 완료")
                return codes
        except Exception as e:
            logger.debug(f"ℹ️ STOCK_MASTER 조회 실패 ({e}), STOCK_DAILY_PRICES_3Y 시도...")

        if _is_mariadb():
            sql = "SELECT DISTINCT STOCK_CODE FROM STOCK_DAILY_PRICES_3Y WHERE PRICE_DATE >= DATE_SUB(NOW(), INTERVAL 7 DAY)"
        else:
            sql = "SELECT DISTINCT STOCK_CODE FROM STOCK_DAILY_PRICES_3Y WHERE PRICE_DATE >= SYSDATE - 7"
            
        cursor.execute(sql)
        rows = cursor.fetchall()
        if rows:
            codes = [row['STOCK_CODE'] if isinstance(row, dict) else row[0] for row in rows]
            logger.info(f"✅ DB: STOCK_DAILY_PRICES_3Y에서 {len(codes)}개 종목 로드 완료")
            return codes
            
        logger.warning("⚠️ DB: 전체 종목 코드 조회 실패 (데이터 없음)")
        return []
        
    except Exception as e:
        logger.error(f"❌ DB: get_all_stock_codes 실패! (에러: {e})")
        return []
    finally:
        if cursor: cursor.close()


def get_stock_sector(connection, stock_code: str):
    """종목의 섹터 정보 반환"""
    cursor = None
    try:
        cursor = connection.cursor()
        
        if _is_mariadb():
            # [Fix] WatchList -> STOCK_MASTER (INDUSTRY_NAME)
            sql = "SELECT INDUSTRY_NAME FROM STOCK_MASTER WHERE STOCK_CODE = %s"
            cursor.execute(sql, (stock_code,))
        else:
            sql = "SELECT INDUSTRY_NAME FROM STOCK_MASTER WHERE STOCK_CODE = :1"
            cursor.execute(sql, [stock_code])
        
        result = cursor.fetchone()
        
        if result:
            sector = result['INDUSTRY_NAME'] if isinstance(result, dict) else result[0]
            if sector:
                logger.info(f"✅ DB: {stock_code} 섹터 조회 성공 → {sector}")
                return sector
        
        logger.warning(f"⚠️ DB: {stock_code} 섹터 정보 없음, 기본값 'UNKNOWN' 반환")
        return "UNKNOWN"
    except Exception as e:
        logger.error(f"❌ DB: get_stock_sector({stock_code}) 실패! (에러: {e})")
        return "UNKNOWN"
    finally:
        if cursor: cursor.close()


def get_trade_logs(connection, date=None):
    """특정 날짜의 거래 내역 조회"""
    trades = []
    cursor = None
    try:
        cursor = connection.cursor()
        tradelog_table = _get_table_name("TradeLog")
        
        if date is None:
            date = datetime.now().strftime('%Y-%m-%d')
        
        if _is_mariadb():
            sql = f"""
            SELECT LOG_ID, STOCK_CODE, TRADE_TYPE, QUANTITY, PRICE, REASON, TRADE_TIMESTAMP
            FROM {tradelog_table}
            WHERE DATE(TRADE_TIMESTAMP) = %s
            ORDER BY TRADE_TIMESTAMP DESC
            """
            cursor.execute(sql, (date,))
        else:
            sql = f"""
            SELECT LOG_ID, STOCK_CODE, TRADE_TYPE, QUANTITY, PRICE, REASON, TRADE_TIMESTAMP
            FROM {tradelog_table}
            WHERE TRUNC(TRADE_TIMESTAMP) = TO_DATE(:1, 'YYYY-MM-DD')
            ORDER BY TRADE_TIMESTAMP DESC
            """
            cursor.execute(sql, [date])
        
        rows = cursor.fetchall()
        
        for row in rows:
            if isinstance(row, dict):
                trades.append(row)
            else:
                trades.append({
                    'log_id': row[0], 'stock_code': row[1], 'trade_type': row[2],
                    'quantity': row[3], 'price': row[4], 'reason': row[5], 'trade_timestamp': row[6]
                })
        
        logger.debug(f"✅ DB: {date} 거래 {len(trades)}건 조회")
        return trades
        
    except Exception as e:
        logger.error(f"❌ DB: get_trade_logs 실패! (에러: {e})")
        return []
    finally:
        if cursor: cursor.close()


def get_today_total_buy_amount(connection):
    """오늘 총 매수 금액"""
    if _is_sqlalchemy_ready():
        try:
            with sa_connection.session_scope(readonly=True) as session:
                return sa_repository.get_today_total_buy_amount(session)
        except Exception:
            pass
    return _get_today_total_buy_amount_legacy(connection)


def _get_today_total_buy_amount_legacy(connection):
    cursor = None
    try:
        cursor = connection.cursor()
        tradelog_table = _get_table_name("TradeLog")
        
        if _is_mariadb():
            sql = f"""
            SELECT COALESCE(SUM(QUANTITY * PRICE), 0)
            FROM {tradelog_table}
            WHERE TRADE_TYPE LIKE 'BUY%' AND DATE(TRADE_TIMESTAMP) = CURDATE()
            """
        else:
            sql = f"""
            SELECT NVL(SUM(QUANTITY * PRICE), 0)
            FROM {tradelog_table}
            WHERE TRADE_TYPE LIKE 'BUY%' AND TRUNC(TRADE_TIMESTAMP) = TRUNC(SYSDATE)
            """
        
        cursor.execute(sql)
        row = cursor.fetchone()
        return float(row[0]) if row and row[0] else 0.0
    except Exception as e:
        logger.error(f"❌ DB: get_today_total_buy_amount 실패! (에러: {e})")
        return 0.0
    finally:
        if cursor: cursor.close()


def get_today_buy_count(connection):
    """오늘 매수한 종목 수 반환"""
    if _is_sqlalchemy_ready():
        try:
            with sa_connection.session_scope(readonly=True) as session:
                return sa_repository.get_today_buy_count(session)
        except Exception:
            pass
    return _get_today_buy_count_legacy(connection)


def _get_today_buy_count_legacy(connection):
    cursor = None
    try:
        cursor = connection.cursor()
        tradelog_table = _get_table_name("TradeLog")
        
        if _is_mariadb():
            sql = f"""
            SELECT COUNT(DISTINCT STOCK_CODE)
            FROM {tradelog_table}
            WHERE TRADE_TYPE LIKE 'BUY%' AND DATE(TRADE_TIMESTAMP) = CURDATE()
            """
        else:
            sql = f"""
            SELECT COUNT(DISTINCT STOCK_CODE)
            FROM {tradelog_table}
            WHERE TRADE_TYPE LIKE 'BUY%' AND TRUNC(TRADE_TIMESTAMP) = TRUNC(SYSDATE)
            """
        
        cursor.execute(sql)
        row = cursor.fetchone()
        return int(row[0]) if row and row[0] else 0
    except Exception as e:
        logger.error(f"❌ DB: get_today_buy_count 실패! (에러: {e})")
        return 0
    finally:
        if cursor: cursor.close()
