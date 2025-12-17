# youngs75_jennie/utils.py
# Version: v3.5
# [모듈] 공통 유틸리티 함수 (재시도, 에러 처리 등)

import logging
import time
from functools import wraps
from typing import Callable, Type, Tuple, Optional, Any
from enum import Enum

logger = logging.getLogger(__name__)


class RetryStrategy(Enum):
    """재시도 전략"""
    EXPONENTIAL_BACKOFF = "exponential_backoff"  # 지수 백오프
    FIXED_INTERVAL = "fixed_interval"  # 고정 간격
    IMMEDIATE = "immediate"  # 즉시 재시도

# [NEW] FailureReporter 지연 임포트 (Circular Dependency 방지)
def _get_reporter():
    try:
        from shared.failure_reporter import FailureReporter
        return FailureReporter()
    except ImportError:
        return None


class RetryableError(Exception):
    """재시도 가능한 에러 (일시적 오류)"""
    pass


class NonRetryableError(Exception):
    """재시도 불가능한 에러 (영구적 오류)"""
    pass


def retry_with_backoff(
    max_attempts: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 60.0,
    backoff_multiplier: float = 2.0,
    retryable_exceptions: Tuple[Type[Exception], ...] = (Exception,),
    strategy: RetryStrategy = RetryStrategy.EXPONENTIAL_BACKOFF,
    on_retry: Optional[Callable[[int, Exception], None]] = None
):
    """
    재시도 로직이 포함된 데코레이터
    
    Args:
        max_attempts: 최대 시도 횟수
        initial_delay: 초기 지연 시간 (초)
        max_delay: 최대 지연 시간 (초)
        backoff_multiplier: 백오프 배수
        retryable_exceptions: 재시도할 예외 타입
        strategy: 재시도 전략
        on_retry: 재시도 시 호출할 콜백 함수 (attempt, exception)
    
    사용 예시:
        @retry_with_backoff(max_attempts=3, initial_delay=2.0)
        def fetch_data():
            # ... 작업 수행 ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            last_exception = None
            delay = initial_delay
            
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except retryable_exceptions as e:
                    last_exception = e
                    
                    # 재시도 불가능한 에러인지 확인
                    if isinstance(e, NonRetryableError):
                        logger.error(f"❌ [{func.__name__}] 재시도 불가능한 에러: {e}")
                        
                        # [NEW] Incident Report 생성
                        reporter = _get_reporter()
                        if reporter:
                            reporter.capture_exception(e, custom_msg=f"[{func.__name__}] Non-Retryable Error")
                            
                        raise
                    
                    # 마지막 시도인 경우 예외 발생
                    if attempt >= max_attempts:
                        logger.error(f"❌ [{func.__name__}] 최대 시도 횟수({max_attempts}) 초과. 마지막 에러: {e}")
                        
                        # [NEW] Incident Report 생성 (최종 실패)
                        reporter = _get_reporter()
                        if reporter:
                            reporter.capture_exception(e, custom_msg=f"[{func.__name__}] Max Retries ({max_attempts}) Exceeded")
                            
                        raise
                    
                    # 재시도 콜백 호출
                    if on_retry:
                        try:
                            on_retry(attempt, e)
                        except Exception as callback_error:
                            logger.warning(f"⚠️ [{func.__name__}] 재시도 콜백 실행 중 오류: {callback_error}")
                    
                    # 재시도 전략에 따른 지연 시간 계산
                    if strategy == RetryStrategy.EXPONENTIAL_BACKOFF:
                        current_delay = min(delay, max_delay)
                        delay *= backoff_multiplier
                    elif strategy == RetryStrategy.FIXED_INTERVAL:
                        current_delay = initial_delay
                    else:  # IMMEDIATE
                        current_delay = 0
                    
                    logger.warning(
                        f"⚠️ [{func.__name__}] 시도 {attempt}/{max_attempts} 실패. "
                        f"{current_delay:.1f}초 후 재시도... (에러: {type(e).__name__}: {str(e)[:100]})"
                    )
                    
                    if current_delay > 0:
                        time.sleep(current_delay)
                
                except Exception as e:
                    # 재시도 불가능한 예외는 즉시 발생
                    if not isinstance(e, retryable_exceptions):
                        logger.error(f"❌ [{func.__name__}] 예상치 못한 예외 발생: {e}")
                        raise
                    last_exception = e
            
            # 모든 시도 실패
            if last_exception:
                # [NEW] Incident Report 생성 (루프 종료 후 실패)
                reporter = _get_reporter()
                if reporter:
                    reporter.capture_exception(last_exception, custom_msg=f"[{func.__name__}] All retries failed")
                raise last_exception
            
        return wrapper
    return decorator


def safe_db_operation(
    operation_name: str = "DB 작업",
    max_retries: int = 3,
    retry_delay: float = 1.0
):
    """
    DB 작업을 안전하게 실행하는 데코레이터
    
    Args:
        operation_name: 작업 이름 (로깅용)
        max_retries: 최대 재시도 횟수
        retry_delay: 재시도 지연 시간 (초)
    
    사용 예시:
        @safe_db_operation(operation_name="포트폴리오 조회", max_retries=3)
        def get_portfolio(conn):
            # ... DB 작업 ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            import oracledb
            
            last_exception = None
            
            for attempt in range(1, max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except (oracledb.DatabaseError, oracledb.OperationalError) as e:
                    last_exception = e
                    error_code = getattr(e, 'code', None)
                    
                    # 일시적 오류인지 확인 (연결 오류, 타임아웃 등)
                    retryable_codes = [
                        1013,  # TNS: unable to connect
                        12535,  # TNS: operation timed out
                        12537,  # TNS: connection closed
                        12541,  # TNS: no listener
                        12570,  # TNS: packet reader failure
                    ]
                    
                    if error_code in retryable_codes or attempt < max_retries:
                        logger.warning(
                            f"⚠️ [{operation_name}] DB 작업 실패 (시도 {attempt}/{max_retries}). "
                            f"{retry_delay}초 후 재시도... (에러 코드: {error_code})"
                        )
                        if attempt < max_retries:
                            time.sleep(retry_delay)
                            continue
                    
                    logger.error(f"❌ [{operation_name}] DB 작업 최종 실패: {e}")
                    raise
                except Exception as e:
                    # DB 관련이 아닌 예외는 즉시 발생
                    logger.error(f"❌ [{operation_name}] 예상치 못한 예외: {e}")
                    raise
            
            if last_exception:
                raise last_exception
        
        return wrapper
    return decorator


def safe_api_call(
    api_name: str = "API 호출",
    max_retries: int = 3,
    retry_delay: float = 2.0,
    retryable_status_codes: Tuple[int, ...] = (500, 502, 503, 504, 429)
):
    """
    API 호출을 안전하게 실행하는 데코레이터
    
    Args:
        api_name: API 이름 (로깅용)
        max_retries: 최대 재시도 횟수
        retry_delay: 재시도 지연 시간 (초)
        retryable_status_codes: 재시도할 HTTP 상태 코드
    
    사용 예시:
        @safe_api_call(api_name="KIS API", max_retries=3)
        def call_kis_api():
            # ... API 호출 ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            import requests
            
            last_exception = None
            
            for attempt in range(1, max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except requests.exceptions.RequestException as e:
                    last_exception = e
                    status_code = getattr(e.response, 'status_code', None) if hasattr(e, 'response') else None
                    
                    # 재시도 가능한 상태 코드인지 확인
                    if status_code in retryable_status_codes or attempt < max_retries:
                        logger.warning(
                            f"⚠️ [{api_name}] API 호출 실패 (시도 {attempt}/{max_retries}). "
                            f"{retry_delay}초 후 재시도... (상태 코드: {status_code})"
                        )
                        if attempt < max_retries:
                            time.sleep(retry_delay)
                            continue
                    
                    logger.error(f"❌ [{api_name}] API 호출 최종 실패: {e}")
                    raise
                except Exception as e:
                    # API 관련이 아닌 예외는 즉시 발생
                    logger.error(f"❌ [{api_name}] 예상치 못한 예외: {e}")
                    raise
            
            if last_exception:
                raise last_exception
        
        return wrapper
    return decorator


def log_execution_time(operation_name: str = None):
    """
    함수 실행 시간을 로깅하는 데코레이터
    
    Args:
        operation_name: 작업 이름 (로깅용, None이면 함수명 사용)
    
    사용 예시:
        @log_execution_time(operation_name="매수 신호 스캔")
        def scan_buy_opportunities():
            # ... 작업 수행 ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            op_name = operation_name or func.__name__
            start_time = time.time()
            try:
                result = func(*args, **kwargs)
                duration = time.time() - start_time
                logger.info(f"⏱️ [{op_name}] 실행 완료 (소요 시간: {duration:.2f}초)")
                return result
            except Exception as e:
                duration = time.time() - start_time
                logger.error(f"❌ [{op_name}] 실행 실패 (소요 시간: {duration:.2f}초, 에러: {e})")
                raise
        return wrapper
    return decorator


def handle_errors(
    default_return: Any = None,
    log_error: bool = True,
    reraise: bool = False
):
    """
    에러를 처리하는 데코레이터
    
    Args:
        default_return: 에러 발생 시 반환할 기본값
        log_error: 에러를 로깅할지 여부
        reraise: 에러를 다시 발생시킬지 여부
    
    사용 예시:
        @handle_errors(default_return=[], log_error=True)
        def get_list():
            # ... 작업 수행 ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            try:
                return func(*args, **kwargs)
            except Exception as e:
                if log_error:
                    logger.error(f"❌ [{func.__name__}] 에러 발생: {e}", exc_info=True)
                    
                    # [NEW] Incident Report 생성
                    reporter = _get_reporter()
                    if reporter:
                        reporter.capture_exception(e, custom_msg=f"[{func.__name__}] Exception Caught")
                        
                if reraise:
                    raise
                return default_return
        return wrapper
    return decorator

