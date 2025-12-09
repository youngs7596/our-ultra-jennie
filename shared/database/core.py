"""
shared/database/core.py

DB 연결 초기화, 기본 유틸리티, 그리고 CONFIG 테이블 관련 기능을 담당합니다.
(기존 shared/database_base.py + shared/database_config.py 통합)
"""

import logging
import os
from datetime import datetime, timezone, timedelta

from shared.db import connection as sa_connection
from shared.db import repository as sa_repository
# get_session for config functions
from shared.db.connection import get_session

logger = logging.getLogger(__name__)

# ============================================================================
# [Base] DB 타입 및 테이블 네이밍 헬퍼
# ============================================================================

def _is_mariadb() -> bool:
    """현재 DB 타입 확인 (항상 MariaDB)"""
    return True


def _get_param_placeholder(index: int = 1) -> str:
    """DB 타입에 따른 파라미터 플레이스홀더 반환 (MariaDB: %s)"""
    return "%s"


def _get_table_name(base_name: str) -> str:
    """
    MOCK 모드일 때는 Portfolio와 TradeLog만 _mock 접미사 추가
    다른 테이블은 그대로 사용 (WatchList, STOCK_DAILY_PRICES_3Y 등)
    """
    trading_mode = os.getenv("TRADING_MODE", "REAL")
    if trading_mode == "MOCK":
        if base_name in ["Portfolio", "TradeLog", "NEWS_SENTIMENT"]:
            table_name = f"{base_name}_mock"
            logger.debug(f"   [MOCK 모드] 테이블명: {base_name} → {table_name}")
            return table_name
    return base_name


def _is_sqlalchemy_ready() -> bool:
    try:
        return sa_connection.is_engine_initialized()
    except Exception:
        return False


pool = None  # 전역 연결 풀 변수 (MariaDB에서는 사용하지 않음)


def init_connection_pool(
    db_user=None,
    db_password=None,
    db_service_name=None,
    wallet_path=None,
    min_sessions=2,
    max_sessions=5,
    increment=1,
):
    """MariaDB에서는 SQLAlchemy 엔진만 초기화합니다."""
    global pool

    min_pool_size = int(os.getenv("DB_POOL_MIN", min_sessions))
    max_pool_size = int(os.getenv("DB_POOL_MAX", max_sessions))

    logger.info(
        f"--- [DB Pool] MariaDB SQLAlchemy 엔진 초기화 (pool_size: {min_pool_size}~{max_pool_size}) ---"
    )

    sa_connection.ensure_engine_initialized(
        min_sessions=min_pool_size,
        max_sessions=max_pool_size,
    )

    pool = sa_connection.get_engine()
    return pool


def get_db_connection():
    """
    레거시 코드 호환용: DB 연결 객체를 반환합니다.
    (scout.py 등에서 사용)
    """
    global pool
    if pool is None:
        init_connection_pool()
    
    # SQLAlchemy Engine에서 Raw DBAPI Connection(PyMySQL Connection)을 가져옴
    # 이렇게 해야 .cursor() 메소드를 사용할 수 있음
    return pool.raw_connection()


def is_pool_initialized():
    """
    레거시 코드 호환용: DB 연결 풀 초기화 여부를 반환합니다.
    """
    global pool
    return pool is not None


# ============================================================================
# [Config] CONFIG 테이블 관련 함수
# ============================================================================

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
        with get_session() as session:
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
        with get_session() as session:
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
        with get_session() as session:
            return sa_repository.set_config(session, config_key, config_value)
    except Exception as e:
        logger.error(f"❌ DB: set_config ('{config_key}') 실패! (에러: {e})")
        return False
