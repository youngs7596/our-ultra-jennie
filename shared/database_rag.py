"""
shared/database_rag.py - RAG 캐시 관련 함수

이 모듈은 RAG_CACHE 테이블에서 RAG 컨텍스트를 조회/저장하는 함수들을 제공합니다.
"""

import logging
from datetime import datetime, timezone, timedelta
from shared.database_base import _is_mariadb

logger = logging.getLogger(__name__)


def upsert_rag_cache(connection, stock_code, rag_context):
    """
    RAG 컨텍스트를 RAG_CACHE 테이블에 저장하거나 업데이트합니다. (UPSERT)
    """
    cursor = None
    try:
        cursor = connection.cursor()
        
        if _is_mariadb():
            sql_upsert = """
            INSERT INTO RAG_CACHE (STOCK_CODE, RAG_CONTEXT, LAST_UPDATED)
            VALUES (%s, %s, NOW())
            ON DUPLICATE KEY UPDATE
                RAG_CONTEXT = VALUES(RAG_CONTEXT),
                LAST_UPDATED = NOW()
            """
            cursor.execute(sql_upsert, (stock_code, rag_context))
        else:
            # Oracle: MERGE INTO 사용
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
        
        if _is_mariadb():
            sql = "SELECT RAG_CONTEXT, LAST_UPDATED FROM RAG_CACHE WHERE STOCK_CODE = %s"
            cursor.execute(sql, (stock_code,))
        else:
            sql = "SELECT RAG_CONTEXT, LAST_UPDATED FROM RAG_CACHE WHERE STOCK_CODE = :1"
            cursor.execute(sql, [stock_code])
        
        row = cursor.fetchone()
        if row:
            return row[0], row[1]  # (rag_context, last_updated)
        return None, None
    except Exception as e:
        logger.error(f"❌ DB: get_rag_context_from_cache ('{stock_code}') 실패! (에러: {e})")
        return None, None
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
    rag_context, last_updated = get_rag_context_from_cache(connection, stock_code)
    
    if not rag_context:
        return None, False, None
    
    # 신선도 검증
    is_fresh = False
    if last_updated:
        now = datetime.now(timezone.utc)
        # last_updated가 timezone-naive인 경우 UTC로 가정
        if last_updated.tzinfo is None:
            age = now.replace(tzinfo=None) - last_updated
        else:
            age = now - last_updated
        
        is_fresh = age < timedelta(hours=max_age_hours)
    
    return rag_context, is_fresh, last_updated
