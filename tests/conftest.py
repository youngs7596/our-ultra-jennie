"""
tests/conftest.py - pytest 공통 fixtures
=========================================

이 파일은 모든 테스트에서 사용할 수 있는 공통 fixtures를 정의합니다.

Fixtures:
---------
- fake_redis: fakeredis를 사용한 Redis mock
- in_memory_db: SQLite in-memory DB (SQLAlchemy)
- mock_db_connection: DB 연결 mock
"""

import os
import sys
import pytest

# 프로젝트 루트를 sys.path에 추가 (shared 모듈 import 가능하게)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


# ============================================================================
# Redis Fixtures
# ============================================================================

@pytest.fixture
def fake_redis():
    """
    fakeredis를 사용한 Redis mock fixture.
    
    테스트가 끝나면 모든 데이터가 자동으로 정리됩니다.
    
    사용 예시:
        def test_something(fake_redis):
            from shared.redis_cache import set_sentiment_score
            set_sentiment_score("005930", 75, "test", redis_client=fake_redis)
    """
    import fakeredis
    
    # decode_responses=True로 설정하여 실제 Redis와 동일하게 동작
    fake_server = fakeredis.FakeServer()
    fake_client = fakeredis.FakeStrictRedis(
        server=fake_server,
        decode_responses=True
    )
    
    yield fake_client
    
    # 테스트 후 정리
    fake_client.flushall()


@pytest.fixture
def fake_redis_with_data(fake_redis):
    """
    미리 데이터가 채워진 fakeredis fixture.
    
    테스트용 샘플 데이터가 미리 저장되어 있습니다.
    """
    import json
    from datetime import datetime, timezone
    
    # 샘플 감성 점수
    fake_redis.setex(
        "sentiment:005930",
        7200,
        json.dumps({
            "score": 65.5,
            "reason": "삼성전자 긍정적 뉴스",
            "updated_at": datetime.now().isoformat()
        })
    )
    
    # 샘플 시장 국면 캐시
    fake_redis.setex(
        "market_regime_cache",
        3600,
        json.dumps({
            "regime": "BULL",
            "risk_level": "LOW",
            "_cached_at": datetime.now(timezone.utc).isoformat()
        })
    )
    
    # 샘플 경쟁사 수혜 점수
    fake_redis.setex(
        "competitor_benefit:000660",
        1728000,
        json.dumps({
            "score": 10,
            "reason": "경쟁사 보안사고로 인한 수혜",
            "affected_stock": "005930",
            "event_type": "보안사고",
            "updated_at": datetime.now(timezone.utc).isoformat()
        })
    )
    
    yield fake_redis


# ============================================================================
# Database Fixtures
# ============================================================================

@pytest.fixture(scope="function")
def in_memory_db():
    """
    SQLite in-memory DB를 사용한 SQLAlchemy 엔진/세션 fixture.
    
    실제 MariaDB/Oracle 없이 DB 로직을 테스트할 수 있습니다.
    
    주의: SQLite는 일부 SQL 문법이 다를 수 있으므로,
    복잡한 쿼리 테스트에는 제한이 있을 수 있습니다.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from shared.db.models import Base
    
    # In-memory SQLite 엔진 생성
    engine = create_engine(
        "sqlite:///:memory:",
        echo=False,  # SQL 로깅 비활성화
        future=True
    )
    
    # 모든 테이블 생성
    Base.metadata.create_all(engine)
    
    # 세션 팩토리 생성
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = SessionLocal()
    
    yield {
        "engine": engine,
        "session": session,
        "SessionLocal": SessionLocal
    }
    
    # 정리
    session.close()
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def mock_db_connection(mocker):
    """
    DB 연결 mock fixture.
    
    pymysql 커넥션을 mock하여 실제 DB 없이 테스트합니다.
    cursor.execute(), fetchone(), fetchall() 등을 mock합니다.
    """
    mock_conn = mocker.MagicMock()
    mock_cursor = mocker.MagicMock()
    
    # cursor 반환 설정
    mock_conn.cursor.return_value = mock_cursor
    mock_cursor.__enter__ = mocker.MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = mocker.MagicMock(return_value=False)
    
    return {
        "connection": mock_conn,
        "cursor": mock_cursor
    }


# ============================================================================
# 환경 설정 Fixtures
# ============================================================================

@pytest.fixture(autouse=True)
def reset_redis_global_state():
    """
    각 테스트 전후로 Redis 전역 상태를 리셋합니다.
    """
    from shared import redis_cache
    
    # 테스트 전 리셋
    redis_cache.reset_redis_connection()
    
    yield
    
    # 테스트 후 리셋
    redis_cache.reset_redis_connection()


@pytest.fixture
def mock_env_vars(monkeypatch):
    """
    환경변수 mock fixture.
    
    사용 예시:
        def test_something(mock_env_vars):
            mock_env_vars({
                "REDIS_URL": "redis://test:6379",
                "TRADING_MODE": "MOCK"
            })
    """
    def _set_env_vars(env_dict):
        for key, value in env_dict.items():
            monkeypatch.setenv(key, value)
    
    return _set_env_vars

