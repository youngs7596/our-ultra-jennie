"""
shared/database_optimization.py - 자동 파라미터 최적화 이력 관리 함수

이 모듈은 OPTIMIZATION_HISTORY 테이블에서 파라미터 최적화 이력을 
관리하는 함수들을 제공합니다.
"""

import json
import logging
from shared.database_base import _is_mariadb

logger = logging.getLogger(__name__)


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
        
        if _is_mariadb():
            sql = """
            INSERT INTO OPTIMIZATION_HISTORY (
                CURRENT_MDD, CURRENT_RETURN,
                NEW_MDD, NEW_RETURN,
                CURRENT_PARAMS, NEW_PARAMS,
                AI_DECISION, AI_REASONING, AI_CONFIDENCE,
                MARKET_SUMMARY, BACKTEST_PERIOD,
                IS_APPLIED
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'N')
            """
            cursor.execute(sql, (
                current_performance.get('mdd', 0.0),
                current_performance.get('return', 0.0),
                new_performance.get('mdd', 0.0),
                new_performance.get('return', 0.0),
                json.dumps(current_params, ensure_ascii=False),
                json.dumps(new_params, ensure_ascii=False),
                ai_decision,
                ai_reasoning,
                ai_confidence,
                market_summary,
                backtest_period
            ))
            optimization_id = cursor.lastrowid
        else:
            # Oracle: RETURNING 절 사용
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
            optimization_id = opt_id_var.getvalue()[0]
        
        connection.commit()
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
        
        if _is_mariadb():
            sql = """
            UPDATE OPTIMIZATION_HISTORY
            SET IS_APPLIED = 'Y', APPLIED_AT = NOW()
            WHERE OPTIMIZATION_ID = %s
            """
            cursor.execute(sql, (optimization_id,))
        else:
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
        
        if _is_mariadb():
            sql = """
            SELECT 
                OPTIMIZATION_ID, EXECUTED_AT,
                CURRENT_MDD, CURRENT_RETURN,
                NEW_MDD, NEW_RETURN,
                AI_DECISION, AI_CONFIDENCE,
                IS_APPLIED, APPLIED_AT
            FROM OPTIMIZATION_HISTORY
            ORDER BY EXECUTED_AT DESC
            LIMIT %s
            """
            cursor.execute(sql, (limit,))
        else:
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
