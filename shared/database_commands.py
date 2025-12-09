"""
shared/database_commands.py - Agent 명령 관련 함수

이 모듈은 AGENT_COMMANDS 테이블에서 App과 Agent 간 비동기 명령을 
관리하는 함수들을 제공합니다.
"""

import json
import logging
from shared.database_base import _get_table_name, _is_mariadb

logger = logging.getLogger(__name__)


def create_agent_command(connection, command_type: str, payload: dict, 
                         requested_by: str = None, priority: int = 5):
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
        
        payload_json = json.dumps(payload, ensure_ascii=False)
        
        if _is_mariadb():
            sql = f"""
            INSERT INTO {commands_table} (COMMAND_TYPE, PAYLOAD, REQUESTED_BY, PRIORITY)
            VALUES (%s, %s, %s, %s)
            """
            cursor.execute(sql, (command_type, payload_json, requested_by, priority))
            command_id = cursor.lastrowid
        else:
            # Oracle: RETURNING 절 사용
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
            command_id = cmd_id_var.getvalue()[0]
        
        connection.commit()
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
        
        if _is_mariadb():
            sql = f"""
            SELECT COMMAND_ID, COMMAND_TYPE, PAYLOAD, PRIORITY, REQUESTED_BY, CREATED_AT, RETRY_COUNT
            FROM {commands_table}
            WHERE STATUS = 'PENDING'
            ORDER BY PRIORITY ASC, CREATED_AT ASC
            LIMIT %s
            """
            cursor.execute(sql, (limit,))
        else:
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
        
        if _is_mariadb():
            if status == 'PROCESSING':
                sql = f"""
                UPDATE {commands_table}
                SET STATUS = %s, PROCESSING_START = NOW()
                WHERE COMMAND_ID = %s
                """
                cursor.execute(sql, (status, command_id))
            else:
                sql = f"""
                UPDATE {commands_table}
                SET STATUS = %s, PROCESSED_AT = NOW(), 
                    RESULT_MSG = %s, ORDER_NO = %s
                WHERE COMMAND_ID = %s
                """
                cursor.execute(sql, (status, result_msg, order_no, command_id))
        else:
            if status == 'PROCESSING':
                sql = f"""
                UPDATE {commands_table}
                SET STATUS = :status, PROCESSING_START = SYSTIMESTAMP
                WHERE COMMAND_ID = :cmd_id
                """
                cursor.execute(sql, {'status': status, 'cmd_id': command_id})
            else:
                sql = f"""
                UPDATE {commands_table}
                SET STATUS = :status, PROCESSED_AT = SYSTIMESTAMP, 
                    RESULT_MSG = :result_msg, ORDER_NO = :order_no
                WHERE COMMAND_ID = :cmd_id
                """
                cursor.execute(sql, {
                    'status': status,
                    'result_msg': result_msg,
                    'order_no': order_no,
                    'cmd_id': command_id
                })
        
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
        
        if _is_mariadb():
            if requested_by:
                sql = f"""
                SELECT COMMAND_ID, COMMAND_TYPE, PAYLOAD, STATUS, PRIORITY, 
                       REQUESTED_BY, CREATED_AT, PROCESSED_AT, RESULT_MSG
                FROM {commands_table}
                WHERE REQUESTED_BY = %s
                ORDER BY CREATED_AT DESC
                LIMIT %s
                """
                cursor.execute(sql, (requested_by, limit))
            else:
                sql = f"""
                SELECT COMMAND_ID, COMMAND_TYPE, PAYLOAD, STATUS, PRIORITY, 
                       REQUESTED_BY, CREATED_AT, PROCESSED_AT, RESULT_MSG
                FROM {commands_table}
                ORDER BY CREATED_AT DESC
                LIMIT %s
                """
                cursor.execute(sql, (limit,))
        else:
            if requested_by:
                sql = f"""
                SELECT COMMAND_ID, COMMAND_TYPE, PAYLOAD, STATUS, PRIORITY, 
                       REQUESTED_BY, CREATED_AT, PROCESSED_AT, RESULT_MSG
                FROM {commands_table}
                WHERE REQUESTED_BY = :requested_by
                ORDER BY CREATED_AT DESC
                FETCH FIRST :limit ROWS ONLY
                """
                cursor.execute(sql, {'requested_by': requested_by, 'limit': limit})
            else:
                sql = f"""
                SELECT COMMAND_ID, COMMAND_TYPE, PAYLOAD, STATUS, PRIORITY, 
                       REQUESTED_BY, CREATED_AT, PROCESSED_AT, RESULT_MSG
                FROM {commands_table}
                ORDER BY CREATED_AT DESC
                FETCH FIRST :limit ROWS ONLY
                """
                cursor.execute(sql, {'limit': limit})
        
        results = cursor.fetchall()
        
        commands = []
        for row in results:
            commands.append({
                'command_id': row[0],
                'command_type': row[1],
                'payload': json.loads(row[2]) if row[2] else {},
                'status': row[3],
                'priority': row[4],
                'requested_by': row[5],
                'created_at': row[6],
                'processed_at': row[7],
                'result_msg': row[8]
            })
        
        return commands
        
    except Exception as e:
        logger.error(f"❌ DB: get_recent_agent_commands 실패! (에러: {e})")
        return []
    finally:
        if cursor: cursor.close()
