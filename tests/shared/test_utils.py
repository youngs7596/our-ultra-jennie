"""
tests/shared/test_utils.py - 유틸리티 함수 테스트
=================================================

shared/utils.py의 데코레이터 및 유틸리티 함수들을 테스트합니다.
"""

import pytest
import time
from unittest.mock import MagicMock, patch
from shared.utils import (
    RetryStrategy, RetryableError, NonRetryableError,
    retry_with_backoff, log_execution_time, handle_errors
)


# ============================================================================
# Tests: retry_with_backoff 데코레이터
# ============================================================================

class TestRetryWithBackoff:
    """retry_with_backoff 데코레이터 테스트"""
    
    def test_success_first_try(self):
        """첫 시도에서 성공"""
        call_count = [0]
        
        @retry_with_backoff(max_attempts=3)
        def successful_func():
            call_count[0] += 1
            return "success"
        
        result = successful_func()
        
        assert result == "success"
        assert call_count[0] == 1
    
    def test_retry_on_exception(self):
        """예외 발생 시 재시도"""
        call_count = [0]
        
        @retry_with_backoff(max_attempts=3, initial_delay=0.01)
        def fail_then_succeed():
            call_count[0] += 1
            if call_count[0] < 3:
                raise ValueError("일시적 오류")
            return "success"
        
        result = fail_then_succeed()
        
        assert result == "success"
        assert call_count[0] == 3
    
    def test_max_attempts_exceeded(self):
        """최대 시도 횟수 초과"""
        call_count = [0]
        
        @retry_with_backoff(max_attempts=3, initial_delay=0.01)
        def always_fail():
            call_count[0] += 1
            raise ValueError("항상 실패")
        
        with pytest.raises(ValueError) as exc_info:
            always_fail()
        
        assert call_count[0] == 3
        assert "항상 실패" in str(exc_info.value)
    
    def test_non_retryable_error(self):
        """NonRetryableError는 즉시 발생"""
        call_count = [0]
        
        @retry_with_backoff(max_attempts=3, initial_delay=0.01)
        def non_retryable():
            call_count[0] += 1
            raise NonRetryableError("재시도 불가")
        
        with pytest.raises(NonRetryableError):
            non_retryable()
        
        assert call_count[0] == 1  # 재시도 안함
    
    def test_fixed_interval_strategy(self):
        """FIXED_INTERVAL 전략"""
        call_count = [0]
        
        @retry_with_backoff(
            max_attempts=3, 
            initial_delay=0.01,
            strategy=RetryStrategy.FIXED_INTERVAL
        )
        def fail_twice():
            call_count[0] += 1
            if call_count[0] < 3:
                raise ValueError("실패")
            return "ok"
        
        result = fail_twice()
        assert result == "ok"
        assert call_count[0] == 3
    
    def test_immediate_strategy(self):
        """IMMEDIATE 전략 (즉시 재시도)"""
        call_count = [0]
        
        @retry_with_backoff(
            max_attempts=3, 
            strategy=RetryStrategy.IMMEDIATE
        )
        def fail_once():
            call_count[0] += 1
            if call_count[0] == 1:
                raise ValueError("첫 번째 실패")
            return "ok"
        
        start = time.time()
        result = fail_once()
        duration = time.time() - start
        
        assert result == "ok"
        assert duration < 0.5  # 지연 없이 빠르게 완료
    
    def test_on_retry_callback(self):
        """재시도 콜백 호출"""
        callback_calls = []
        
        def on_retry_callback(attempt, exc):
            callback_calls.append((attempt, str(exc)))
        
        @retry_with_backoff(
            max_attempts=3,
            initial_delay=0.01,
            on_retry=on_retry_callback
        )
        def fail_twice():
            if len(callback_calls) < 2:
                raise ValueError("실패")
            return "ok"
        
        result = fail_twice()
        
        assert result == "ok"
        assert len(callback_calls) == 2
        assert callback_calls[0][0] == 1  # 첫 번째 재시도
        assert callback_calls[1][0] == 2  # 두 번째 재시도
    
    def test_specific_exception_types(self):
        """특정 예외만 재시도"""
        call_count = [0]
        
        @retry_with_backoff(
            max_attempts=3,
            initial_delay=0.01,
            retryable_exceptions=(ValueError,)  # ValueError만 재시도
        )
        def raise_type_error():
            call_count[0] += 1
            raise TypeError("타입 에러")
        
        with pytest.raises(TypeError):
            raise_type_error()
        
        assert call_count[0] == 1  # TypeError는 재시도 안함


# ============================================================================
# Tests: log_execution_time 데코레이터
# ============================================================================

