"""
shared/database_config.py - CONFIG 테이블 관련 함수

이 모듈은 CONFIG 테이블에서 설정값을 조회/저장하는 함수들을 제공합니다.
"""

import logging
from shared.db import connection as sa_connection
from shared.db import repository as sa_repository

logger = logging.getLogger(__name__)


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
