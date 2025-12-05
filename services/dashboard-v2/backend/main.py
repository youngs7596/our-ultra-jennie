#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Dashboard V2 Backend - FastAPI
JWT 인증, WebSocket 실시간 업데이트, REST API
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional
from contextlib import asynccontextmanager
import asyncio
import json
import logging

from fastapi import FastAPI, HTTPException, Depends, WebSocket, WebSocketDisconnect, status, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
import jwt
import redis
import httpx

# --- shared 패키지 임포트 ---
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_dir)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from shared.db.connection import get_session, get_engine, ensure_engine_initialized
from shared.db.repository import (
    get_portfolio_summary,
    get_portfolio_with_current_prices,
    get_watchlist_all,
    get_recent_trades,
    get_scheduler_jobs,
)
from shared.db.models import Portfolio, WatchList, TradeLog

# --- 로깅 설정 (가장 먼저!) ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("dashboard-v2")

# --- secrets.json 로드 및 DB 설정 ---
def load_secrets():
    """secrets.json 파일에서 시크릿 로드"""
    secrets_path = os.getenv("SECRETS_FILE", "/app/config/secrets.json")
    try:
        with open(secrets_path, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"secrets.json 로드 실패: {e}")
        return {}

def setup_db_credentials():
    """MariaDB 자격 증명을 환경 변수로 설정"""
    secrets = load_secrets()
    
    # MariaDB 사용 설정
    if not os.getenv("DB_TYPE"):
        os.environ["DB_TYPE"] = "MARIADB"
        logger.info("✅ DB_TYPE=MARIADB 설정")
    
    # MariaDB 자격 증명 (secrets.json 또는 기본값)
    if not os.getenv("MARIADB_USER"):
        os.environ["MARIADB_USER"] = secrets.get("mariadb-user", "root")
    if not os.getenv("MARIADB_PASSWORD"):
        os.environ["MARIADB_PASSWORD"] = secrets.get("mariadb-password", "q1w2e3R$")
    if not os.getenv("MARIADB_HOST"):
        # Docker 컨테이너에서 Windows MariaDB 접근: host-gateway 사용
        os.environ["MARIADB_HOST"] = secrets.get("mariadb-host", "172.17.0.1")
    if not os.getenv("MARIADB_PORT"):
        os.environ["MARIADB_PORT"] = secrets.get("mariadb-port", "3306")
    if not os.getenv("MARIADB_DBNAME"):
        os.environ["MARIADB_DBNAME"] = secrets.get("mariadb-dbname", "jennie_db")
    
    logger.info(f"✅ MariaDB 설정: {os.getenv('MARIADB_HOST')}:{os.getenv('MARIADB_PORT')}/{os.getenv('MARIADB_DBNAME')}")

# 앱 시작 전에 DB 자격 증명 설정
setup_db_credentials()

# --- 환경 변수 ---
JWT_SECRET = os.getenv("JWT_SECRET", "my-supreme-jennie-jwt-secret-2025")
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_HOURS = 24 * 7  # 7일
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
KIS_GATEWAY_URL = os.getenv("KIS_GATEWAY_URL", "http://kis-gateway:8080")

# --- Pydantic 모델 ---
class LoginRequest(BaseModel):
    username: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: dict

class PortfolioSummary(BaseModel):
    total_value: float
    total_invested: float
    total_profit: float
    profit_rate: float
    cash_balance: float
    positions_count: int

class Position(BaseModel):
    stock_code: str
    stock_name: str
    quantity: int
    avg_price: float
    current_price: float
    profit: float
    profit_rate: float
    weight: float

class WatchlistItem(BaseModel):
    stock_code: str
    stock_name: str
    llm_score: Optional[float]
    per: Optional[float]
    pbr: Optional[float]
    is_tradable: Optional[bool]

class TradeRecord(BaseModel):
    id: int
    stock_code: str
    stock_name: Optional[str]
    trade_type: str
    quantity: int
    price: float
    total_amount: float
    traded_at: datetime
    profit: Optional[float]

class SystemStatus(BaseModel):
    service_name: str
    status: str
    last_run: Optional[datetime]
    next_run: Optional[datetime]
    message: Optional[str]

class ScoutPipelineStatus(BaseModel):
    phase: int
    phase_name: str
    status: str
    progress: float
    current_stock: Optional[str]
    results: Optional[dict]

