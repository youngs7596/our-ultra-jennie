import os
import json
import logging
import traceback
import sys
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from filelock import FileLock

# Pydantic Schemas
from shared.incident_schema import (
    IncidentReport, IncidentMeta, ErrorDetails, 
    SystemContext, Actionability, PositionInfo, ApiResLog
)

logger = logging.getLogger(__name__)

class FailureReporter:
    """
    운영 중 발생하는 에러를 포착하여 표준화된 Incident Report(JSONL)로 저장하는 클래스.
    Antigravity 에이전트가 이 로그를 감시합니다.
    """
    
    LOG_FILE_PATH = "logs/incidents.jsonl"
    LOCK_FILE_PATH = "logs/incidents.jsonl.lock"
    
    def __init__(self, execution_context: Dict[str, Any] = None):
        self.execution_context = execution_context or {}
        # 로그 디렉토리 생성
        os.makedirs("logs", exist_ok=True)

    def capture_exception(
        self, 
        exception: Exception, 
        custom_msg: str = None,
        context_override: Dict[str, Any] = None
    ) -> str:
        """
        예외를 캡처하여 Incident Report를 생성하고 파일에 기록합니다.
        
        Returns:
            error_id (str): 생성된 에러 ID
        """
        try:
            # 1. 메타 데이터 생성
            meta = IncidentMeta(
                timestamp=datetime.now(timezone.utc).isoformat(),
                environment=os.getenv("APP_ENV", "production"),
                commit_hash=os.getenv("GIT_COMMIT_HASH", "unknown")
            )
            
            # 2. 에러 상세 정보 추출
            exc_type, exc_value, exc_traceback = sys.exc_info()
            if not exc_type:
                # sys.exc_info()가 없으면 exception 객체에서 추출 시도
                exc_type = type(exception)
                exc_value = exception
            
            # 스택 트레이스 파싱
            tb_list = traceback.format_exception(exc_type, exc_value, exc_traceback)
            
            # 마지막 프레임 정보 추출 (파일 경로, 라인 번호 등)
            last_frame = traceback.extract_tb(exc_traceback)[-1] if exc_traceback else None
            
            error_details = ErrorDetails(
                error_type=exc_type.__name__,
                message=custom_msg or str(exc_value),
                file_path=last_frame.filename if last_frame else "unknown",
                line_number=last_frame.lineno if last_frame else 0,
                function_name=last_frame.name if last_frame else "unknown",
                stack_trace=[line.strip() for line in tb_list]
            )
            
            # 3. 시스템 컨텍스트 구성 (Mockup for now, real implementation needs DI)
            # 실제 구현 시에는 Portfolio Manager나 Market Data 인스턴스를 주입받아야 함
            sys_ctx = self._build_system_context(context_override)
            
            # 4. 조치 가능성 초기 판단 (Rule-based)
            actionability = self._assess_actionability(error_details)
            
            # 5. 리포트 생성
            report = IncidentReport(
                meta=meta,
                error_details=error_details,
                system_context=sys_ctx,
                actionability=actionability
            )
            
            # 6. 파일 저장 (Append-only + FileLock)
            self._append_to_log(report)
            
            logger.info(f"🚨 Incident Reported: {meta.error_id} ({error_details.error_type})")
            return meta.error_id
            
        except Exception as e:
            # 리포터 자체 에러는 로깅만 하고 무시 (운영 영향 최소화)
            logger.error(f"❌ Failed to report incident: {e}")
            return "reporting_failed"

    def _build_system_context(self, override: Dict[str, Any] = None) -> SystemContext:
        """현재 시스템 상태 스냅샷 생성 (보강 필요)"""
        # TODO: 실제 포지션 서비스 연동
        # 지금은 호출 시 전달받은 override나 기본값 사용
        ctx_data = override or {}
        
        return SystemContext(
            position_snapshot=ctx_data.get("position_snapshot", {}),
            recent_api_responses=ctx_data.get("recent_api_responses", []),
            market_context=ctx_data.get("market_context", {"note": "Context data not connected"})
        )

    def _assess_actionability(self, error: ErrorDetails) -> Actionability:
        """
        단순 규칙 기반으로 자동 수정 가능 여부 판단.
        더 복잡한 판단은 Antigravity 에이전트가 수행.
        """
        # 수정 금지/허용 경로 정의
        SAFE_DIRS = ["utils", "infra", "shared"]
        CRITICAL_DIRS = ["strategy", "risk", "execution"]
        
        path = error.file_path or ""
        
        if any(d in path for d in CRITICAL_DIRS):
            return Actionability(
                auto_fix_allowed=False,
                reason="Error in critical logic directory."
            )
        
        if any(d in path for d in SAFE_DIRS):
            return Actionability(
                auto_fix_allowed=True, # 일단 True로 마킹하되 에이전트가 최종 판단
                reason="Error in safe directory. Agent review required."
            )

        return Actionability(auto_fix_allowed=False, reason="Unknown directory path.")

    def _append_to_log(self, report: IncidentReport):
        """JSONL 파일에 Append (Thread-safe/Process-safe)"""
        json_line = report.model_dump_json() + "\n"
        
        lock = FileLock(self.LOCK_FILE_PATH, timeout=5)
        try:
            with lock:
                with open(self.LOG_FILE_PATH, "a", encoding="utf-8") as f:
                    f.write(json_line)
        except TimeoutError:
             logger.error("❌ Failed to acquire lock for incident log.")