class TestLogExecutionTime:
    """log_execution_time 데코레이터 테스트"""
    
    def test_logs_execution_time(self, caplog):
        """실행 시간 로깅"""
        @log_execution_time(operation_name="테스트 작업")
        def slow_func():
            time.sleep(0.1)
            return "done"
        
        with caplog.at_level('INFO'):
            result = slow_func()
        
        assert result == "done"
        assert "테스트 작업" in caplog.text
        assert "실행 완료" in caplog.text
        assert "소요 시간" in caplog.text
    
    def test_uses_function_name_if_no_operation_name(self, caplog):
        """operation_name 없으면 함수명 사용"""
        @log_execution_time()
        def my_custom_function():
            return "result"
        
        with caplog.at_level('INFO'):
            my_custom_function()
        
        assert "my_custom_function" in caplog.text
    
    def test_logs_error_on_exception(self, caplog):
        """예외 발생 시에도 시간 로깅"""
        @log_execution_time(operation_name="실패 작업")
        def failing_func():
            raise ValueError("에러 발생")
        
        with caplog.at_level('ERROR'):
            with pytest.raises(ValueError):
                failing_func()
        
        assert "실패 작업" in caplog.text
        assert "실행 실패" in caplog.text


# ============================================================================
# Tests: handle_errors 데코레이터
# ============================================================================

class TestHandleErrors:
    """handle_errors 데코레이터 테스트"""
    
    def test_returns_result_on_success(self):
        """성공 시 결과 반환"""
        @handle_errors(default_return=[])
        def successful_func():
            return ["a", "b", "c"]
        
        result = successful_func()
        
        assert result == ["a", "b", "c"]
    
    def test_returns_default_on_error(self):
        """에러 시 기본값 반환"""
        @handle_errors(default_return=[])
        def failing_func():
            raise ValueError("에러")
        
        result = failing_func()
        
        assert result == []
    
    def test_returns_none_default(self):
        """기본값이 None인 경우"""
        @handle_errors(default_return=None)
        def failing_func():
            raise ValueError("에러")
        
        result = failing_func()
        
        assert result is None
    
    def test_logs_error(self, caplog):
        """에러 로깅"""
        @handle_errors(default_return={}, log_error=True)
        def failing_func():
            raise ValueError("상세 에러 메시지")
        
        with caplog.at_level('ERROR'):
            failing_func()
        
        assert "에러 발생" in caplog.text
        assert "failing_func" in caplog.text
    
    def test_no_log_when_disabled(self, caplog):
        """log_error=False일 때 로깅 안함"""
        @handle_errors(default_return=0, log_error=False)
        def failing_func():
            raise ValueError("에러")
        
        with caplog.at_level('ERROR'):
            result = failing_func()
        
        assert result == 0
        # 에러 로그가 없어야 함
        assert "에러 발생" not in caplog.text
    
    def test_reraise_when_enabled(self):
        """reraise=True일 때 에러 재발생"""
        @handle_errors(default_return=None, reraise=True)
        def failing_func():
            raise ValueError("재발생할 에러")
        
        with pytest.raises(ValueError) as exc_info:
            failing_func()
        
        assert "재발생할 에러" in str(exc_info.value)
    
    def test_preserves_function_metadata(self):
        """함수 메타데이터 보존"""
        @handle_errors(default_return=None)
        def documented_function():
            """이것은 문서화된 함수입니다."""
            pass
        
        assert documented_function.__name__ == "documented_function"
        assert "문서화된" in documented_function.__doc__


# ============================================================================
# Tests: RetryableError / NonRetryableError
# ============================================================================

class TestCustomExceptions:
    """커스텀 예외 클래스 테스트"""
    
    def test_retryable_error(self):
        """RetryableError 인스턴스화"""
        error = RetryableError("일시적 오류")
        
        assert str(error) == "일시적 오류"
        assert isinstance(error, Exception)
    
    def test_non_retryable_error(self):
        """NonRetryableError 인스턴스화"""
        error = NonRetryableError("영구적 오류")
        
        assert str(error) == "영구적 오류"
        assert isinstance(error, Exception)


# ============================================================================
# Tests: 데코레이터 조합
# ============================================================================

class TestDecoratorComposition:
    """데코레이터 조합 테스트"""
    
    def test_retry_with_log_and_error_handling(self, caplog):
        """여러 데코레이터 조합"""
        call_count = [0]
        
        @handle_errors(default_return="fallback")
        @log_execution_time(operation_name="조합 테스트")
        @retry_with_backoff(max_attempts=2, initial_delay=0.01)
        def combined_func():
            call_count[0] += 1
            if call_count[0] < 2:
                raise ValueError("첫 번째 실패")
            return "success"
        
        with caplog.at_level('INFO'):
            result = combined_func()
        
        assert result == "success"
        assert call_count[0] == 2
        assert "조합 테스트" in caplog.text

