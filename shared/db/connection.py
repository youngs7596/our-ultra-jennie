import logging
import os
from contextlib import contextmanager
from typing import Optional
from urllib.parse import quote_plus

from sqlalchemy import create_engine

from shared.auth import get_secret
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import scoped_session, sessionmaker, Session

logger = logging.getLogger(__name__)

_engine: Optional[Engine] = None
_session_factory: Optional[scoped_session] = None
_engine_config: dict = {}


def _build_connection_url() -> str:
    """
    MariaDB 연결 URL을 생성합니다.
    환경변수 → secrets.json 순서로 읽습니다.
    """
    logger.info("DB 연결 타입: MARIADB")

    # MariaDB (MySQL 호환) 연결 설정 - 환경변수 우선, 없으면 secrets.json
    user = os.getenv("MARIADB_USER") or get_secret("mariadb-user") or "root"
    password = os.getenv("MARIADB_PASSWORD") or get_secret("mariadb-password") or ""
    host = os.getenv("MARIADB_HOST") or get_secret("mariadb-host") or "localhost"
    port = os.getenv("MARIADB_PORT", "3306")
    dbname = os.getenv("MARIADB_DBNAME") or get_secret("mariadb-database") or "jennie_db"

    if not all([user, password, host, port, dbname]):
        raise ValueError("MariaDB 접속 정보를 확인할 수 없습니다. (MARIADB_* 환경변수 또는 secrets.json 필요)")

    user_enc = quote_plus(user)
    password_enc = quote_plus(password)
    
    # PyMySQL 드라이버 사용
    return f"mysql+pymysql://{user_enc}:{password_enc}@{host}:{port}/{dbname}?charset=utf8mb4"


def _get_db_type():
    return "MARIADB"


def init_engine(
    db_user: Optional[str],
    db_password: Optional[str],
    db_service_name: Optional[str],
    wallet_path: Optional[str],
    *,
    min_sessions: int = None,
    max_sessions: int = None,
    echo: bool = False,
) -> Engine:
    # 환경변수에서 연결 풀 크기 설정 (기본: min=2, max=10)
    if min_sessions is None:
        min_sessions = int(os.getenv("SQLALCHEMY_POOL_SIZE", "2"))
    if max_sessions is None:
        max_sessions = int(os.getenv("SQLALCHEMY_MAX_OVERFLOW", "10")) + min_sessions
    """
    SQLAlchemy Engine + Session Factory를 초기화합니다.
    기존 oracledb Connection Pool과 병행 운영되며,
    추후에는 이 Engine을 기본 진입점으로 사용할 예정입니다.
    """
    global _engine, _session_factory, _engine_config

    if _engine:
        return _engine

    connection_url = _build_connection_url()
    pool_size = max(1, min_sessions)
    max_overflow = max(0, max_sessions - pool_size)
    pool_recycle = int(os.getenv("SQLALCHEMY_POOL_RECYCLE", "900"))

    logger.info(
        "🔌 [SQLAlchemy] 엔진 초기화 시작 (pool_size=%s, max_overflow=%s, recycle=%ss)",
        pool_size,
        max_overflow,
        pool_recycle,
    )
    try:
        _engine = create_engine(
            connection_url,
            echo=echo,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_pre_ping=True,
            pool_recycle=pool_recycle,
            future=True,
            connect_args={
                # GSSAPI 인증 플러그인 우회 (mysql_native_password 사용)
                "auth_plugin_map": {"auth_gssapi_client": None}
            },
        )
        _session_factory = scoped_session(
            sessionmaker(bind=_engine, autoflush=False, autocommit=False, expire_on_commit=False)
        )
        _engine_config = {
            "db_type": _get_db_type(),
            "url": connection_url.split('@')[-1], # 민감 정보 제외
            "pool_size": pool_size,
            "max_overflow": max_overflow,
        }
        logger.info("✅ [SQLAlchemy] 엔진 초기화 완료 (DB Type: %s)", _get_db_type())
        return _engine
    except SQLAlchemyError as exc:
        logger.exception("❌ [SQLAlchemy] 엔진 초기화 실패: %s", exc)
        _engine = None
        _session_factory = None
        raise


def ensure_engine_initialized(
    db_user: Optional[str] = None,
    db_password: Optional[str] = None,
    db_service_name: Optional[str] = None,
    wallet_path: Optional[str] = None,
    *,
    min_sessions: int = None,
    max_sessions: int = None,
) -> Optional[Engine]:
    """
    외부에서 보조적으로 엔진 초기화를 트리거할 때 사용합니다.
    이미 초기화된 경우 기존 엔진을 반환합니다.
    """
    if _engine:
        return _engine
    
    # 환경변수에서 연결 풀 크기 설정
    if min_sessions is None:
        min_sessions = int(os.getenv("SQLALCHEMY_POOL_SIZE", "5"))
    if max_sessions is None:
        max_sessions = min_sessions + int(os.getenv("SQLALCHEMY_MAX_OVERFLOW", "20"))

    try:
        return init_engine(
            db_user=db_user,
            db_password=db_password,
            db_service_name=db_service_name,
            wallet_path=wallet_path,
            min_sessions=min_sessions,
            max_sessions=max_sessions,
        )
    except Exception as exc:
        logger.error("❌ [SQLAlchemy] ensure_engine_initialized 실패: %s", exc)
        return None


def is_engine_initialized() -> bool:
    return _engine is not None


def get_engine() -> Engine:
    if not _engine:
        raise RuntimeError("SQLAlchemy 엔진이 초기화되지 않았습니다. init_engine()을 먼저 호출하세요.")
    return _engine


def get_session() -> Session:
    if not _session_factory:
        raise RuntimeError("SQLAlchemy 세션 팩토리가 초기화되지 않았습니다.")
    return _session_factory()


@contextmanager
def session_scope(readonly: bool = False):
    """
    SQLAlchemy Session용 컨텍스트 매니저.
    readonly=True일 때는 commit 대신 rollback만 수행하여 커넥션을 빠르게 반환합니다.
    """
    session = get_session()
    try:
        yield session
        if readonly:
            session.rollback()
        else:
            session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def dispose_engine():
    """테스트용: 엔진/세션을 완전히 정리"""
    global _engine, _session_factory, _engine_config
    if _session_factory:
        _session_factory.remove()
    if _engine:
        _engine.dispose()
    _engine = None
    _session_factory = None
    _engine_config = {}
