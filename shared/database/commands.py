"""
shared/database/commands.py - Agent 명령 관련 함수

이 모듈은 AGENT_COMMANDS 테이블에서 App과 Agent 간 비동기 명령을 
관리하는 함수들을 제공합니다.
[v5.0] SQLAlchemy 마이그레이션 완료
"""

import json
import logging
from sqlalchemy import text
from .core import _get_table_name

logger = logging.getLogger(__name__)


def create_agent_command(session, command_type: str, payload: dict, 
                         requested_by: str = None, priority: int = 5):
    """
    [v5.0] Agent 명령 생성 (SQLAlchemy)
    """
    try:
        commands_table = _get_table_name("AGENT_COMMANDS")
        payload_json = json.dumps(payload, ensure_ascii=False)
        
        result = session.execute(text(f"""
            INSERT INTO {commands_table} (COMMAND_TYPE, PAYLOAD, REQUESTED_BY, PRIORITY)
            VALUES (:cmd_type, :payload, :requested_by, :priority)
        """), {
            'cmd_type': command_type,
            'payload': payload_json,
            'requested_by': requested_by,
            'priority': priority
        })
        
        # MariaDB에서 lastrowid 얻기
        command_id = result.lastrowid
        session.commit()
        logger.info(f"✅ DB: Agent 명령 생성 완료 (ID: {command_id}, Type: {command_type})")
        return command_id
        
    except Exception as e:
        logger.error(f"❌ DB: create_agent_command 실패! (에러: {e})")
        session.rollback()
        raise


def get_pending_agent_commands(session, limit: int = 100):
    """
    [v5.0] 대기 중인 Agent 명령 조회 (SQLAlchemy)
    """
    try:
        commands_table = _get_table_name("AGENT_COMMANDS")
        
        result = session.execute(text(f"""
            SELECT COMMAND_ID, COMMAND_TYPE, PAYLOAD, PRIORITY, REQUESTED_BY, CREATED_AT, RETRY_COUNT
            FROM {commands_table}
            WHERE STATUS = 'PENDING'
            ORDER BY PRIORITY ASC, CREATED_AT ASC
            LIMIT :limit
        """), {"limit": limit})
        
        rows = result.fetchall()
        
        commands = []
        for row in rows:
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


def update_agent_command_status(session, command_id: int, status: str, 
                                result_msg: str = None, order_no: str = None):
    """
    [v5.0] Agent 명령 상태 업데이트 (SQLAlchemy)
    """
    try:
        commands_table = _get_table_name("AGENT_COMMANDS")
        
        if status == 'PROCESSING':
            session.execute(text(f"""
                UPDATE {commands_table}
                SET STATUS = :status, PROCESSING_START = NOW()
                WHERE COMMAND_ID = :cmd_id
            """), {'status': status, 'cmd_id': command_id})
        else:
            session.execute(text(f"""
                UPDATE {commands_table}
                SET STATUS = :status, PROCESSED_AT = NOW(), 
                    RESULT_MSG = :result_msg, ORDER_NO = :order_no
                WHERE COMMAND_ID = :cmd_id
            """), {
                'status': status,
                'result_msg': result_msg,
                'order_no': order_no,
                'cmd_id': command_id
            })
        
        session.commit()
        logger.info(f"✅ DB: Agent 명령 상태 업데이트 (ID: {command_id}, Status: {status})")
        
    except Exception as e:
        logger.error(f"❌ DB: update_agent_command_status 실패! (에러: {e})")
        session.rollback()
        raise


def get_recent_agent_commands(session, limit: int = 10, requested_by: str = None):
    """
    [v5.0] 최근 Agent 명령 조회 (SQLAlchemy)
    """
    try:
        commands_table = _get_table_name("AGENT_COMMANDS")
        
        if requested_by:
            result = session.execute(text(f"""
                SELECT COMMAND_ID, COMMAND_TYPE, PAYLOAD, STATUS, PRIORITY, 
                       REQUESTED_BY, CREATED_AT, PROCESSED_AT, RESULT_MSG
                FROM {commands_table}
                WHERE REQUESTED_BY = :requested_by
                ORDER BY CREATED_AT DESC
                LIMIT :limit
            """), {'requested_by': requested_by, 'limit': limit})
        else:
            result = session.execute(text(f"""
                SELECT COMMAND_ID, COMMAND_TYPE, PAYLOAD, STATUS, PRIORITY, 
                       REQUESTED_BY, CREATED_AT, PROCESSED_AT, RESULT_MSG
                FROM {commands_table}
                ORDER BY CREATED_AT DESC
                LIMIT :limit
            """), {'limit': limit})
        
        rows = result.fetchall()
        
        commands = []
        for row in rows:
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
