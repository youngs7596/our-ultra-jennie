"""
shared/database/rag.py - RAG 캐시 관련 함수

이 모듈은 RAG_CACHE 테이블에서 RAG 컨텍스트를 조회/저장하는 함수들을 제공합니다.
[v5.0] SQLAlchemy 마이그레이션 완료
"""

import logging
from datetime import datetime, timezone, timedelta
from sqlalchemy import text

logger = logging.getLogger(__name__)


def upsert_rag_cache(session, stock_code, rag_context):
    """
    [v5.0] RAG 컨텍스트를 RAG_CACHE 테이블에 저장하거나 업데이트합니다. (SQLAlchemy)
    """
    try:
        session.execute(text("""
            INSERT INTO RAG_CACHE (STOCK_CODE, RAG_CONTEXT, LAST_UPDATED)
            VALUES (:code, :context, NOW())
            ON DUPLICATE KEY UPDATE
                RAG_CONTEXT = VALUES(RAG_CONTEXT),
                LAST_UPDATED = NOW()
        """), {"code": stock_code, "context": rag_context})
        
        session.commit()
        return True
    except Exception as e:
        logger.error(f"❌ DB: upsert_rag_cache ('{stock_code}') 실패! (에러: {e})")
        session.rollback()
        return False


def get_rag_context_from_cache(session, stock_code):
    """
    [v5.0] RAG_CACHE 테이블에서 특정 종목의 컨텍스트를 조회합니다. (SQLAlchemy)
    """
    try:
        result = session.execute(text("""
            SELECT RAG_CONTEXT, LAST_UPDATED FROM RAG_CACHE WHERE STOCK_CODE = :code
        """), {"code": stock_code})
        
        row = result.fetchone()
        if row:
            return row[0], row[1]  # (rag_context, last_updated)
        return None, None
    except Exception as e:
        logger.error(f"❌ DB: get_rag_context_from_cache ('{stock_code}') 실패! (에러: {e})")
        return None, None


def get_rag_context_with_validation(session, stock_code, max_age_hours=24):
    """
    [v5.0] RAG 컨텍스트를 조회하고 신선도 검증 (SQLAlchemy)
    """
    rag_context, last_updated = get_rag_context_from_cache(session, stock_code)
    
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
