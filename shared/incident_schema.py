from pydantic import BaseModel, Field, field_validator
from typing import List, Dict, Optional, Any, Union
from datetime import datetime
import uuid

class IncidentMeta(BaseModel):
    """메타데이터: 사고 식별 및 환경 정보"""
    error_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = Field(..., description="ISO 8601 formatted timestamp")
    environment: str = Field(default="production")
    commit_hash: Optional[str] = None
    release_id: Optional[str] = None

class ErrorDetails(BaseModel):
    """에러 상세 정보"""
    error_type: str
    message: str
    file_path: Optional[str] = None
    line_number: Optional[int] = None
    function_name: Optional[str] = None
    stack_trace: List[str] = Field(default_factory=list)

class PositionInfo(BaseModel):
    """포지션 정보 (간소화)"""
    symbol: str
    side: str  # LONG, SHORT
    amount: float
    entry_price: float
    unrealized_pnl: Optional[float] = None

class ApiResLog(BaseModel):
    """최근 API 응답 로그"""
    endpoint: str
    status: Union[int, str]
    latency_ms: Optional[float] = None
    timestamp: str
    error_msg: Optional[str] = None

class SystemContext(BaseModel):
    """시스템 및 시장 문맥 정보"""
    position_snapshot: Dict[str, PositionInfo] = Field(default_factory=dict)
    recent_api_responses: List[ApiResLog] = Field(default_factory=list)
    market_context: Dict[str, Any] = Field(default_factory=dict)

class Actionability(BaseModel):
    """자동 조치 가능 여부 판단"""
    auto_fix_allowed: bool = False
    reason: str = "Pending Analysis"

class IncidentReport(BaseModel):
    """AntiGravity Incident Report v1.0"""
    meta: IncidentMeta
    error_details: ErrorDetails
    system_context: SystemContext
    actionability: Actionability