# --- Redis 연결 ---
redis_client = None

def get_redis():
    global redis_client
    if redis_client is None:
        try:
            redis_client = redis.from_url(REDIS_URL, decode_responses=True)
            redis_client.ping()
            logger.info("✅ Redis 연결 성공")
        except Exception as e:
            logger.warning(f"⚠️ Redis 연결 실패: {e}")
            redis_client = None
    return redis_client

# --- JWT 인증 ---
security = HTTPBearer()

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(hours=JWT_EXPIRATION_HOURS))
    to_encode.update({"exp": expire, "iat": datetime.now(timezone.utc)})
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)

def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        username = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

# --- WebSocket 연결 관리 ---
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []
    
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"WebSocket 연결: {len(self.active_connections)}개 활성")
    
    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        logger.info(f"WebSocket 해제: {len(self.active_connections)}개 활성")
    
    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                pass

manager = ConnectionManager()

# --- Lifespan 이벤트 ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Dashboard V2 Backend 시작")
    # DB 엔진 초기화
    try:
        ensure_engine_initialized()
        logger.info("✅ DB 엔진 초기화 완료")
    except Exception as e:
        logger.error(f"❌ DB 엔진 초기화 실패: {e}")
    # Redis 연결
    get_redis()
    yield
    logger.info("👋 Dashboard V2 Backend 종료")

