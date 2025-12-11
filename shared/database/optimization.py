"""
shared/database/optimization.py - 자동 파라미터 최적화 이력 관리 함수

이 모듈은 OPTIMIZATION_HISTORY 테이블에서 파라미터 최적화 이력을 
관리하는 함수들을 제공합니다.
[v5.0] SQLAlchemy 마이그레이션 완료
"""

import json
import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)


def save_optimization_history(session, current_params: dict, new_params: dict,
                              current_performance: dict, new_performance: dict,
                              ai_decision: str, ai_reasoning: str, ai_confidence: float,
                              market_summary: str = "", backtest_period: int = 90) -> int:
    """
    [v5.0] 자동 파라미터 최적화 이력 저장 (SQLAlchemy)
    """
    try:
        result = session.execute(text("""
            INSERT INTO OPTIMIZATION_HISTORY (
                CURRENT_MDD, CURRENT_RETURN,
                NEW_MDD, NEW_RETURN,
                CURRENT_PARAMS, NEW_PARAMS,
                AI_DECISION, AI_REASONING, AI_CONFIDENCE,
                MARKET_SUMMARY, BACKTEST_PERIOD,
                IS_APPLIED
            ) VALUES (:current_mdd, :current_return, :new_mdd, :new_return, 
                      :current_params, :new_params, :ai_decision, :ai_reasoning, 
                      :ai_confidence, :market_summary, :backtest_period, 'N')
        """), {
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
            'backtest_period': backtest_period
        })
        
        optimization_id = result.lastrowid
        session.commit()
        logger.info(f"✅ DB: 최적화 이력 저장 완료 (ID: {optimization_id}, 결정: {ai_decision})")
        return optimization_id
        
    except Exception as e:
        logger.error(f"❌ DB: save_optimization_history 실패! (에러: {e})", exc_info=True)
        session.rollback()
        return None


def mark_optimization_applied(session, optimization_id: int):
    """
    [v5.0] 최적화 이력을 '적용됨'으로 표시 (SQLAlchemy)
    """
    try:
        session.execute(text("""
            UPDATE OPTIMIZATION_HISTORY
            SET IS_APPLIED = 'Y', APPLIED_AT = NOW()
            WHERE OPTIMIZATION_ID = :opt_id
        """), {'opt_id': optimization_id})
        
        session.commit()
        logger.info(f"✅ DB: 최적화 이력 적용 표시 완료 (ID: {optimization_id})")
        
    except Exception as e:
        logger.error(f"❌ DB: mark_optimization_applied 실패! (에러: {e})")
        session.rollback()


def get_recent_optimization_history(session, limit: int = 10) -> list:
    """
    [v5.0] 최근 최적화 이력 조회 (SQLAlchemy)
    """
    try:
        result = session.execute(text("""
            SELECT 
                OPTIMIZATION_ID, EXECUTED_AT,
                CURRENT_MDD, CURRENT_RETURN,
                NEW_MDD, NEW_RETURN,
                AI_DECISION, AI_CONFIDENCE,
                IS_APPLIED, APPLIED_AT
            FROM OPTIMIZATION_HISTORY
            ORDER BY EXECUTED_AT DESC
            LIMIT :limit
        """), {"limit": limit})
        
        rows = result.fetchall()
        
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
