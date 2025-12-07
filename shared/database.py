"""
shared/database.py - Ultra Jennie 데이터베이스 유틸리티 모듈
==========================================================

이 모듈은 MariaDB 및 Redis와의 연동을 담당합니다.

핵심 기능:
---------
1. DB 연결 관리: MariaDB 연결 풀 관리
2. Redis 캐시: 시장 국면, 토큰 등 실시간 데이터 캐싱
3. Watchlist 관리: 관심 종목 CRUD
4. Portfolio 관리: 보유 종목 CRUD
5. Trade Log: 거래 이력 기록
6. 주가 데이터: 일봉/분봉 조회

주요 테이블:
----------
- WATCHLIST: 관심 종목 (LLM 점수 포함)
- PORTFOLIO / PORTFOLIO_MOCK: 보유 종목
- TRADELOG / TRADELOG_MOCK: 거래 이력
- STOCK_DAILY_PRICES_3Y: 3년 일봉 데이터
- STOCK_MASTER: 종목 마스터 (코드, 이름, 섹터)
- NEWS_SENTIMENT: 뉴스 감성 분석 결과

사용 예시:
---------
>>> from shared.database import get_db_connection, get_active_watchlist
>>>
>>> conn = get_db_connection()
>>> watchlist = get_active_watchlist(conn)
>>> for code, info in watchlist.items():
...     print(f"{code}: {info['name']} - Score {info.get('llm_score', 'N/A')}")

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

from shared.db import connection as sa_connection
from shared.db import repository as sa_repository
from datetime import datetime, timezone, timedelta

# [v4.1] Redis 함수들을 redis_cache 모듈에서 re-export (하위 호환성 유지)
# 기존 `from shared.database import get_sentiment_score` 등이 계속 동작함
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

logger = logging.getLogger(__name__)

# ============================================================================
# DB 타입 헬퍼 함수
# ============================================================================
def _is_mariadb() -> bool:
    """현재 DB 타입 확인 (항상 MariaDB)"""
    return True

def _get_param_placeholder(index: int = 1) -> str:
    """DB 타입에 따른 파라미터 플레이스홀더 반환 (MariaDB: %s)"""
    return "%s"


# ============================================================================
# [v4.1] Redis 함수들은 shared/redis_cache.py로 분리됨
# 하위 호환성을 위해 이 파일 상단에서 re-export 중
# ============================================================================


# ============================================================================
# Oracle DB: 뉴스 감성 저장
# ============================================================================
def save_news_sentiment(connection, stock_code, title, score, reason, url, published_at):
    """
    [v3.8] 뉴스 감성 분석 결과를 영구 저장합니다.
    MariaDB/Oracle 하이브리드 지원 (Claude Opus 4.5)
    """
    cursor = None
    try:
        cursor = connection.cursor()
        
        # 테이블 이름 매핑 (Mock 모드 대응)
        table_name = _get_table_name("NEWS_SENTIMENT")
        
        # 테이블 존재 여부 확인 (없으면 자동 생성)
        # MariaDB: LIMIT 1 사용
        try:
            cursor.execute(f"SELECT 1 FROM {table_name} LIMIT 1")
        except Exception:
            logger.warning(f"⚠️ 테이블 {table_name}이 없어 생성을 시도합니다.")
            create_sql = f"""
            CREATE TABLE {table_name} (
                ID INT AUTO_INCREMENT PRIMARY KEY,
                STOCK_CODE VARCHAR(20) NOT NULL,
                NEWS_TITLE VARCHAR(1000),
                SENTIMENT_SCORE INT DEFAULT 50,
                SENTIMENT_REASON VARCHAR(2000),
                SOURCE_URL VARCHAR(2000),
                PUBLISHED_AT DATETIME,
                CREATED_AT DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY UK_NEWS_URL (SOURCE_URL(500))
            )
            """
            cursor.execute(create_sql)
            connection.commit()
            logger.info(f"✅ 테이블 {table_name} 생성 완료")

        # 중복 URL 체크 (이미 저장된 뉴스면 Skip)
        check_sql = f"SELECT 1 FROM {table_name} WHERE SOURCE_URL = %s"
        cursor.execute(check_sql, [url])
        if cursor.fetchone():
            logger.debug(f"ℹ️ [DB] 이미 존재하는 뉴스입니다. (Skip): {title[:20]}...")
            return

        # published_at이 int timestamp인 경우 변환
        if isinstance(published_at, int):
            published_at_str = datetime.fromtimestamp(published_at).strftime('%Y-%m-%d %H:%M:%S')
        else:
            published_at_str = str(published_at)[:19]

        insert_sql = f"""
        INSERT INTO {table_name} 
        (STOCK_CODE, NEWS_TITLE, SENTIMENT_SCORE, SENTIMENT_REASON, SOURCE_URL, PUBLISHED_AT)
        VALUES (%s, %s, %s, %s, %s, %s)
        """
        cursor.execute(insert_sql, [stock_code, title, score, reason, url, published_at_str])
        
        connection.commit()
        logger.info(f"✅ [DB] 뉴스 감성 저장 완료: {stock_code} ({score}점)")
        
    except Exception as e:
        logger.error(f"❌ [DB] 뉴스 감성 저장 실패: {e}")
        if connection: connection.rollback()
    finally:
        if cursor: cursor.close()


# ============================================================================
# MOCK 모드 테이블명 헬퍼 함수
# ============================================================================
def _get_table_name(base_name: str) -> str:
    """
    MOCK 모드일 때는 Portfolio와 TradeLog만 _mock 접미사 추가
    다른 테이블은 그대로 사용 (WatchList, STOCK_DAILY_PRICES_3Y 등)
    
    Args:
        base_name: 기본 테이블명 (예: "Portfolio", "TradeLog")
    
    Returns:
        MOCK 모드일 때는 "Portfolio_mock" 또는 "TradeLog_mock", 
        REAL 모드이거나 다른 테이블은 원래 이름 그대로
    """
    trading_mode = os.getenv("TRADING_MODE", "REAL")
    if trading_mode == "MOCK":
        if base_name in ["Portfolio", "TradeLog", "NEWS_SENTIMENT"]: # NEWS_SENTIMENT도 Mock 지원
            table_name = f"{base_name}_mock"
            logger.debug(f"   [MOCK 모드] 테이블명: {base_name} → {table_name}")
            return table_name
    return base_name

def _is_sqlalchemy_ready() -> bool:
    try:
        return sa_connection.is_engine_initialized()
    except Exception:
        return False

pool = None # 전역 연결 풀 변수 (MariaDB에서는 사용하지 않음)

# --- (init_connection_pool - MariaDB에서는 SQLAlchemy 엔진만 초기화) ---
def init_connection_pool(db_user=None, db_password=None, db_service_name=None, wallet_path=None, min_sessions=2, max_sessions=5, increment=1):
    """MariaDB에서는 SQLAlchemy 엔진만 초기화합니다."""
    global pool
    
    # 환경 변수가 존재하면 기본값을 덮어씁니다.
    min_pool_size = int(os.getenv("DB_POOL_MIN", min_sessions))
    max_pool_size = int(os.getenv("DB_POOL_MAX", max_sessions))

    logger.info(f"--- [DB Pool] MariaDB SQLAlchemy 엔진 초기화 (pool_size: {min_pool_size}~{max_pool_size}) ---")

    # SQLAlchemy 엔진 초기화
    sa_connection.ensure_engine_initialized(
        db_user=db_user,
        db_password=db_password,
        db_service_name=db_service_name,
        wallet_path=wallet_path,
        min_sessions=min_pool_size,
        max_sessions=max_pool_size,
    )
    
    # MariaDB는 pymysql 단일 연결 또는 SQLAlchemy pool 사용
    pool = True  # 초기화 완료 플래그
    logger.info("✅ [DB Pool] MariaDB SQLAlchemy 엔진 초기화 완료!")

def get_connection(max_retries=3, retry_delay=1, validate_connection=True):
    """
    MariaDB 연결을 가져옵니다. (SQLAlchemy raw connection 또는 legacy pool)
    
    Args:
        max_retries: 최대 재시도 횟수 (기본값: 3)
        retry_delay: 재시도 간 대기 시간(초) (기본값: 1초)
        validate_connection: 연결 유효성 검사 여부 (기본값: True)
    """
    import time
    
    # 1. SQLAlchemy 엔진 확인
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
    
    # 2. Legacy pool 사용
    global pool
    if not pool:
        logger.error("❌ [DB Pool] 연결 풀이 초기화되지 않았습니다.")
        return None
    
    import pymysql
    from shared.auth import get_secret
    
    for attempt in range(1, max_retries + 1):
        try:
            # 환경변수 우선, 없으면 secrets.json에서 읽기
            host = os.getenv("MARIADB_HOST") or get_secret("mariadb-host") or "localhost"
            port = int(os.getenv("MARIADB_PORT", "3306"))
            user = os.getenv("MARIADB_USER") or get_secret("mariadb-user") or "root"
            password = os.getenv("MARIADB_PASSWORD") or get_secret("mariadb-password") or ""
            dbname = os.getenv("MARIADB_DBNAME") or get_secret("mariadb-database") or "jennie_db"
            
            conn = pymysql.connect(
                host=host,
                port=port,
                user=user,
                password=password,
                database=dbname,
                charset='utf8mb4',
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
                logger.error(f"❌ [DB Pool] 연결 가져오기 최종 실패 (재시도 {max_retries}회 모두 실패): {e}")
    
    return None

def release_connection(connection):
    """MariaDB 연결을 닫습니다."""
    if connection:
        try:
            connection.close()
        except Exception as e:
            logger.warning(f"⚠️ [DB Pool] 연결 닫기 중 오류: {e}")

def close_pool():
    """연결 풀 종료 (MariaDB에서는 플래그만 리셋)"""
    global pool
    pool = None
    logger.info("--- [DB Pool] MariaDB 연결 풀 플래그가 리셋되었습니다. ---")

def is_pool_initialized():
    """연결 풀이 초기화되었는지 확인 (SQLAlchemy 엔진 또는 legacy pool)"""
    global pool
    # SQLAlchemy 엔진이 초기화되었거나 legacy pool이 있으면 True
    engine = sa_connection.get_engine()
    return pool is not None or engine is not None

def reset_pool():
    """연결 풀을 강제로 재초기화"""
    global pool
    logger.warning("⚠️ [DB Pool] MariaDB 연결 풀 재초기화...")
    pool = None
    logger.info("✅ [DB Pool] 연결 풀 재초기화 완료")

# --- 컨텍스트 매니저 추가 ---
from contextlib import contextmanager

@contextmanager
def get_db_connection_context():
    """
    연결 풀에서 연결을 가져와서 자동으로 반환하는 컨텍스트 매니저
    
    - Pool에서 연결을 가져와서 사용 후 반납 (재사용)
    - Pool이 초기화되지 않은 경우 예외 발생
    
    사용 예시:
        with database.get_db_connection_context() as conn:
            watchlist = database.get_active_watchlist(conn)
            # ... 작업 수행 ...
    
    성능 최적화:
        - Secret은 shared.auth에서 캐싱되므로 반복 호출 시 빠름
        - Connection Pool을 재사용하여 연결 생성 오버헤드 제거
        - Pool 연결 획득 실패 시 자동 재시도 (get_connection 내부 로직)
    """
    conn = None
    
    # Pool 초기화 확인
    if not is_pool_initialized():
        raise Exception("DB Connection Pool이 초기화되지 않았습니다. 서비스 초기화 중 오류가 발생했을 수 있습니다.")
    
    try:
        # Pool에서 연결 획득 (재시도 로직 포함)
        conn = get_connection()
        if not conn:
            raise Exception("DB 연결을 가져올 수 없습니다. (Pool 연결 획득 실패)")
            
    except Exception as e:
        error_str = str(e)
        
        # Broken pipe 또는 OCI 연결 끊김 오류인 경우
        if "Broken pipe" in error_str or "Errno 32" in error_str or "DPY-1001" in error_str or "not connected" in error_str:
            logger.error(f"❌ [DB Pool] 연결 끊김 감지 ({error_str}) - 연결 풀 재초기화가 필요합니다.")
            reset_pool()
            raise Exception("DB 연결이 끊어졌습니다. (Connection lost)")
        else:
            # 다른 오류는 그대로 전파
            raise
    
    if not conn:
        raise Exception(f"DB 연결을 가져올 수 없습니다.")
    
    try:
        yield conn
    finally:
        if conn:
            # Pool 모드: 연결 반납 (재사용)
            release_connection(conn)
            logger.debug("🔧 [DB Pool] 연결 반납 완료 (Pool 재사용)")

# --- (get_db_connection - MariaDB 전용) ---
def get_db_connection(db_user=None, db_password=None, db_service_name=None, wallet_path=None):
    """
    MariaDB 연결을 반환합니다. (SQLAlchemy raw connection 사용)
    """
    try:
        # SQLAlchemy 엔진 초기화 후 raw connection 반환
        sa_connection.ensure_engine_initialized()
        engine = sa_connection.get_engine()
        if engine is None:
            raise RuntimeError("SQLAlchemy 엔진이 초기화되지 않았습니다.")
        
        # raw DBAPI connection 반환
        connection = engine.raw_connection()
        host = os.getenv("MARIADB_HOST", "localhost")
        port = os.getenv("MARIADB_PORT", "3306")
        dbname = os.getenv("MARIADB_DBNAME", "jennie_db")
        logger.info(f"✅ DB: MariaDB 연결 성공! ({host}:{port}/{dbname})")
        return connection
    except Exception as e:
        logger.error(f"❌ DB: MariaDB 연결 실패! (에러: {e})")
        return None

# --- (save_all_daily_prices, update_all_stock_fundamentals, save_to_watchlist - MariaDB/Oracle 호환) ---
def save_all_daily_prices(connection, all_daily_prices_params):
    """일봉 데이터 Bulk 저장 (MariaDB/Oracle 호환)"""
    cursor = None
    try:
        cursor = connection.cursor()
        
        if _is_mariadb():
            # MariaDB: INSERT ... ON DUPLICATE KEY UPDATE
            sql = """
            INSERT INTO STOCK_DAILY_PRICES (STOCK_CODE, PRICE_DATE, CLOSE_PRICE, HIGH_PRICE, LOW_PRICE)
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                CLOSE_PRICE = VALUES(CLOSE_PRICE),
                HIGH_PRICE = VALUES(HIGH_PRICE),
                LOW_PRICE = VALUES(LOW_PRICE)
            """
            # 파라미터 변환: dict -> tuple
            insert_data = []
            for p in all_daily_prices_params:
                insert_data.append((
                    p.get('p_code', p.get('stock_code')),
                    p.get('p_date', p.get('price_date')),
                    p.get('p_price', p.get('close_price')),
                    p.get('p_high', p.get('high_price')),
                    p.get('p_low', p.get('low_price'))
                ))
            cursor.executemany(sql, insert_data)
        else:
            # Oracle: MERGE
            sql_merge = """
            MERGE /*+ NO_PARALLEL */ INTO STOCK_DAILY_PRICES t
            USING (SELECT TO_DATE(:p_date, 'YYYY-MM-DD') AS price_date, :p_code AS stock_code, 
                          :p_price AS close_price, :p_high AS high_price, :p_low AS low_price FROM DUAL) s
            ON (t.STOCK_CODE = s.stock_code AND t.PRICE_DATE = s.price_date)
            WHEN MATCHED THEN
                UPDATE SET t.CLOSE_PRICE = s.close_price, t.HIGH_PRICE = s.high_price, t.LOW_PRICE = s.low_price
            WHEN NOT MATCHED THEN
                INSERT (STOCK_CODE, PRICE_DATE, CLOSE_PRICE, HIGH_PRICE, LOW_PRICE)
                VALUES (s.stock_code, s.price_date, s.close_price, s.high_price, s.low_price)
            """
            cursor.executemany(sql_merge, all_daily_prices_params)
        
        connection.commit()
        logger.info(f"✅ DB: 모든 종목의 일봉 데이터 {len(all_daily_prices_params)}건 Bulk 저장 완료.")
    except Exception as e:
        logger.error(f"❌ DB: 모든 종목 일봉 데이터 Bulk 저장 실패! (에러: {e})")
        if connection: connection.rollback()
    finally:
        if cursor: cursor.close()
        
def update_all_stock_fundamentals(connection, all_fundamentals_params):
    """펀더멘털 데이터 Bulk 업데이트 (MariaDB/Oracle 호환)"""
    cursor = None
    try:
        cursor = connection.cursor()
        now = datetime.now(timezone.utc)
        
        if _is_mariadb():
            # MariaDB: UPDATE 문 사용
            sql = """
            UPDATE WatchList 
            SET PER = %s, PBR = %s, MARKET_CAP = %s, UPDATED_AT = %s
            WHERE STOCK_CODE = %s
            """
            params_to_run = [
                (p['per'], p['pbr'], p['market_cap'], now, p['code'])
                for p in all_fundamentals_params
            ]
            cursor.executemany(sql, params_to_run)
        else:
            # Oracle: MERGE
            sql_merge = """
            MERGE INTO WatchList t
            USING (SELECT :p_code AS stock_code FROM dual) s
            ON (t.STOCK_CODE = s.stock_code)
            WHEN MATCHED THEN
                UPDATE SET
                    t.PER = :p_per,
                    t.PBR = :p_pbr,
                    t.MARKET_CAP = :p_market_cap,
                    t.UPDATED_AT = SYSTIMESTAMP
            """
            params_to_run = [
                {'p_code': p['code'], 'p_per': p['per'], 'p_pbr': p['pbr'], 'p_market_cap': p['market_cap']}
                for p in all_fundamentals_params
            ]
            cursor.executemany(sql_merge, params_to_run)
        
        connection.commit()
        logger.info(f"✅ DB: 모든 종목의 펀더멘털 {len(all_fundamentals_params)}건 Bulk 업데이트 완료.")
    except Exception as e:
        logger.error(f"❌ DB: 모든 종목 펀더멘털 데이터 Bulk 업데이트 실패! (에러: {e})")
        if connection: connection.rollback()
    finally:
        if cursor: cursor.close()

def save_to_watchlist(connection, candidates_to_save):
    """
    WatchList 저장 (MariaDB/Oracle 호환)
    
    [v4.1] UPSERT 방식으로 변경:
    - 새 종목: INSERT
    - 기존 종목: UPDATE (점수, 이유 갱신)
    - 24시간 지난 종목: 자동 삭제 (TTL)
    
    이렇게 하면 1시간마다 실행해도 이전 종목이 유지됨!
    """
    cursor = None
    try:
        cursor = connection.cursor()
        
        # [v4.1] Step 1: 24시간 지난 오래된 종목 삭제 (TTL)
        logger.info("   (DB) 1. 24시간 지난 오래된 종목 정리 중...")
        if _is_mariadb():
            cursor.execute("""
                DELETE FROM WatchList 
                WHERE LLM_UPDATED_AT < DATE_SUB(NOW(), INTERVAL 24 HOUR)
            """)
        else:
            cursor.execute("""
                DELETE FROM WatchList 
                WHERE LLM_UPDATED_AT < SYSTIMESTAMP - INTERVAL '24' HOUR
            """)
        deleted_count = cursor.rowcount
        if deleted_count > 0:
            logger.info(f"   (DB) ✅ {deleted_count}개 오래된 종목 삭제")
        
        if not candidates_to_save:
            logger.info("   (DB) 저장할 후보가 없습니다. (기존 종목 유지)")
            connection.commit()
            return
        
        logger.info(f"   (DB) 2. 우량주 후보 {len(candidates_to_save)}건 UPSERT...")
        
        now = datetime.now(timezone.utc)
        
        # [v4.1] UPSERT 쿼리 (기존 종목은 UPDATE, 새 종목은 INSERT)
        if _is_mariadb():
            sql_upsert = """
            INSERT INTO WatchList (
                STOCK_CODE, STOCK_NAME, CREATED_AT, IS_TRADABLE,
                LLM_SCORE, LLM_REASON, LLM_UPDATED_AT
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                STOCK_NAME = VALUES(STOCK_NAME),
                IS_TRADABLE = VALUES(IS_TRADABLE),
                LLM_SCORE = VALUES(LLM_SCORE),
                LLM_REASON = VALUES(LLM_REASON),
                LLM_UPDATED_AT = VALUES(LLM_UPDATED_AT)
            """
        else:
            # Oracle: MERGE INTO 사용
            sql_upsert = """
            MERGE INTO WatchList w
            USING (SELECT :1 AS code, :2 AS name, :3 AS tradable, :4 AS score, :5 AS reason FROM DUAL) src
            ON (w.STOCK_CODE = src.code)
            WHEN MATCHED THEN
                UPDATE SET STOCK_NAME = src.name, IS_TRADABLE = src.tradable,
                           LLM_SCORE = src.score, LLM_REASON = src.reason, LLM_UPDATED_AT = SYSTIMESTAMP
            WHEN NOT MATCHED THEN
                INSERT (STOCK_CODE, STOCK_NAME, CREATED_AT, IS_TRADABLE, LLM_SCORE, LLM_REASON, LLM_UPDATED_AT)
                VALUES (src.code, src.name, SYSTIMESTAMP, src.tradable, src.score, src.reason, SYSTIMESTAMP)
            """
        
        insert_count = 0
        update_count = 0
        metadata_marker = "[LLM_METADATA]"
        
        for c in candidates_to_save:
            # LLM 점수와 이유 추출 (기본값: 점수 0, 이유 없음)
            llm_score = c.get('llm_score', 0)
            llm_reason = c.get('llm_reason', '') or ''
            llm_metadata = c.get('llm_metadata')

            if llm_metadata:
                try:
                    metadata_json = json.dumps(llm_metadata, ensure_ascii=False)
                    llm_reason = f"{llm_reason}\n\n{metadata_marker}{metadata_json}"
                except Exception as e:
                    logger.warning(f"⚠️ WatchList 메타데이터 직렬화 실패: {e}")

            # REASON 길이 제한 (TEXT 타입이지만 안전하게 제한)
            if len(llm_reason) > 60000:
                llm_reason = llm_reason[:60000] + "..."
            
            # [v4.1] 개별 UPSERT 실행 (MariaDB/Oracle)
            if _is_mariadb():
                params = (
                    c['code'], 
                    c['name'],
                    now,  # CREATED_AT
                    1 if c.get('is_tradable', True) else 0,
                    llm_score,
                    llm_reason,
                    now  # LLM_UPDATED_AT
                )
                cursor.execute(sql_upsert, params)
                # rowcount: 1=INSERT, 2=UPDATE (MariaDB ON DUPLICATE KEY UPDATE 특성)
                if cursor.rowcount == 1:
                    insert_count += 1
                elif cursor.rowcount == 2:
                    update_count += 1
            else:
                params = (
                    c['code'], 
                    c['name'], 
                    1 if c.get('is_tradable', True) else 0,
                    llm_score,
                    llm_reason
                )
                cursor.execute(sql_upsert, params)
                # Oracle MERGE는 rowcount가 항상 1
                insert_count += 1
        
        connection.commit()
        logger.info(f"   (DB) ✅ WatchList UPSERT 완료! (신규 {insert_count}건, 갱신 {update_count}건)")
    except Exception as e:
        logger.error(f"❌ DB: save_to_watchlist 실패! (에러: {e})")
        if connection: connection.rollback()
    finally:
        if cursor: cursor.close()

def save_to_watchlist_history(connection, candidates_to_save, snapshot_date=None):
    """
    [v3.8] WatchList 스냅샷을 히스토리 테이블에 저장합니다. (Point-in-Time Backtest용)
    MariaDB/Oracle 하이브리드 지원 (Claude Opus 4.5)
    """
    cursor = None
    is_mariadb = _is_mariadb()
    
    try:
        cursor = connection.cursor()
        
        # 테이블 확인 및 생성
        table_name = "WATCHLIST_HISTORY"
        
        if is_mariadb:
            # MariaDB: 테이블 존재 여부 확인
            cursor.execute(f"SHOW TABLES LIKE '{table_name}'")
            if not cursor.fetchone():
                logger.warning(f"⚠️ 테이블 {table_name}이 없어 생성을 시도합니다.")
                create_sql = f"""
                CREATE TABLE {table_name} (
                    SNAPSHOT_DATE DATE NOT NULL,
                    STOCK_CODE VARCHAR(16) NOT NULL,
                    STOCK_NAME VARCHAR(128),
                    IS_TRADABLE TINYINT DEFAULT 1,
                    LLM_SCORE INT,
                    LLM_REASON TEXT,
                    CREATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (SNAPSHOT_DATE, STOCK_CODE)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
                cursor.execute(create_sql)
                logger.info(f"✅ 테이블 {table_name} 생성 완료")
        else:
            # Oracle: ROWNUM 사용
            try:
                cursor.execute(f"SELECT 1 FROM {table_name} WHERE ROWNUM=1")
            except Exception:
                logger.warning(f"⚠️ 테이블 {table_name}이 없어 생성을 시도합니다.")
                create_sql = f"""
                CREATE TABLE {table_name} (
                    SNAPSHOT_DATE DATE NOT NULL,
                    STOCK_CODE VARCHAR2(16) NOT NULL,
                    STOCK_NAME VARCHAR2(128),
                    IS_TRADABLE NUMBER(1) DEFAULT 1,
                    LLM_SCORE NUMBER,
                    LLM_REASON VARCHAR2(4000),
                    CREATED_AT TIMESTAMP DEFAULT SYSTIMESTAMP,
                    CONSTRAINT PK_{table_name} PRIMARY KEY (SNAPSHOT_DATE, STOCK_CODE)
                )
                """
                cursor.execute(create_sql)
                logger.info(f"✅ 테이블 {table_name} 생성 완료")

        if snapshot_date is None:
            snapshot_date = datetime.now().strftime('%Y-%m-%d')

        # 해당 날짜의 기존 데이터 삭제 (재실행 시 중복 방지)
        if is_mariadb:
            cursor.execute(f"DELETE FROM {table_name} WHERE SNAPSHOT_DATE = %s", (snapshot_date,))
        else:
            cursor.execute(f"DELETE /*+ NO_PARALLEL */ FROM {table_name} WHERE SNAPSHOT_DATE = TO_DATE(:1, 'YYYY-MM-DD')", [snapshot_date])
        
        if not candidates_to_save:
            connection.commit()
            return

        logger.info(f"   (DB) '{snapshot_date}' 기준 WatchList 히스토리 {len(candidates_to_save)}건 저장...")
        
        if is_mariadb:
            sql_insert = f"""
            INSERT INTO {table_name} (
                SNAPSHOT_DATE, STOCK_CODE, STOCK_NAME, IS_TRADABLE, LLM_SCORE, LLM_REASON
            ) VALUES (%s, %s, %s, %s, %s, %s)
            """
        else:
            sql_insert = f"""
            INSERT /*+ NO_PARALLEL */ INTO {table_name} (
                SNAPSHOT_DATE, STOCK_CODE, STOCK_NAME, IS_TRADABLE, LLM_SCORE, LLM_REASON
            ) VALUES (
                TO_DATE(:1, 'YYYY-MM-DD'), :2, :3, :4, :5, :6
            )
            """
        
        insert_data = []
        for c in candidates_to_save:
            llm_score = c.get('llm_score', 0)
            llm_reason = c.get('llm_reason', '')
            if len(llm_reason) > 3950:
                llm_reason = llm_reason[:3950] + "..."
                
            insert_data.append((
                snapshot_date,
                c['code'],
                c['name'],
                1 if c.get('is_tradable', True) else 0,
                llm_score,
                llm_reason
            ))
            
        cursor.executemany(sql_insert, insert_data)
        connection.commit()
        logger.info(f"   (DB) ✅ WatchList History 저장 완료")
        
    except Exception as e:
        logger.error(f"❌ DB: save_to_watchlist_history 실패! (에러: {e})")
        if connection: connection.rollback()
    finally:
        if cursor: cursor.close()

def get_watchlist_history(connection, snapshot_date):
    """
    [v3.5] 특정 날짜의 WatchList 히스토리를 조회합니다.
    """
    watchlist = {}
    cursor = None
    try:
        cursor = connection.cursor()
        sql = """
        SELECT STOCK_CODE, STOCK_NAME, IS_TRADABLE, LLM_SCORE, LLM_REASON
        FROM WATCHLIST_HISTORY
        WHERE SNAPSHOT_DATE = TO_DATE(:1, 'YYYY-MM-DD')
        """
        cursor.execute(sql, [snapshot_date])
        for row in cursor:
            watchlist[row[0]] = {
                "name": row[1], 
                "is_tradable": bool(row[2]),
                "llm_score": row[3] if row[3] is not None else 0,
                "llm_reason": row[4] if row[4] is not None else ""
            }
        
        if watchlist:
            logger.info(f"✅ DB: {snapshot_date} WatchList History {len(watchlist)}개 로드 성공")
        else:
            logger.debug(f"ℹ️ DB: {snapshot_date} WatchList History 데이터 없음")
            
        return watchlist
    except Exception as e:
        logger.error(f"❌ DB: get_watchlist_history 실패! (에러: {e})")
        return {}
    finally:
        if cursor: cursor.close()

# --- (get_daily_prices, get_active_watchlist, get_today_total_buy_amount - 기존과 동일) ---
def get_daily_prices(connection, stock_code, limit=30, table_name="STOCK_DAILY_PRICES_3Y"):
    """
    특정 종목의 일봉 데이터를 조회합니다. (SQLAlchemy 사용)
    
    Args:
        connection: DB 연결 (Legacy, 무시됨 - SQLAlchemy 세션 사용)
        stock_code: 종목 코드
        limit: 조회할 일수 (기본값 30)
        table_name: 조회할 테이블 이름 (기본값 STOCK_DAILY_PRICES_3Y)
        
    Returns:
        DataFrame: 일봉 데이터 (날짜 오름차순 정렬)
    """
    try:
        from sqlalchemy import text
        
        # 테이블 이름 유효성 검사 (SQL Injection 방지)
        if table_name not in ["STOCK_DAILY_PRICES", "STOCK_DAILY_PRICES_3Y"]:
            logger.warning(f"⚠️ 허용되지 않은 테이블 이름: {table_name}. 기본값 사용.")
            table_name = "STOCK_DAILY_PRICES_3Y"

        with sa_connection.get_session() as session:
            # DB 타입에 따라 SQL 분기
            if _is_mariadb():
                sql = text(f"""
                    SELECT PRICE_DATE, OPEN_PRICE, CLOSE_PRICE, HIGH_PRICE, LOW_PRICE, VOLUME
                    FROM (
                        SELECT PRICE_DATE, OPEN_PRICE, CLOSE_PRICE, HIGH_PRICE, LOW_PRICE, VOLUME
                        FROM {table_name}
                        WHERE stock_code = :stock_code
                        ORDER BY price_date DESC
                        LIMIT :limit_val
                    ) sub
                    ORDER BY PRICE_DATE ASC
                """)
            else:
                # Oracle
                sql = text(f"""
                    SELECT PRICE_DATE, OPEN_PRICE, CLOSE_PRICE, HIGH_PRICE, LOW_PRICE, VOLUME
                    FROM (
                        SELECT PRICE_DATE, OPEN_PRICE, CLOSE_PRICE, HIGH_PRICE, LOW_PRICE, VOLUME
                        FROM {table_name}
                        WHERE stock_code = :stock_code
                        ORDER BY price_date DESC
                        FETCH FIRST :limit_val ROWS ONLY
                    )
                    ORDER BY PRICE_DATE ASC
                """)
            
            result = session.execute(sql, {"stock_code": stock_code, "limit_val": limit})
            rows = result.fetchall()
            
            if not rows:
                return pd.DataFrame()
            
            df = pd.DataFrame(rows, columns=['PRICE_DATE', 'OPEN_PRICE', 'CLOSE_PRICE', 'HIGH_PRICE', 'LOW_PRICE', 'VOLUME'])
            
            # 숫자형 변환
            for col in ['OPEN_PRICE', 'CLOSE_PRICE', 'HIGH_PRICE', 'LOW_PRICE', 'VOLUME']:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            
            return df
    except Exception as e:
        # 연결 끊김 오류는 상위로 전파하여 Pool을 리셋하도록 함
        if "DPY-1001" in str(e) or "not connected" in str(e):
            raise
            
        logger.error(f"❌ DB: get_daily_prices ({stock_code}) 실패! (에러: {e})")
        return pd.DataFrame()

def get_daily_prices_batch(connection, stock_codes: list, limit=120, table_name="STOCK_DAILY_PRICES_3Y"):
    """
    여러 종목의 일봉 데이터를 한 번에 조회합니다. (SQLAlchemy 사용)
    
    Args:
        connection: DB 연결 (Legacy, 무시됨 - SQLAlchemy 세션 사용)
        stock_codes: 종목 코드 리스트
        limit: 조회할 일수
        table_name: 조회할 테이블 이름 (기본값 STOCK_DAILY_PRICES_3Y)
        
    Returns:
        dict: {stock_code: DataFrame} 형태의 딕셔너리
    """
    prices_dict = {}
    
    if not stock_codes:
        logger.warning("⚠️ DB: get_daily_prices_batch - 빈 종목 코드 리스트")
        return {}
    
    # 테이블 이름 유효성 검사
    if table_name not in ["STOCK_DAILY_PRICES", "STOCK_DAILY_PRICES_3Y"]:
        logger.warning(f"⚠️ 허용되지 않은 테이블 이름: {table_name}. 기본값 사용.")
        table_name = "STOCK_DAILY_PRICES_3Y"

    try:
        from sqlalchemy import text
        
        with sa_connection.get_session() as session:
            # 배치 사이즈 제한
            batch_size = 500
            all_results = []
            
            for i in range(0, len(stock_codes), batch_size):
                batch_codes = stock_codes[i:i + batch_size]
                placeholders = ','.join([f':code_{j}' for j in range(len(batch_codes))])
                
                # DB 타입에 따라 SQL 분기
                if _is_mariadb():
                    sql = text(f"""
                        SELECT STOCK_CODE, PRICE_DATE, CLOSE_PRICE, HIGH_PRICE, LOW_PRICE, VOLUME
                        FROM (
                            SELECT STOCK_CODE, PRICE_DATE, CLOSE_PRICE, HIGH_PRICE, LOW_PRICE, VOLUME,
                                   ROW_NUMBER() OVER (PARTITION BY STOCK_CODE ORDER BY PRICE_DATE DESC) as rn
                            FROM {table_name}
                            WHERE STOCK_CODE IN ({placeholders})
                        ) sub
                        WHERE rn <= :limit_val
                        ORDER BY STOCK_CODE, PRICE_DATE ASC
                    """)
                else:
                    # Oracle
                    sql = text(f"""
                        SELECT STOCK_CODE, PRICE_DATE, CLOSE_PRICE, HIGH_PRICE, LOW_PRICE, VOLUME
                        FROM (
                            SELECT STOCK_CODE, PRICE_DATE, CLOSE_PRICE, HIGH_PRICE, LOW_PRICE, VOLUME,
                                   ROW_NUMBER() OVER (PARTITION BY STOCK_CODE ORDER BY PRICE_DATE DESC) as rn
                            FROM {table_name}
                            WHERE STOCK_CODE IN ({placeholders})
                        )
                        WHERE rn <= :limit_val
                        ORDER BY STOCK_CODE, PRICE_DATE ASC
                    """)
                
                params = {f'code_{j}': code for j, code in enumerate(batch_codes)}
                params['limit_val'] = limit
                
                result = session.execute(sql, params)
                all_results.extend(result.fetchall())
            
            # 결과를 종목별로 그룹화
            for row in all_results:
                stock_code = row[0]
                if stock_code not in prices_dict:
                    prices_dict[stock_code] = []
                prices_dict[stock_code].append({
                    'PRICE_DATE': row[1],
                    'CLOSE_PRICE': row[2],
                    'HIGH_PRICE': row[3],
                    'LOW_PRICE': row[4],
                    'VOLUME': row[5]
                })
            
            # DataFrame으로 변환
            for stock_code in prices_dict:
                df = pd.DataFrame(prices_dict[stock_code])
                if not df.empty:
                    for col in ['CLOSE_PRICE', 'HIGH_PRICE', 'LOW_PRICE', 'VOLUME']:
                        df[col] = pd.to_numeric(df[col], errors='coerce')
                prices_dict[stock_code] = df
            
            logger.info(f"✅ DB: 배치 조회 완료 ({len(stock_codes)}개 종목, {limit}일치, 실제 조회: {len(prices_dict)}개)")
            return prices_dict
    except Exception as e:
        logger.error(f"❌ DB: get_daily_prices_batch 실패! (에러: {e})", exc_info=True)
        return {}

def get_active_watchlist(connection):
    if _is_sqlalchemy_ready():
        try:
            with sa_connection.session_scope(readonly=True) as session:
                return sa_repository.get_active_watchlist(session)
        except Exception as exc:
            logger.error("❌ [SQLAlchemy] WatchList 조회 실패 - legacy로 fallback: %s", exc, exc_info=True)
    return _get_active_watchlist_legacy(connection)


def _get_active_watchlist_legacy(connection):
    watchlist = {}
    cursor = None
    try:
        cursor = connection.cursor()
        sql = """
        SELECT stock_code, stock_name, is_tradable, per, pbr, market_cap,
               LLM_SCORE, LLM_REASON
        FROM WatchList
        """
        cursor.execute(sql)
        metadata_marker = "[LLM_METADATA]"
        for row in cursor:
            llm_reason = row[7] if row[7] is not None else ""
            metadata_payload = {}
            clean_reason = llm_reason
            if metadata_marker in llm_reason:
                base, metadata_raw = llm_reason.split(metadata_marker, 1)
                clean_reason = base.strip()
                try:
                    metadata_payload = json.loads(metadata_raw.strip())
                except Exception as e:
                    logger.warning(f"⚠️ LLM 메타데이터 파싱 실패: {e}")

            watchlist[row[0]] = {
                "name": row[1], "is_tradable": bool(row[2]),
                "per": row[3], "pbr": row[4], "market_cap": row[5],
                "llm_score": row[6] if row[6] is not None else 0,
                "llm_reason": clean_reason,
                "llm_metadata": metadata_payload,
                "llm_grade": metadata_payload.get("llm_grade"),
                "bear_strategy": metadata_payload.get("bear_strategy"),
            }
        logger.info(f"✅ DB(Legacy): WatchList {len(watchlist)}개 로드 성공!")
        return watchlist
    except Exception as e:
        logger.error(f"❌ DB(Legacy): get_active_watchlist 실패! (에러: {e})")
        return {}
    finally:
        if cursor: cursor.close()

def get_today_total_buy_amount(connection):
    if _is_sqlalchemy_ready():
        try:
            with sa_connection.session_scope(readonly=True) as session:
                return sa_repository.get_today_total_buy_amount(session)
        except Exception as exc:
            logger.error("❌ [SQLAlchemy] 오늘 총 매수 금액 조회 실패 - legacy fallback: %s", exc, exc_info=True)
    return _get_today_total_buy_amount_legacy(connection)


def _get_today_total_buy_amount_legacy(connection):
    cursor = None
    try:
        cursor = connection.cursor()
        tradelog_table = _get_table_name("TradeLog")
        sql = f"SELECT SUM(price * quantity) FROM {tradelog_table} WHERE trade_type = 'BUY' AND TRUNC(trade_timestamp) = TRUNC(SYSDATE)"
        cursor.execute(sql)
        result = cursor.fetchone()
        total_amount = result[0] if result and result[0] is not None else 0
        logger.info(f"✅ DB(Legacy): 오늘 총 매수 금액 {total_amount:,.0f}원 로드 성공!")
        return float(total_amount)
    except Exception as e:
        logger.error(f"❌ DB(Legacy): get_today_total_buy_amount 실패! (에러: {e})")
        return 0.0
    finally:
        if cursor: cursor.close()

def get_today_buy_count(connection):
    """오늘 매수한 종목 수 반환"""
    if _is_sqlalchemy_ready():
        try:
            with sa_connection.session_scope(readonly=True) as session:
                return sa_repository.get_today_buy_count(session)
        except Exception as exc:
            logger.error("❌ [SQLAlchemy] 오늘 매수 건수 조회 실패 - legacy fallback: %s", exc, exc_info=True)
    return _get_today_buy_count_legacy(connection)


def _get_today_buy_count_legacy(connection):
    cursor = None
    try:
        cursor = connection.cursor()
        tradelog_table = _get_table_name("TradeLog")
        sql = f"SELECT COUNT(*) FROM {tradelog_table} WHERE trade_type = 'BUY' AND TRUNC(trade_timestamp) = TRUNC(SYSDATE)"
        cursor.execute(sql)
        result = cursor.fetchone()
        buy_count = result[0] if result and result[0] is not None else 0
        logger.info(f"✅ DB(Legacy): 오늘 매수 종목 수 {buy_count}개 로드 성공!")
        return int(buy_count)
    except Exception as e:
        logger.error(f"❌ DB(Legacy): get_today_buy_count 실패! (에러: {e})")
        return 0
    finally:
        if cursor: cursor.close()

def get_trade_logs(connection, date=None):
    """
    특정 날짜의 거래 내역 조회
    """
    if _is_sqlalchemy_ready():
        try:
            with sa_connection.session_scope(readonly=True) as session:
                return sa_repository.get_trade_logs(session, date)
        except Exception as exc:
            logger.error("❌ [SQLAlchemy] 거래 내역 조회 실패 - legacy fallback: %s", exc, exc_info=True)
    return _get_trade_logs_legacy(connection, date)


def _get_trade_logs_legacy(connection, date=None):
    logs = []
    cursor = None
    try:
        cursor = connection.cursor()
        tradelog_table = _get_table_name("TradeLog")
        
        if date:
            condition = "TRUNC(trade_timestamp) = TO_DATE(:1, 'YYYY-MM-DD')"
            params = [date]
        else:
            condition = "TRUNC(trade_timestamp) = TRUNC(SYSDATE)"
            params = []
            
        sql = f"""
        SELECT stock_code, trade_type, quantity, price, KEY_METRICS_JSON
        FROM {tradelog_table}
        WHERE {condition}
        ORDER BY trade_timestamp DESC
        """
        cursor.execute(sql, params)
        
        for row in cursor:
            key_metrics = {}
            try:
                if row[4]:
                    key_metrics = json.loads(row[4].read() if hasattr(row[4], 'read') else row[4])
            except Exception as e:
                logger.warning(f"⚠️ JSON 파싱 오류: {e}")
                
            profit_amount = float(key_metrics.get('profit_amount', 0.0))
            
            logs.append({
                'code': row[0],
                'action': row[1],
                'quantity': int(row[2]),
                'price': float(row[3]),
                'profit_amount': profit_amount
            })
            
        logger.info(f"✅ DB(Legacy): 거래 내역 {len(logs)}건 조회 성공 ({date or '오늘'})")
        return logs
    except Exception as e:
        logger.error(f"❌ DB(Legacy): get_trade_logs 실패! (에러: {e})")
        return []
    finally:
        if cursor: cursor.close()

def get_stock_sector(connection, stock_code: str) -> str:
    """종목의 섹터 정보 반환"""
    cursor = None
    try:
        cursor = connection.cursor()
        # WatchList 테이블에서 섹터 정보 조회 (STOCK_CODE 컬럼 사용)
        sql = "SELECT SECTOR FROM WatchList WHERE STOCK_CODE = :1"
        cursor.execute(sql, [stock_code])
        result = cursor.fetchone()
        
        if result and result[0]:
            sector = result[0]
            logger.info(f"✅ DB: {stock_code} 섹터 조회 성공 → {sector}")
            return sector
        else:
            logger.warning(f"⚠️ DB: {stock_code} 섹터 정보 없음, 기본값 'UNKNOWN' 반환")
            return "UNKNOWN"
    except Exception as e:
        logger.error(f"❌ DB: get_stock_sector({stock_code}) 실패! (에러: {e})")
        return "UNKNOWN"
    finally:
        if cursor: cursor.close()

# --- (get_active_portfolio, update_portfolio_state_and_stoploss - 기존과 동일) ---
def _get_active_portfolio_impl(connection):
    """get_active_portfolio의 실제 구현 (재시도 로직 적용 가능)"""
    if _is_sqlalchemy_ready():
        try:
            with sa_connection.session_scope(readonly=True) as session:
                return sa_repository.get_active_portfolio(session)
        except Exception as exc:
            logger.error("❌ [SQLAlchemy] Active Portfolio 조회 실패 - legacy로 fallback: %s", exc, exc_info=True)
    return _get_active_portfolio_impl_legacy(connection)


def _get_active_portfolio_impl_legacy(connection):
    portfolio = []
    cursor = None
    try:
        cursor = connection.cursor()
        portfolio_table = _get_table_name("Portfolio")
        
        # CREATED_AT 컬럼 조회 (시간 기반 매도 로직용)
        sql = f"""
        SELECT id, stock_code, stock_name, quantity, average_buy_price, current_high_price,
               SELL_STATE, STOP_LOSS_PRICE, CREATED_AT
        FROM {portfolio_table} 
        WHERE status = 'HOLDING'
        ORDER BY id ASC
        """
        cursor.execute(sql)
        for row in cursor:
            portfolio.append({
                "id": row[0], "code": row[1], "name": row[2], "quantity": row[3],
                "avg_price": float(row[4]), "high_price": float(row[5]),
                "sell_state": row[6],
                "stop_loss_price": float(row[7]) if row[7] is not None else 0.0,
                "created_at": row[8]  # 매수 날짜 (시간 기반 매도 로직용)
            })
        
        logger.info(f"✅ DB(Legacy): 보유(ACTIVE) 포트폴리오 {len(portfolio)}개 로드 성공!")
        return portfolio
    except Exception as e:
        logger.error(f"❌ DB(Legacy): get_active_portfolio 실패! (에러: {e})")
        return []
    finally:
        if cursor: cursor.close()

def get_active_portfolio(connection):
    """보유 포트폴리오 조회"""
    return _get_active_portfolio_impl(connection)

def update_portfolio_status(connection, portfolio_id, status):
    """
    Portfolio 상태 변경 (수동 매도 등에서 사용)
    
    Args:
        connection: DB 연결 객체
        portfolio_id: Portfolio ID
        status: 새로운 상태 ('SOLD', 'HOLDING' 등)
    
    Returns:
        bool: 성공 여부
    """
    cursor = None
    try:
        cursor = connection.cursor()
        portfolio_table = _get_table_name("Portfolio")
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



# -----------------------------------------------------------
# execute_trade_and_log 함수
# -----------------------------------------------------------
def execute_trade_and_log(
    connection, trade_type, stock_info, quantity, price, llm_decision,
    initial_stop_loss_price=None,
    strategy_signal: str = None,
    key_metrics_dict: dict = None,
    market_context_dict: dict = None
):
    if _is_sqlalchemy_ready():
        try:
            with sa_connection.session_scope() as session:
                return _execute_trade_and_log_sqlalchemy(
                    session, trade_type, stock_info, quantity, price, llm_decision,
                    initial_stop_loss_price, strategy_signal, key_metrics_dict, market_context_dict
                )
        except Exception as exc:
            logger.error("❌ [SQLAlchemy] execute_trade_and_log 실패 - legacy fallback: %s", exc, exc_info=True)
    return _execute_trade_and_log_legacy(
        connection, trade_type, stock_info, quantity, price, llm_decision,
        initial_stop_loss_price, strategy_signal, key_metrics_dict, market_context_dict
    )


def _execute_trade_and_log_sqlalchemy(
    session, trade_type, stock_info, quantity, price, llm_decision,
    initial_stop_loss_price, strategy_signal, key_metrics_dict, market_context_dict
):
    if price <= 0:
        logger.error("❌ [SQLAlchemy] price가 유효하지 않습니다. (price=%s, code=%s)", price, stock_info.get("code"))
        return False

    llm_reason = llm_decision.get('reason', 'N/A') if llm_decision else 'N/A'
    MAX_REASON_LENGTH = 1950
    if len(llm_reason) > MAX_REASON_LENGTH:
        llm_reason = llm_reason[:MAX_REASON_LENGTH-3] + '...'
        logger.warning("⚠️ [SQLAlchemy] REASON 길이 초과로 truncate 수행")

    def convert_numpy_types(obj):
        import numpy as np
        if isinstance(obj, dict):
            return {k: convert_numpy_types(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [convert_numpy_types(item) for item in obj]
        if isinstance(obj, (np.integer, np.int64)):
            return int(obj)
        if isinstance(obj, (np.floating, np.float64)):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return obj

    key_metrics_json = json.dumps(convert_numpy_types(key_metrics_dict or {}))
    market_context_json = json.dumps(convert_numpy_types(market_context_dict or {}))

    from shared.db import models as db_models

    new_portfolio_id = None
    if trade_type.startswith('BUY'):
        existing = (
            session.query(db_models.Portfolio)
            .filter(db_models.Portfolio.stock_code == stock_info['code'])
            .filter(db_models.Portfolio.status == 'HOLDING')
            .order_by(db_models.Portfolio.id.asc())
            .first()
        )
        if existing:
            new_quantity = existing.quantity + quantity
            new_total_amount = (existing.total_buy_amount or 0) + (quantity * price)
            new_avg_price = new_total_amount / new_quantity if new_quantity > 0 else price
            new_high_price = max(existing.current_high_price or price, price)
            if initial_stop_loss_price is None:
                initial_stop_loss_price = price * 0.93
            new_stop_loss = min(
                existing.stop_loss_price if existing.stop_loss_price and existing.stop_loss_price > 0 else initial_stop_loss_price,
                initial_stop_loss_price
            )
            new_sell_state = existing.sell_state if existing.sell_state in ('SECURED', 'TRAILING') else 'INITIAL'

            existing.quantity = new_quantity
            existing.average_buy_price = new_avg_price
            existing.total_buy_amount = new_total_amount
            existing.current_high_price = new_high_price
            existing.stop_loss_price = new_stop_loss
            existing.sell_state = new_sell_state
            new_portfolio_id = existing.id
            logger.info("   (SQLAlchemy) Portfolio 업데이트 (ID=%s, qty=%s, avg=%.2f)", new_portfolio_id, new_quantity, new_avg_price)
        else:
            if initial_stop_loss_price is None:
                initial_stop_loss_price = price * 0.93
            portfolio = db_models.Portfolio(
                stock_code=stock_info['code'],
                stock_name=stock_info['name'],
                quantity=quantity,
                average_buy_price=price,
                total_buy_amount=quantity * price,
                current_high_price=price,
                status='HOLDING',
                sell_state='INITIAL',
                stop_loss_price=initial_stop_loss_price,
            )
            session.add(portfolio)
            session.flush()
            new_portfolio_id = portfolio.id
            logger.info("   (SQLAlchemy) 새 Portfolio 생성 (ID=%s)", new_portfolio_id)
    elif trade_type == 'SELL':
        portfolio = session.get(db_models.Portfolio, stock_info['id'])
        if portfolio:
            portfolio.status = 'SOLD'
            portfolio.sell_state = 'SOLD'
            new_portfolio_id = portfolio.id

    trade_log = db_models.TradeLog(
        portfolio_id=new_portfolio_id,
        stock_code=stock_info['code'],
        trade_type=trade_type,
        quantity=quantity,
        price=price,
        reason=llm_reason,
        strategy_signal=strategy_signal,
        key_metrics_json=key_metrics_json,
        market_context_json=market_context_json,
    )
    session.add(trade_log)
    logger.info("   (SQLAlchemy) TradeLog 저장 (portfolio_id=%s, type=%s)", new_portfolio_id, trade_type)
    return True


def _execute_trade_and_log_legacy(
    connection, trade_type, stock_info, quantity, price, llm_decision,
    initial_stop_loss_price, strategy_signal, key_metrics_dict, market_context_dict
):
    """MariaDB 전용 거래 실행 및 로깅"""
    cursor = None
    try:
        if price <= 0:
            logger.error(f"❌ DB: execute_trade_and_log 호출 시 price가 유효하지 않습니다. (price: {price}, stock_code: {stock_info.get('code', 'N/A')})")
            return False
        
        cursor = connection.cursor()
        llm_reason = llm_decision.get('reason', 'N/A') if llm_decision else 'N/A'
        
        MAX_REASON_LENGTH = 1950
        original_reason_length = len(llm_reason)
        if original_reason_length > MAX_REASON_LENGTH:
            llm_reason = llm_reason[:MAX_REASON_LENGTH-3] + '...'
            logger.warning(f"⚠️ DB: REASON이 {MAX_REASON_LENGTH}자를 초과하여 잘렸습니다. (원본: {original_reason_length}자 → 저장: {len(llm_reason)}자)")
        
        new_portfolio_id = None
        
        def convert_numpy_types(obj):
            import numpy as np
            if isinstance(obj, dict):
                return {k: convert_numpy_types(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [convert_numpy_types(item) for item in obj]
            elif isinstance(obj, (np.integer, np.int64)):
                return int(obj)
            elif isinstance(obj, (np.floating, np.float64)):
                return float(obj)
            elif isinstance(obj, np.ndarray):
                return obj.tolist()
            else:
                return obj
        
        key_metrics_json = json.dumps(convert_numpy_types(key_metrics_dict or {}))
        market_context_json = json.dumps(convert_numpy_types(market_context_dict or {}))

        portfolio_table = _get_table_name("Portfolio")
        tradelog_table = _get_table_name("TradeLog")
        
        if trade_type.startswith('BUY'):
            # MariaDB: LIMIT 1 사용
            sql_check = f"""
            SELECT id, quantity, average_buy_price, total_buy_amount, current_high_price, STOP_LOSS_PRICE, SELL_STATE
            FROM {portfolio_table}
            WHERE stock_code = %s AND status = 'HOLDING'
            ORDER BY id ASC
            LIMIT 1
            """
            cursor.execute(sql_check, [stock_info['code']])
            existing = cursor.fetchone()
            
            if existing:
                # DictCursor 사용 시 컬럼명으로 접근
                existing_id = existing['id']
                existing_quantity = existing['quantity']
                existing_avg_price = existing['average_buy_price']
                existing_total_amount = existing['total_buy_amount']
                existing_high_price = existing['current_high_price']
                existing_stop_loss = existing['STOP_LOSS_PRICE']
                existing_sell_state = existing['SELL_STATE']
                
                new_quantity = existing_quantity + quantity
                new_total_amount = existing_total_amount + (quantity * price)
                new_avg_price = new_total_amount / new_quantity if new_quantity > 0 else price
                new_high_price = max(existing_high_price if existing_high_price else price, price)
                
                if initial_stop_loss_price is None:
                    initial_stop_loss_price = price * 0.93
                new_stop_loss = min(existing_stop_loss if existing_stop_loss and existing_stop_loss > 0 else initial_stop_loss_price, initial_stop_loss_price)
                
                new_sell_state = existing_sell_state if existing_sell_state in ('SECURED', 'TRAILING') else 'INITIAL'
                
                sql_update = f"""
                UPDATE {portfolio_table}
                SET quantity = %s,
                    average_buy_price = %s,
                    total_buy_amount = %s,
                    current_high_price = %s,
                    STOP_LOSS_PRICE = %s,
                    SELL_STATE = %s
                WHERE id = %s
                """
                cursor.execute(sql_update, [
                    new_quantity,
                    new_avg_price,
                    new_total_amount,
                    new_high_price,
                    new_stop_loss,
                    new_sell_state,
                    existing_id
                ])
                new_portfolio_id = existing_id
                logger.info(f"   (DB) 기존 Portfolio 레코드 업데이트 (ID: {existing_id}, 수량: {existing_quantity}주 → {new_quantity}주, 평균가: {existing_avg_price:,.0f}원 → {new_avg_price:,.0f}원, SELL_STATE: {existing_sell_state} → {new_sell_state})")
                logger.info(f"   (DB) [상세] 기존 total_buy_amount: {existing_total_amount:,.0f}원, 추가 매수 금액: {quantity * price:,.0f}원, 새 total_buy_amount: {new_total_amount:,.0f}원")
            else:
                # MariaDB: lastrowid 사용
                sql_portfolio = f"""
                INSERT INTO {portfolio_table} (
                    stock_code, stock_name, quantity, average_buy_price, total_buy_amount, 
                    current_high_price, status, SELL_STATE, STOP_LOSS_PRICE
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, 'HOLDING', 'INITIAL', %s
                )
                """
                if initial_stop_loss_price is None:
                    initial_stop_loss_price = price * 0.93 # Fallback
                cursor.execute(sql_portfolio, [
                    stock_info['code'], stock_info['name'], quantity, price, quantity * price, 
                    price, initial_stop_loss_price
                ])
                new_portfolio_id = cursor.lastrowid
                logger.info(f"   (DB) 새 Portfolio 레코드 생성 (ID: {new_portfolio_id}, average_buy_price: {price:,.0f}원, quantity: {quantity}주)")
        elif trade_type == 'SELL':
            sql_portfolio = f"UPDATE {portfolio_table} SET status = 'SOLD', SELL_STATE = 'SOLD' WHERE id = %s"
            cursor.execute(sql_portfolio, [stock_info['id']])
            new_portfolio_id = stock_info['id']

        sql_log = f"""
        INSERT INTO {tradelog_table} (
            portfolio_id, stock_code, trade_type, quantity, price, reason, 
            trade_timestamp, 
            STRATEGY_SIGNAL, KEY_METRICS_JSON, MARKET_CONTEXT_JSON
        ) VALUES (
            %s, %s, %s, %s, %s, %s, NOW(),
            %s, %s, %s
        )
        """
        cursor.execute(sql_log, [
            new_portfolio_id, stock_info['code'], trade_type, quantity, price, llm_reason,
            strategy_signal,
            key_metrics_json,
            market_context_json
        ])
        logger.info(f"   (DB) TradeLog 저장: price={price:,.0f}원, quantity={quantity}주, portfolio_id={new_portfolio_id}")
        connection.commit()
        logger.info(f"✅ DB: '{trade_type}' 트랜잭션 성공 (Portfolio ID: {new_portfolio_id}, Signal: {strategy_signal}, Price: {price:,.0f}원)")
        return True
    except Exception as e:
        logger.error(f"❌ DB: execute_trade_and_log 실패! (에러: {e})")
        if connection: connection.rollback()
        return False
    finally:
        if cursor: cursor.close()

# --- (get_trade_log, get_config, set_config - 기존과 동일) ---
def get_trade_log(connection, limit=50):
    logs_df = None
    cursor = None
    try:
        cursor = connection.cursor()
        tradelog_table = _get_table_name("TradeLog")
        # Select additional columns for trade analysis
        # TRADE_TIMESTAMP를 한국시간(KST)으로 변환하여 반환
        # Oracle DB의 TIMESTAMP는 DB 서버 timezone을 따르므로, AT TIME ZONE으로 한국시간 변환
        sql = f"""
        SELECT LOG_ID, PORTFOLIO_ID, STOCK_CODE, TRADE_TYPE, QUANTITY, PRICE, 
               REASON, 
               CAST(TRADE_TIMESTAMP AT TIME ZONE 'Asia/Seoul' AS TIMESTAMP) AS TRADE_TIMESTAMP,
               STRATEGY_SIGNAL, KEY_METRICS_JSON, MARKET_CONTEXT_JSON
        FROM {tradelog_table}
        ORDER BY TRADE_TIMESTAMP DESC
        FETCH FIRST :1 ROWS ONLY
        """
        cursor.execute(sql, [limit])
        logs_df = pd.DataFrame(cursor.fetchall(), columns=[desc[0] for desc in cursor.description])
        logger.info(f"✅ DB: 최신 거래 내역 {len(logs_df)}건 로드 성공! (한국시간 기준)")
        return logs_df
    except Exception as e:
        logger.error(f"❌ DB: get_trade_log 실패! (에러: {e})")
        return pd.DataFrame()
    finally:
        if cursor: cursor.close()

def get_config(connection, config_key, silent=False):
    """
    CONFIG 테이블에서 설정값 조회 (SQLAlchemy ORM 사용)
    
    Args:
        connection: DB 연결 (Legacy, 무시됨 - SQLAlchemy 세션 사용)
        config_key: 설정 키
        silent: True이면 설정값이 없을 때 경고 로그를 남기지 않음 (기본값: False)
    
    Returns:
        설정값 (문자열) 또는 None
    """
    try:
        with sa_connection.get_session() as session:
            return sa_repository.get_config(session, config_key, silent)
    except Exception as e:
        logger.error(f"❌ DB: get_config ('{config_key}') 실패! (에러: {e})")
        return None

def get_all_config(connection):
    """
    CONFIG 테이블의 모든 설정값을 조회 (SQLAlchemy ORM 사용)
    
    Args:
        connection: DB 연결 (Legacy, 무시됨 - SQLAlchemy 세션 사용)
    
    Returns:
        dict: {CONFIG_KEY: CONFIG_VALUE} 형태의 딕셔너리
    """
    try:
        from shared.db.models import Config
        with sa_connection.get_session() as session:
            configs = session.query(Config).all()
            config_dict = {c.config_key: c.config_value for c in configs}
            logger.info(f"✅ DB: CONFIG 테이블에서 {len(config_dict)}개 설정값 조회 완료")
            return config_dict
    except Exception as e:
        logger.error(f"❌ DB: get_all_config 실패! (에러: {e})")
        return {}


def set_config(connection, config_key, config_value):
    """
    CONFIG 테이블에 설정값 저장 (SQLAlchemy ORM 사용, UPSERT)
    
    Args:
        connection: DB 연결 (Legacy, 무시됨 - SQLAlchemy 세션 사용)
        config_key: 설정 키
        config_value: 설정 값
    
    Returns:
        성공 여부
    """
    try:
        with sa_connection.get_session() as session:
            return sa_repository.set_config(session, config_key, config_value)
    except Exception as e:
        logger.error(f"❌ DB: set_config ('{config_key}') 실패! (에러: {e})")
        return False

# --- RAG 캐시 관련 함수 ---
def upsert_rag_cache(connection, stock_code, rag_context):
    """
    RAG 컨텍스트를 RAG_CACHE 테이블에 저장하거나 업데이트합니다. (UPSERT)
    """
    cursor = None
    try:
        cursor = connection.cursor()
        # MERGE 문을 사용하여 UPSERT 로직 구현
        sql_merge = """
        MERGE INTO RAG_CACHE t
        USING (SELECT :code AS stock_code, :context AS rag_context FROM DUAL) s
        ON (t.STOCK_CODE = s.stock_code)
        WHEN MATCHED THEN
            UPDATE SET t.RAG_CONTEXT = s.rag_context, t.LAST_UPDATED = SYSTIMESTAMP
        WHEN NOT MATCHED THEN
            INSERT (STOCK_CODE, RAG_CONTEXT, LAST_UPDATED)
            VALUES (s.stock_code, s.rag_context, SYSTIMESTAMP)
        """
        cursor.execute(sql_merge, code=stock_code, context=rag_context)
        connection.commit()
        return True
    except Exception as e:
        logger.error(f"❌ DB: upsert_rag_cache ('{stock_code}') 실패! (에러: {e})")
        if connection: connection.rollback()
        return False
    finally:
        if cursor: cursor.close()

def get_rag_context_from_cache(connection, stock_code):
    """
    RAG_CACHE 테이블에서 특정 종목의 컨텍스트를 조회합니다.
    [수정] 컨텍스트와 함께 마지막 업데이트 시간(LAST_UPDATED)도 반환합니다.
    """
    cursor = None
    try:
        cursor = connection.cursor()
        sql = "SELECT RAG_CONTEXT, LAST_UPDATED FROM RAG_CACHE WHERE STOCK_CODE = :1"
        cursor.execute(sql, [stock_code])
        result = cursor.fetchone()
        if result and result[0]:
            clob_data = result[0]
            last_updated = result[1]
            # (컨텍스트 텍스트, 마지막 업데이트 시간) 튜플로 반환
            return (clob_data.read(), last_updated)
        else:
            # 캐시에 데이터가 없는 경우
            return (None, None)
    except Exception as e:
        logger.error(f"❌ DB: get_rag_context_from_cache ('{stock_code}') 실패! (에러: {e})")
        return (None, None)
    finally:
        if cursor: cursor.close()

def get_rag_context_with_validation(connection, stock_code, max_age_hours=24):
    """
    RAG 컨텍스트를 조회하고 신선도 검증
    
    Args:
        connection: DB 연결
        stock_code: 종목 코드
        max_age_hours: 최대 유효 시간 (시간, 기본값 24시간)
    
    Returns:
        (rag_context: str, is_fresh: bool, last_updated: datetime or None)
    """
    from datetime import datetime, timezone, timedelta
    
    try:
        cached_context, last_updated = get_rag_context_from_cache(connection, stock_code)
        if not cached_context or not last_updated:
            return "최신 뉴스 없음", False, None
        
        # 타임존 처리
        if last_updated.tzinfo is None:
            last_updated_utc = last_updated.replace(tzinfo=timezone.utc)
        else:
            last_updated_utc = last_updated.astimezone(timezone.utc)
        
        age_hours = (datetime.now(timezone.utc) - last_updated_utc).total_seconds() / 3600
        is_fresh = age_hours < max_age_hours
        
        return cached_context if is_fresh else "최신 뉴스 없음", is_fresh, last_updated
    except Exception as e:
        logger.error(f"❌ DB: get_rag_context_with_validation ('{stock_code}') 실패! (에러: {e})")
        return "최신 뉴스 없음", False, None

# -------------------------------------

def was_traded_recently(connection, stock_code, hours=24):
    """
    특정 종목이 최근 N시간 이내에 거래(매수/매도)되었는지 확인합니다.
    
    Args:
        connection: Oracle DB 연결 객체
        stock_code (str): 확인할 종목 코드
        hours (int): 확인할 시간 범위 (기본값: 24시간)
        
    Returns:
        bool: 최근 거래 이력이 있으면 True, 없으면 False
    """
    if _is_sqlalchemy_ready():
        try:
            with sa_connection.session_scope(readonly=True) as session:
                return sa_repository.was_traded_recently(session, stock_code, hours)
        except Exception as exc:
            logger.error("❌ [SQLAlchemy] was_traded_recently 실패 - legacy fallback (%s): %s", stock_code, exc, exc_info=True)
    try:
        with connection.cursor() as cursor:
            tradelog_table = _get_table_name("TradeLog")
            sql = f"""
            SELECT 1
            FROM {tradelog_table}
            WHERE STOCK_CODE = :stock_code
              AND TRADE_TIMESTAMP >= SYSTIMESTAMP - INTERVAL '1' HOUR * :hours
              AND ROWNUM = 1
            """
            cursor.execute(sql, stock_code=stock_code, hours=hours)
            result = cursor.fetchone()
            return result is not None
    except Exception as e:
        logger.error(f"❌ DB: was_traded_recently 확인 중 오류 발생 ({stock_code}): {e}")
        return False

def get_recently_traded_stocks_batch(connection, stock_codes: list, hours: int = 24) -> set:
    """
    여러 종목의 최근 거래 여부를 한 번에 조회합니다.
    
    Args:
        connection: DB 연결
        stock_codes: 종목 코드 리스트
        hours: 확인할 시간 범위 (기본값: 24시간)
    
    Returns:
        set: 최근 거래된 종목 코드 집합
    """
    if not stock_codes:
        return set()
    
    if _is_sqlalchemy_ready():
        try:
            with sa_connection.session_scope(readonly=True) as session:
                result = sa_repository.get_recently_traded_stocks_batch(session, stock_codes, hours)
                logger.info("✅ [SQLAlchemy] 최근 거래 종목 배치 조회 완료 (%d/%d)", len(result), len(stock_codes))
                return result
        except Exception as exc:
            logger.error("❌ [SQLAlchemy] get_recently_traded_stocks_batch 실패 - legacy fallback: %s", exc, exc_info=True)
    cursor = None
    try:
        cursor = connection.cursor()
        batch_size = 1000
        all_results = set()
        
        tradelog_table = _get_table_name("TradeLog")
        for i in range(0, len(stock_codes), batch_size):
            batch_codes = stock_codes[i:i + batch_size]
            placeholders = ','.join([f':{j+1}' for j in range(len(batch_codes))])
            hours_param_pos = len(batch_codes) + 1
            hours_placeholder = f':{hours_param_pos}'
            
            sql = f"""
            SELECT DISTINCT STOCK_CODE
            FROM {tradelog_table}
            WHERE STOCK_CODE IN ({placeholders})
              AND TRADE_TIMESTAMP >= SYSTIMESTAMP - INTERVAL '1' HOUR * {hours_placeholder}
            """
            params = list(batch_codes) + [hours]
            cursor.execute(sql, params)
            results = cursor.fetchall()
            all_results.update([row[0] for row in results])
        
        logger.info(f"✅ DB(Legacy): 최근 거래 종목 배치 조회 완료 ({len(stock_codes)}개 중 {len(all_results)}개)")
        return all_results
    except Exception as e:
        logger.error(f"❌ DB(Legacy): get_recently_traded_stocks_batch 실패! (에러: {e})")
        return set()
    finally:
        if cursor: cursor.close()

# ============================================================================
# AGENT_COMMANDS 관련 함수 (App과 Agent 간 비동기 명령 전달)
# ============================================================================

def create_agent_command(connection, command_type: str, payload: dict, requested_by: str = None, priority: int = 5):
    """
    Agent 명령 생성 (App → Agent 명령 전달)
    
    Args:
        connection: DB 연결 객체
        command_type: 명령 타입 ('MANUAL_SELL', 'MANUAL_BUY', etc.)
        payload: JSON 형식의 명령 파라미터 (dict)
        requested_by: 요청자 (App 사용자 email 등)
        priority: 우선순위 (1=최고, 10=최저, 기본값=5)
    
    Returns:
        command_id: 생성된 명령 ID
    """
    cursor = None
    try:
        commands_table = _get_table_name("AGENT_COMMANDS")
        cursor = connection.cursor()
        
        # JSON 직렬화
        import json
        payload_json = json.dumps(payload, ensure_ascii=False)
        
        sql = f"""
        INSERT INTO {commands_table} (COMMAND_TYPE, PAYLOAD, REQUESTED_BY, PRIORITY)
        VALUES (:cmd_type, :payload, :requested_by, :priority)
        RETURNING COMMAND_ID INTO :cmd_id
        """
        
        cmd_id_var = cursor.var(int)
        cursor.execute(sql, {
            'cmd_type': command_type,
            'payload': payload_json,
            'requested_by': requested_by,
            'priority': priority,
            'cmd_id': cmd_id_var
        })
        connection.commit()
        
        command_id = cmd_id_var.getvalue()[0]
        logger.info(f"✅ DB: Agent 명령 생성 완료 (ID: {command_id}, Type: {command_type})")
        return command_id
        
    except Exception as e:
        logger.error(f"❌ DB: create_agent_command 실패! (에러: {e})")
        connection.rollback()
        raise
    finally:
        if cursor: cursor.close()


def get_pending_agent_commands(connection, limit: int = 100):
    """
    대기 중인 Agent 명령 조회 (STATUS='PENDING')
    우선순위(PRIORITY) 높은 순, 생성 시간 빠른 순으로 정렬
    
    Args:
        connection: DB 연결 객체
        limit: 최대 조회 개수
    
    Returns:
        list of dict: 명령 목록
    """
    cursor = None
    try:
        commands_table = _get_table_name("AGENT_COMMANDS")
        cursor = connection.cursor()
        
        sql = f"""
        SELECT COMMAND_ID, COMMAND_TYPE, PAYLOAD, PRIORITY, REQUESTED_BY, CREATED_AT, RETRY_COUNT
        FROM {commands_table}
        WHERE STATUS = 'PENDING'
        ORDER BY PRIORITY ASC, CREATED_AT ASC
        FETCH FIRST :limit ROWS ONLY
        """
        
        cursor.execute(sql, {'limit': limit})
        results = cursor.fetchall()
        
        commands = []
        import json
        for row in results:
            commands.append({
                'command_id': row[0],
                'command_type': row[1],
                'payload': json.loads(row[2]) if row[2] else {},
                'priority': row[3],
                'requested_by': row[4],
                'created_at': row[5],
                'retry_count': row[6]
            })
        
        if commands:
            logger.info(f"✅ DB: 대기 중인 Agent 명령 {len(commands)}개 조회")
        return commands
        
    except Exception as e:
        logger.error(f"❌ DB: get_pending_agent_commands 실패! (에러: {e})")
        return []
    finally:
        if cursor: cursor.close()


def update_agent_command_status(connection, command_id: int, status: str, 
                                result_msg: str = None, order_no: str = None):
    """
    Agent 명령 상태 업데이트
    
    Args:
        connection: DB 연결 객체
        command_id: 명령 ID
        status: 새 상태 ('PROCESSING', 'COMPLETED', 'FAILED', 'CANCELLED')
        result_msg: 처리 결과 메시지
        order_no: KIS API 주문번호 (매매 명령인 경우)
    """
    cursor = None
    try:
        commands_table = _get_table_name("AGENT_COMMANDS")
        cursor = connection.cursor()
        
        # 상태별로 타임스탬프 필드 업데이트
        if status == 'PROCESSING':
            sql = f"""
            UPDATE {commands_table}
            SET STATUS = :status, PROCESSING_START = SYSTIMESTAMP
            WHERE COMMAND_ID = :cmd_id
            """
            params = {'status': status, 'cmd_id': command_id}
        else:
            sql = f"""
            UPDATE {commands_table}
            SET STATUS = :status, PROCESSED_AT = SYSTIMESTAMP, 
                RESULT_MSG = :result_msg, ORDER_NO = :order_no
            WHERE COMMAND_ID = :cmd_id
            """
            params = {
                'status': status,
                'result_msg': result_msg,
                'order_no': order_no,
                'cmd_id': command_id
            }
        
        cursor.execute(sql, params)
        connection.commit()
        
        logger.info(f"✅ DB: Agent 명령 상태 업데이트 (ID: {command_id}, Status: {status})")
        
    except Exception as e:
        logger.error(f"❌ DB: update_agent_command_status 실패! (에러: {e})")
        connection.rollback()
        raise
    finally:
        if cursor: cursor.close()


def get_recent_agent_commands(connection, limit: int = 10, requested_by: str = None):
    """
    최근 Agent 명령 조회 (모니터링용)
    
    Args:
        connection: DB 연결 객체
        limit: 최대 조회 개수
        requested_by: 특정 요청자 필터 (선택사항)
    
    Returns:
        list of dict: 명령 목록 (최신순)
    """
    cursor = None
    try:
        commands_table = _get_table_name("AGENT_COMMANDS")
        cursor = connection.cursor()
        
        if requested_by:
            sql = f"""
            SELECT COMMAND_ID, COMMAND_TYPE, PAYLOAD, STATUS, REQUESTED_BY, 
                   CREATED_AT, PROCESSING_START, PROCESSED_AT, RESULT_MSG, ORDER_NO
            FROM {commands_table}
            WHERE REQUESTED_BY = :requested_by
            ORDER BY CREATED_AT DESC
            FETCH FIRST :limit ROWS ONLY
            """
            params = {'requested_by': requested_by, 'limit': limit}
        else:
            sql = f"""
            SELECT COMMAND_ID, COMMAND_TYPE, PAYLOAD, STATUS, REQUESTED_BY, 
                   CREATED_AT, PROCESSING_START, PROCESSED_AT, RESULT_MSG, ORDER_NO
            FROM {commands_table}
            ORDER BY CREATED_AT DESC
            FETCH FIRST :limit ROWS ONLY
            """
            params = {'limit': limit}
        
        cursor.execute(sql, params)
        results = cursor.fetchall()
        
        commands = []
        import json
        for row in results:
            payload_dict = json.loads(row[2]) if row[2] else {}
            commands.append({
                'command_id': row[0],
                'command_type': row[1],
                'stock_code': payload_dict.get('stock_code', 'N/A'),
                'stock_name': payload_dict.get('stock_name', 'N/A'),
                'quantity': payload_dict.get('quantity', 0),
                'status': row[3],
                'requested_by': row[4],
                'created_at': row[5],
                'processing_start': row[6],
                'processed_at': row[7],
                'result_msg': row[8],
                'order_no': row[9]
            })
        
        return commands
        
    except Exception as e:
        logger.error(f"❌ DB: get_recent_agent_commands 실패! (에러: {e})")
        return []
    finally:
        if cursor: cursor.close()





# ============================================================================
# 자동 파라미터 최적화 이력 관리
# ============================================================================

def save_optimization_history(connection, current_params: dict, new_params: dict,
                              current_performance: dict, new_performance: dict,
                              ai_decision: str, ai_reasoning: str, ai_confidence: float,
                              market_summary: str = "", backtest_period: int = 90) -> int:
    """
    자동 파라미터 최적화 이력 저장
    
    Args:
        connection: DB 연결 객체
        current_params: 현재 파라미터 (전체, dict)
        new_params: 변경 파라미터 (변경분만, dict)
        current_performance: 현재 성과 {'mdd': float, 'return': float}
        new_performance: 새 성과 {'mdd': float, 'return': float}
        ai_decision: AI 검증 결과 ('APPROVED', 'REJECTED')
        ai_reasoning: AI 검증 사유
        ai_confidence: AI 신뢰도 (0.0~1.0)
        market_summary: 시장 요약 (선택)
        backtest_period: 백테스트 기간 (일)
    
    Returns:
        optimization_id: 생성된 최적화 이력 ID
    """
    cursor = None
    try:
        cursor = connection.cursor()
        
        sql = """
        INSERT INTO OPTIMIZATION_HISTORY (
            CURRENT_MDD, CURRENT_RETURN,
            NEW_MDD, NEW_RETURN,
            CURRENT_PARAMS, NEW_PARAMS,
            AI_DECISION, AI_REASONING, AI_CONFIDENCE,
            MARKET_SUMMARY, BACKTEST_PERIOD,
            IS_APPLIED
        ) VALUES (
            :current_mdd, :current_return,
            :new_mdd, :new_return,
            :current_params, :new_params,
            :ai_decision, :ai_reasoning, :ai_confidence,
            :market_summary, :backtest_period,
            'N'
        ) RETURNING OPTIMIZATION_ID INTO :opt_id
        """
        
        # RETURNING 절을 위한 변수
        opt_id_var = cursor.var(int)
        
        cursor.execute(sql, {
            'current_mdd': current_performance.get('mdd', 0.0),
            'current_return': current_performance.get('return', 0.0),
            'new_mdd': new_performance.get('mdd', 0.0),
            'new_return': new_performance.get('return', 0.0),
            'current_params': json.dumps(current_params, ensure_ascii=False),
            'new_params': json.dumps(new_params, ensure_ascii=False),
            'ai_decision': ai_decision,
            'ai_reasoning': ai_reasoning,
            'ai_confidence': ai_confidence,
            'market_summary': market_summary,
            'backtest_period': backtest_period,
            'opt_id': opt_id_var
        })
        
        connection.commit()
        
        optimization_id = opt_id_var.getvalue()[0]
        logger.info(f"✅ DB: 최적화 이력 저장 완료 (ID: {optimization_id}, 결정: {ai_decision})")
        
        return optimization_id
        
    except Exception as e:
        logger.error(f"❌ DB: save_optimization_history 실패! (에러: {e})", exc_info=True)
        connection.rollback()
        return None
    finally:
        if cursor: cursor.close()


def mark_optimization_applied(connection, optimization_id: int):
    """
    최적화 이력을 '적용됨'으로 표시
    
    Args:
        connection: DB 연결 객체
        optimization_id: 최적화 이력 ID
    """
    cursor = None
    try:
        cursor = connection.cursor()
        
        sql = """
        UPDATE OPTIMIZATION_HISTORY
        SET IS_APPLIED = 'Y', APPLIED_AT = SYSTIMESTAMP
        WHERE OPTIMIZATION_ID = :opt_id
        """
        
        cursor.execute(sql, {'opt_id': optimization_id})
        connection.commit()
        
        logger.info(f"✅ DB: 최적화 이력 적용 표시 완료 (ID: {optimization_id})")
        
    except Exception as e:
        logger.error(f"❌ DB: mark_optimization_applied 실패! (에러: {e})")
        connection.rollback()
    finally:
        if cursor: cursor.close()


def get_recent_optimization_history(connection, limit: int = 10) -> list:
    """
    최근 최적화 이력 조회
    
    Args:
        connection: DB 연결 객체
        limit: 조회할 개수
    
    Returns:
        최적화 이력 리스트
    """
    cursor = None
    try:
        cursor = connection.cursor()
        
        sql = f"""
        SELECT 
            OPTIMIZATION_ID, EXECUTED_AT,
            CURRENT_MDD, CURRENT_RETURN,
            NEW_MDD, NEW_RETURN,
            AI_DECISION, AI_CONFIDENCE,
            IS_APPLIED, APPLIED_AT
        FROM OPTIMIZATION_HISTORY
        ORDER BY EXECUTED_AT DESC
        FETCH FIRST {limit} ROWS ONLY
        """
        
        cursor.execute(sql)
        rows = cursor.fetchall()
        
        history = []
        for row in rows:
            history.append({
                'optimization_id': row[0],
                'executed_at': row[1],
                'current_mdd': row[2],
                'current_return': row[3],
                'new_mdd': row[4],
                'new_return': row[5],
                'ai_decision': row[6],
                'ai_confidence': row[7],
                'is_applied': row[8],
                'applied_at': row[9]
            })
        
        return history
        
    except Exception as e:
        logger.error(f"❌ DB: get_recent_optimization_history 실패! (에러: {e})")
        return []
    finally:
        if cursor: cursor.close()

def remove_from_portfolio(connection, stock_code, quantity):
    """
    포트폴리오에서 종목을 매도 처리합니다.
    - 전량 매도 시: STATUS='SOLD', SELL_STATE='SOLD'로 업데이트 (실제 삭제 X)
    - 부분 매도 시: QUANTITY, TOTAL_BUY_AMOUNT 차감

    Args:
        connection: DB 연결 객체
        stock_code: 종목 코드
        quantity: 매도 수량

    Returns:
        bool: 성공 여부
    """
    cursor = None
    try:
        cursor = connection.cursor()
        portfolio_table = _get_table_name("Portfolio")
        
        # 1. 현재 보유량 조회 (LOCK)
        sql_select = f"""
        SELECT ID, QUANTITY, AVERAGE_BUY_PRICE 
        FROM {portfolio_table} 
        WHERE STOCK_CODE = :1 AND STATUS = 'HOLDING'
        FOR UPDATE
        """
        cursor.execute(sql_select, [stock_code])
        row = cursor.fetchone()
        
        if not row:
            logger.warning(f"⚠️ DB: 매도 처리 실패 - 보유 중인 종목이 아님 ({stock_code})")
            return False
            
        portfolio_id, current_qty, avg_price = row
        
        if current_qty <= quantity:
            # 전량 매도 (또는 초과 매도 시 전량 매도로 처리)
            sql_update = f"""
            UPDATE {portfolio_table}
            SET STATUS = 'SOLD', SELL_STATE = 'SOLD', QUANTITY = 0, UPDATED_AT = SYSTIMESTAMP
            WHERE ID = :1
            """
            cursor.execute(sql_update, [portfolio_id])
            logger.info(f"✅ DB: 전량 매도 처리 완료 ({stock_code}, {current_qty}주)")
        else:
            # 부분 매도
            new_qty = current_qty - quantity
            new_total_amount = new_qty * avg_price
            sql_update = f"""
            UPDATE {portfolio_table}
            SET QUANTITY = :1, TOTAL_BUY_AMOUNT = :2, UPDATED_AT = SYSTIMESTAMP
            WHERE ID = :3
            """
            cursor.execute(sql_update, [new_qty, new_total_amount, portfolio_id])
            logger.info(f"✅ DB: 부분 매도 처리 완료 ({stock_code}, {quantity}주 매도, 잔여 {new_qty}주)")
            
        connection.commit()
        return True
        
    except Exception as e:
        logger.error(f"❌ DB: remove_from_portfolio 실패! (에러: {e})")
        if connection: connection.rollback()
        return False
    finally:
        if cursor: cursor.close()

def check_duplicate_order(connection, stock_code, trade_type, time_window_minutes=5):
    """
    최근 N분 이내에 동일한 종목에 대한 동일한 유형의 주문이 있었는지 확인 (중복 주문 방지)
    
    Args:
        connection: DB 연결 객체
        stock_code: 종목 코드
        trade_type: 주문 유형 ('BUY', 'SELL')
        time_window_minutes: 확인 시간 범위 (분)
        
    Returns:
        bool: 중복 주문이 있으면 True, 없으면 False
    """
    cursor = None
    try:
        cursor = connection.cursor()
        tradelog_table = _get_table_name("TradeLog")
        
        sql = f"""
        SELECT 1 FROM {tradelog_table}
        WHERE STOCK_CODE = :1 
          AND TRADE_TYPE = :2
          AND TRADE_TIMESTAMP >= SYSTIMESTAMP - INTERVAL '1' MINUTE * :3
          AND ROWNUM = 1
        """
        cursor.execute(sql, [stock_code, trade_type, time_window_minutes])
        result = cursor.fetchone()
        
        if result:
            logger.warning(f"⚠️ DB: 중복 주문 감지! ({stock_code}, {trade_type}, 최근 {time_window_minutes}분 내)")
            return True
        return False
        
    except Exception as e:
        logger.error(f"❌ DB: check_duplicate_order 실패! (에러: {e})")
        # 에러 발생 시 안전을 위해 중복으로 간주할지 여부는 정책에 따라 결정
        # 여기서는 False 반환하여 진행하도록 함 (로그 확인 필요)
        return False
    finally:
        if cursor: cursor.close()