# --- FastAPI 앱 ---
app = FastAPI(
    title="My Supreme Jennie - Dashboard V2 API",
    description="차세대 AI 자동매매 시스템 대시보드 API",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =============================================================================
# 인증 API
# =============================================================================

@app.post("/api/auth/login", response_model=TokenResponse)
async def login(request: LoginRequest):
    """로그인 및 JWT 토큰 발급"""
    # 환경 변수 또는 secrets.json에서 비밀번호 확인
    secrets = load_secrets()
    valid_password = secrets.get("dashboard-password", os.getenv("DASHBOARD_PASSWORD", "!fpdlwl94A#"))
    valid_username = secrets.get("dashboard-username", os.getenv("DASHBOARD_USERNAME", "admin"))
    
    if request.username != valid_username or request.password != valid_password:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    access_token = create_access_token(
        data={"sub": request.username, "role": "admin"}
    )
    
    return TokenResponse(
        access_token=access_token,
        expires_in=JWT_EXPIRATION_HOURS * 3600,
        user={"username": request.username, "role": "admin"}
    )

@app.get("/api/auth/me")
async def get_current_user(payload: dict = Depends(verify_token)):
    """현재 로그인한 사용자 정보"""
    return {
        "username": payload.get("sub"),
        "role": payload.get("role"),
        "exp": payload.get("exp"),
    }

@app.post("/api/auth/refresh", response_model=TokenResponse)
async def refresh_token(payload: dict = Depends(verify_token)):
    """토큰 갱신"""
    access_token = create_access_token(
        data={"sub": payload.get("sub"), "role": payload.get("role")}
    )
    return TokenResponse(
        access_token=access_token,
        expires_in=JWT_EXPIRATION_HOURS * 3600,
        user={"username": payload.get("sub"), "role": payload.get("role")}
    )

# =============================================================================
# 포트폴리오 API
# =============================================================================

@app.get("/api/portfolio/summary", response_model=PortfolioSummary)
async def get_portfolio_summary_api(payload: dict = Depends(verify_token)):
    """포트폴리오 요약 정보"""
    try:
        with get_session() as session:
            summary = get_portfolio_summary(session)
            return PortfolioSummary(
                total_value=summary.get("total_value", 0),
                total_invested=summary.get("total_invested", 0),
                total_profit=summary.get("total_profit", 0),
                profit_rate=summary.get("profit_rate", 0),
                cash_balance=summary.get("cash_balance", 0),
                positions_count=summary.get("positions_count", 0),
            )
    except Exception as e:
        logger.error(f"포트폴리오 요약 조회 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/portfolio/positions", response_model=list[Position])
async def get_positions_api(payload: dict = Depends(verify_token)):
    """보유 종목 목록 (실시간 가격 포함)"""
    try:
        with get_session() as session:
            positions = get_portfolio_with_current_prices(session)
            return [
                Position(
                    stock_code=p["stock_code"],
                    stock_name=p["stock_name"],
                    quantity=p["quantity"],
                    avg_price=p["avg_price"],
                    current_price=p.get("current_price", p["avg_price"]),
                    profit=p.get("profit", 0),
                    profit_rate=p.get("profit_rate", 0),
                    weight=p.get("weight", 0),
                )
                for p in positions
            ]
    except Exception as e:
        logger.error(f"보유 종목 조회 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# =============================================================================
# Watchlist API
# =============================================================================

@app.get("/api/watchlist", response_model=list[WatchlistItem])
async def get_watchlist_api(
    limit: int = Query(50, ge=1, le=200),
    payload: dict = Depends(verify_token)
):
    """관심 종목 목록"""
    try:
        with get_session() as session:
            items = get_watchlist_all(session, limit=limit)
            return [
                WatchlistItem(
                    stock_code=item.stock_code,
                    stock_name=item.stock_name,
                    llm_score=item.llm_score,
                    per=item.per,
                    pbr=item.pbr,
                    is_tradable=item.is_tradable,
                )
                for item in items
            ]
    except Exception as e:
        logger.error(f"Watchlist 조회 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# =============================================================================
# 거래 내역 API
# =============================================================================

@app.get("/api/trades", response_model=list[TradeRecord])
async def get_trades_api(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    payload: dict = Depends(verify_token)
):
    """거래 내역"""
    try:
        with get_session() as session:
            trades = get_recent_trades(session, limit=limit, offset=offset)
            return [
                TradeRecord(
                    id=t.log_id,
                    stock_code=t.stock_code,
                    stock_name=None,  # TradeLog에는 stock_name이 없음
                    trade_type=t.trade_type,
                    quantity=t.quantity or 0,
                    price=t.price or 0,
                    total_amount=(t.quantity or 0) * (t.price or 0),
                    traded_at=t.trade_timestamp,
                    profit=None,
                )
                for t in trades
            ]
    except Exception as e:
        logger.error(f"거래 내역 조회 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# =============================================================================
# 시스템 상태 API
# =============================================================================

@app.get("/api/system/status", response_model=list[SystemStatus])
async def get_system_status_api(payload: dict = Depends(verify_token)):
    """시스템 서비스 상태 (Docker 컨테이너 기반)"""
    try:
        with get_session() as session:
            jobs = get_scheduler_jobs(session)
            return [
                SystemStatus(
                    service_name=job.get("job_id", "unknown"),
                    status="active" if job.get("enabled") else "inactive",
                    last_run=job.get("last_run_at"),
                    next_run=job.get("next_due_at"),
                    message=f"Queue: {job.get('queue', 'N/A')}",
                )
                for job in jobs
            ]
    except Exception as e:
        logger.error(f"시스템 상태 조회 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/system/docker")
async def get_docker_status_api(payload: dict = Depends(verify_token)):
    """Docker 컨테이너 상태 (WSL2 환경) - Docker Socket API 사용"""
    try:
        # httpx 동기 클라이언트로 Unix 소켓 통신
        transport = httpx.HTTPTransport(uds="/var/run/docker.sock")
        with httpx.Client(transport=transport, base_url="http://localhost") as client:
            response = client.get("/containers/json")
            containers_raw = response.json()
            
            containers = []
            for c in containers_raw:
                containers.append({
                    "ID": c.get("Id", "")[:12],
                    "Names": c.get("Names", [""])[0].lstrip("/"),
                    "Image": c.get("Image", ""),
                    "Status": c.get("Status", ""),
                    "State": c.get("State", ""),
                })
            return {"containers": containers, "count": len(containers)}
        
    except Exception as e:
        logger.error(f"Docker 상태 조회 실패: {e}")
        return {"containers": [], "count": 0, "error": str(e)}

@app.get("/api/system/rabbitmq")
async def get_rabbitmq_status_api(payload: dict = Depends(verify_token)):
    """RabbitMQ 큐 상태 - Management API 사용"""
    try:
        # RabbitMQ Management API (기본 포트 15672)
        rabbitmq_url = os.getenv("RABBITMQ_MANAGEMENT_URL", "http://127.0.0.1:15672")
        rabbitmq_user = os.getenv("RABBITMQ_USER", "guest")
        rabbitmq_pass = os.getenv("RABBITMQ_PASS", "guest")
        
        import base64
        auth = base64.b64encode(f"{rabbitmq_user}:{rabbitmq_pass}".encode()).decode()
        
        async with httpx.AsyncClient() as client:
            # 큐 목록 조회
            response = await client.get(
                f"{rabbitmq_url}/api/queues",
                headers={"Authorization": f"Basic {auth}"},
                timeout=10.0
            )
            
            if response.status_code == 200:
                queues_raw = response.json()
                queues = []
                for q in queues_raw:
                    queues.append({
                        "name": q.get("name", ""),
                        "messages": q.get("messages", 0),
                        "messages_ready": q.get("messages_ready", 0),
                        "messages_unacknowledged": q.get("messages_unacknowledged", 0),
                        "consumers": q.get("consumers", 0),
                        "state": q.get("state", "unknown")
                    })
                return {"queues": queues, "count": len(queues)}
            else:
                return {"queues": [], "error": f"HTTP {response.status_code}"}
                
    except Exception as e:
        logger.error(f"RabbitMQ 상태 조회 실패: {e}")
        return {"queues": [], "error": str(e)}

@app.get("/api/system/scheduler")
async def get_scheduler_jobs_api(payload: dict = Depends(verify_token)):
    """Scheduler Jobs 상태 - DB에서 조회"""
    try:
        from sqlalchemy import text
        with get_session() as session:
            # scheduler.jobs 테이블에서 작업 목록 조회
            result = session.execute(text("""
                SELECT 
                    job_id,
                    queue,
                    cron_expr,
                    interval_seconds,
                    enabled,
                    last_run_at,
                    last_status,
                    description,
                    created_at
                FROM jobs
                ORDER BY job_id
            """))
            
            jobs = []
            for row in result:
                # interval_seconds를 사람이 읽기 쉬운 형태로 변환
                interval = row.interval_seconds or 0
                if interval >= 3600:
                    interval_str = f"{interval // 3600}시간"
                elif interval >= 60:
                    interval_str = f"{interval // 60}분"
                elif interval > 0:
                    interval_str = f"{interval}초"
                else:
                    interval_str = row.cron_expr or "N/A"
                
                jobs.append({
                    "name": row.job_id,
                    "queue": row.queue,
                    "interval": interval_str,
                    "interval_seconds": interval,
                    "cron": row.cron_expr,
                    "is_active": row.enabled,
                    "last_run": row.last_run_at.isoformat() if row.last_run_at else None,
                    "last_status": row.last_status or "unknown",
                    "description": row.description,
                    "created_at": row.created_at.isoformat() if row.created_at else None
                })
            
            return {"jobs": jobs, "count": len(jobs)}
            
    except Exception as e:
        logger.error(f"Scheduler Jobs 조회 실패: {e}")
        return {"jobs": [], "error": str(e)}

@app.get("/api/system/logs/{container_name}")
async def get_container_logs_api(
    container_name: str,
    limit: int = 100,
    since: str = "1h",
    payload: dict = Depends(verify_token)
):
    """Loki에서 컨테이너 로그 조회"""
    try:
        loki_url = os.getenv("LOKI_URL", "http://127.0.0.1:3100")
        
        # Loki 쿼리 - container 라벨로 필터링 (Promtail 설정에 따라 다름)
        query = f'{{container="{container_name}"}}'
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{loki_url}/loki/api/v1/query_range",
                params={
                    "query": query,
                    "limit": limit,
                    "since": since,
                },
                timeout=30.0
            )
            
            if response.status_code == 200:
                data = response.json()
                logs = []
                
                # Loki 응답 파싱
                results = data.get("data", {}).get("result", [])
                for stream in results:
                    for value in stream.get("values", []):
                        timestamp, log_line = value
                        # 타임스탬프를 사람이 읽기 쉬운 형태로 변환
                        from datetime import datetime
                        ts = datetime.fromtimestamp(int(timestamp) / 1e9)
                        logs.append({
                            "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
                            "message": log_line
                        })
                
                # 시간순 정렬 (최신이 아래로)
                logs.sort(key=lambda x: x["timestamp"])
                
                return {
                    "container": container_name,
                    "logs": logs[-limit:],  # 최신 limit개만
                    "count": len(logs)
                }
            else:
                return {
                    "container": container_name,
                    "logs": [],
                    "error": f"Loki HTTP {response.status_code}"
                }
                
    except Exception as e:
        logger.error(f"로그 조회 실패 ({container_name}): {e}")
        return {
            "container": container_name,
            "logs": [],
            "error": str(e)
        }

# =============================================================================
# Scout Pipeline API
# =============================================================================

@app.get("/api/scout/status")
async def get_scout_status_api(payload: dict = Depends(verify_token)):
    """Scout Pipeline 현재 상태"""
    r = get_redis()
    if not r:
        return {"status": "unknown", "message": "Redis 연결 실패"}
    
    try:
        status = r.hgetall("scout:pipeline:status")
        return {
            "phase": int(status.get("phase", 0)),
            "phase_name": status.get("phase_name", "대기"),
            "status": status.get("status", "idle"),
            "progress": float(status.get("progress", 0)),
            "current_stock": status.get("current_stock"),
            "total_candidates": int(status.get("total_candidates", 0)),
            "passed_phase1": int(status.get("passed_phase1", 0)),
            "passed_phase2": int(status.get("passed_phase2", 0)),
            "final_selected": int(status.get("final_selected", 0)),
            "last_updated": status.get("last_updated"),
        }
    except Exception as e:
        logger.error(f"Scout 상태 조회 실패: {e}")
        return {"status": "error", "message": str(e)}

@app.get("/api/scout/results")
async def get_scout_results_api(payload: dict = Depends(verify_token)):
    """Scout Pipeline 최근 결과"""
    r = get_redis()
    if not r:
        return {"results": [], "message": "Redis 연결 실패"}
    
    try:
        results_json = r.get("scout:pipeline:results")
        if results_json:
            return {"results": json.loads(results_json)}
        return {"results": []}
    except Exception as e:
        logger.error(f"Scout 결과 조회 실패: {e}")
        return {"results": [], "error": str(e)}

# =============================================================================
# 뉴스 & 감성 API
# =============================================================================

@app.get("/api/news/sentiment")
async def get_news_sentiment_api(
    stock_code: Optional[str] = None,
    limit: int = Query(20, ge=1, le=100),
    payload: dict = Depends(verify_token)
):
    """뉴스 감성 점수 (shared/database.py의 sentiment:{code} 키 사용)"""
    r = get_redis()
    if not r:
        return {"items": [], "message": "Redis 연결 실패"}
    
    try:
        if stock_code:
            # 특정 종목의 감성 점수
            data = r.get(f"sentiment:{stock_code}")
            if data:
                import json
                parsed = json.loads(data)
                return {
                    "stock_code": stock_code,
                    "sentiment_score": parsed.get("score"),
                    "reason": parsed.get("reason"),
                    "updated_at": parsed.get("updated_at"),
                }
            return {
                "stock_code": stock_code,
                "sentiment_score": None,
            }
        else:
            # 전체 감성 점수 (상위 N개) - sentiment:* 키 조회
            keys = r.keys("sentiment:*")
            items = []
            import json
            for key in keys[:limit]:
                code = key.split(":")[-1] if isinstance(key, str) else key.decode().split(":")[-1]
                data = r.get(key)
                if data:
                    parsed = json.loads(data) if isinstance(data, str) else json.loads(data.decode())
                    items.append({
                        "stock_code": code,
                        "sentiment_score": parsed.get("score"),
                        "reason": parsed.get("reason"),
                    })
            items.sort(key=lambda x: x["sentiment_score"] or 0, reverse=True)
            return {"items": items}
    except Exception as e:
        logger.error(f"뉴스 감성 조회 실패: {e}")
        return {"items": [], "error": str(e)}

# =============================================================================
# WebSocket 실시간 업데이트
# =============================================================================

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """실시간 업데이트 WebSocket"""
    await manager.connect(websocket)
    try:
        while True:
            # 클라이언트로부터 메시지 수신 (ping/pong)
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        manager.disconnect(websocket)

# =============================================================================
# 헬스 체크
# =============================================================================

@app.get("/health")
async def health_check():
    """헬스 체크"""
    return {
        "status": "healthy",
        "service": "dashboard-v2-backend",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

@app.get("/")
async def root():
    """루트 엔드포인트"""
    return {
        "service": "My Supreme Jennie - Dashboard V2 API",
        "version": "2.0.0",
        "docs": "/docs",
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8090)

