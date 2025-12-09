"""
shared/database_base.py

DB/Redis 기본 유틸과 테이블 네이밍/연결 초기화 로직을 분리했습니다.
기존 shared/database.py는 하위 호환을 위해 이 모듈을 import하여 사용합니다.
"""

import logging
import os
from datetime import datetime, timezone, timedelta

from shared.db import connection as sa_connection
from shared.db import repository as sa_repository

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
# MOCK 모드 테이블명 헬퍼 함수
# ============================================================================
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


# ============================================================================
# SQLAlchemy 준비 상태
# ============================================================================
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
        pool_size=min_pool_size,
        max_overflow=max(0, max_pool_size - min_pool_size),
    )

    pool = sa_connection.get_engine()
    return pool
