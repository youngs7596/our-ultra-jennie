import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from datetime import timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from croniter import croniter
from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import BaseModel, Field, validator
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    String,
    Text,
    create_engine,
    func,
    select,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, declarative_base, sessionmaker
from sqlalchemy import inspect

from shared.rabbitmq import RabbitMQPublisher

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("scheduler-service")

SCHEDULER_SCOPE = os.getenv("SCHEDULER_SCOPE", "real")
SCHEDULER_TICK_SECONDS = int(os.getenv("SCHEDULER_TICK_SECONDS", "5"))
SCHEDULER_TIMEZONE = os.getenv("SCHEDULER_TIMEZONE", "Asia/Seoul")
SCHEDULER_DB_PATH = os.getenv("SCHEDULER_DB_PATH", "/app/data/scheduler.db")
RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@rabbitmq:5672/")

# DB 타입 선택 (MARIADB 또는 SQLITE)
DB_TYPE = os.getenv("DB_TYPE", "SQLITE").upper()

try:
    from zoneinfo import ZoneInfo

    TZ = ZoneInfo(SCHEDULER_TIMEZONE)
except Exception:
    logger.warning("⚠️ ZoneInfo 초기화 실패. UTC를 사용합니다.")
    TZ = timezone.utc

# DB 연결 설정
if DB_TYPE == "MARIADB":
    MARIADB_HOST = os.getenv("MARIADB_HOST", "127.0.0.1")
    MARIADB_PORT = os.getenv("MARIADB_PORT", "3306")
    MARIADB_USER = os.getenv("MARIADB_USER", "root")
    MARIADB_PASSWORD = os.getenv("MARIADB_PASSWORD", "")
    MARIADB_DBNAME = os.getenv("MARIADB_DBNAME", "jennie_db")
    
    SQLALCHEMY_DATABASE_URL = f"mysql+pymysql://{MARIADB_USER}:{MARIADB_PASSWORD}@{MARIADB_HOST}:{MARIADB_PORT}/{MARIADB_DBNAME}?charset=utf8mb4"
    engine = create_engine(SQLALCHEMY_DATABASE_URL, pool_pre_ping=True, pool_recycle=3600)
    logger.info(f"🗄️ Scheduler DB: MariaDB ({MARIADB_HOST}:{MARIADB_PORT}/{MARIADB_DBNAME})")
else:
    os.makedirs(os.path.dirname(SCHEDULER_DB_PATH), exist_ok=True)
    SQLALCHEMY_DATABASE_URL = f"sqlite:///{SCHEDULER_DB_PATH}"
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
    )
    logger.info(f"🗄️ Scheduler DB: SQLite ({SCHEDULER_DB_PATH})")

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()
RESCHEDULE_MODES = ("scheduler", "queue")


def _json_dump(data: Optional[Dict]) -> Optional[str]:
    if data is None:
        return None
    return json.dumps(data, ensure_ascii=False, default=str)


def _json_load(data: Optional[str]) -> Dict:
    if not data:
        return {}
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        logger.warning("⚠️ JSON 파싱 실패. 빈 dict 반환.")
        return {}


class Job(Base):
    __tablename__ = "jobs"

    job_id = Column(String(100), primary_key=True)
    scope = Column(String(50), nullable=False, default="real", index=True)
    reschedule_mode = Column(String(50), nullable=False, default="scheduler")
    interval_seconds = Column(Integer, nullable=True)
    description = Column(Text)
    queue = Column(String(200), nullable=False)
    cron_expr = Column(String(100), nullable=False)
    enabled = Column(Boolean, default=True)
    max_parallel = Column(Integer, default=1)
    default_params = Column(Text, nullable=True)
    timeout_sec = Column(Integer, default=120)
    retry_limit = Column(Integer, default=3)
    telemetry_label = Column(String(100), nullable=True)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    next_due_at = Column(DateTime(timezone=True), nullable=True)
    last_run_at = Column(DateTime(timezone=True), nullable=True)
    last_status = Column(String(50), nullable=True)
    last_error = Column(Text, nullable=True)

    def to_dict(self) -> Dict:
        return {
            "job_id": self.job_id,
            "scope": self.scope,
            "description": self.description,
            "queue": self.queue,
            "cron_expr": self.cron_expr,
            "enabled": self.enabled,
            "reschedule_mode": self.reschedule_mode,
            "interval_seconds": self.interval_seconds,
            "max_parallel": self.max_parallel,
            "default_params": _json_load(self.default_params),
            "timeout_sec": self.timeout_sec,
            "retry_limit": self.retry_limit,
            "telemetry_label": self.telemetry_label,
            "created_at": self._iso(self.created_at),
            "updated_at": self._iso(self.updated_at),
            "next_due_at": self._iso(self.next_due_at),
            "last_run_at": self._iso(self.last_run_at),
            "last_status": self.last_status,
            "last_error": self.last_error,
        }

    @staticmethod
    def _iso(value: Optional[datetime]) -> Optional[str]:
        return value.isoformat() if value else None


class JobBase(BaseModel):
    description: Optional[str] = None
    queue: str = Field(..., min_length=1)
    cron_expr: str = Field(..., min_length=3)
    enabled: bool = True
    reschedule_mode: str = Field(default="scheduler")
    interval_seconds: Optional[int] = Field(default=None, ge=5)
    max_parallel: int = Field(default=1, ge=1)
    default_params: Dict = Field(default_factory=dict)
    timeout_sec: int = Field(default=120, ge=10)
    retry_limit: int = Field(default=3, ge=0)
    telemetry_label: Optional[str] = None

    @validator("cron_expr")
    def validate_cron(cls, value: str) -> str:
        if not croniter.is_valid(value):
            raise ValueError("유효하지 않은 cron 표현식입니다.")
        return value

    @validator("reschedule_mode")
    def validate_mode(cls, value: str) -> str:
        if value not in RESCHEDULE_MODES:
            raise ValueError(f"reschedule_mode는 {RESCHEDULE_MODES} 중 하나여야 합니다.")
        return value

    @validator("interval_seconds")
    def validate_interval(cls, value: Optional[int], values):
        mode = values.get("reschedule_mode", "scheduler")
        if mode == "queue" and (value is None or value <= 0):
            raise ValueError("queue 모드에서는 interval_seconds가 필요합니다.")
        return value


class JobCreate(JobBase):
    job_id: str = Field(..., min_length=3)


class JobUpdate(BaseModel):
    description: Optional[str] = None
    queue: Optional[str] = None
    cron_expr: Optional[str] = None
    enabled: Optional[bool] = None
    reschedule_mode: Optional[str] = None
    interval_seconds: Optional[int] = Field(default=None, ge=5)
    max_parallel: Optional[int] = Field(default=None, ge=1)
    default_params: Optional[Dict] = None
    timeout_sec: Optional[int] = Field(default=None, ge=10)
    retry_limit: Optional[int] = Field(default=None, ge=0)
    telemetry_label: Optional[str] = None

    @validator("cron_expr")
    def validate_cron(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not croniter.is_valid(value):
            raise ValueError("유효하지 않은 cron 표현식입니다.")
        return value

    @validator("reschedule_mode")
    def validate_mode(cls, value: Optional[str]) -> Optional[str]:
        if value and value not in RESCHEDULE_MODES:
            raise ValueError(f"reschedule_mode는 {RESCHEDULE_MODES} 중 하나여야 합니다.")
        return value

    @validator("interval_seconds")
    def validate_interval(cls, value: Optional[int], values):
        mode = values.get("reschedule_mode")
        if mode == "queue" and (value is None or value <= 0):
            raise ValueError("queue 모드에서는 interval_seconds가 필요합니다.")
        return value


class ManualRunRequest(BaseModel):
    params: Optional[Dict] = None
    delay_seconds: int = Field(default=0, ge=0)
    trigger_source: str = Field(default="manual", max_length=50)


class JobRunUpdate(BaseModel):
    scope: Optional[str] = None


publisher_cache: Dict[str, RabbitMQPublisher] = {}
apscheduler = BackgroundScheduler(timezone=TZ)


def get_publisher(queue_name: str) -> RabbitMQPublisher:
    if queue_name not in publisher_cache:
        publisher_cache[queue_name] = RabbitMQPublisher(RABBITMQ_URL, queue_name)
    return publisher_cache[queue_name]


def scoped_queue_name(queue: str) -> str:
    if queue.startswith(f"{SCHEDULER_SCOPE}."):
        return queue
    return f"{SCHEDULER_SCOPE}.{queue}"


def compute_next_due(cron_expr: str, base: Optional[datetime] = None) -> datetime:
    reference = base or datetime.now(TZ)
    iterator = croniter(cron_expr, reference)
    next_dt = iterator.get_next(datetime)
    if next_dt.tzinfo is None:
        next_dt = next_dt.replace(tzinfo=TZ)
    return next_dt


def _job_interval_seconds(job: Job) -> int:
    if job.interval_seconds:
        return job.interval_seconds
    base = job.last_run_at or datetime.now(TZ)
    next_time = compute_next_due(job.cron_expr, base)
    subsequent = croniter(job.cron_expr, next_time).get_next(datetime)
    return max(1, int((subsequent - next_time).total_seconds()))
    
def compute_default_delay(job: Job, reference: Optional[datetime] = None) -> Optional[int]:
    return _job_interval_seconds(job)


def schedule_job_execution(
    session: Session,
    job: Job,
    trigger_source: str,
    params_override: Optional[Dict],
    delay_seconds: int = 0,
    ttl_seconds: Optional[int] = None,
) -> str:
    queue_name = scoped_queue_name(job.queue)
    publisher = get_publisher(queue_name)

    payload = {
        "job_id": job.job_id,
        "scope": job.scope,
        "run_id": str(uuid.uuid4()),
        "trigger_source": trigger_source,
        "params": {**_json_load(job.default_params), **(params_override or {})},
        "timeout_sec": job.timeout_sec,
        "retry_limit": job.retry_limit,
        "telemetry_label": job.telemetry_label,
        "queued_at": datetime.now(TZ).isoformat(),
        "auto_reschedule": False,
    }

    ttl_ms = int(ttl_seconds * 1000) if ttl_seconds else None
    message_id = publisher.publish(
        payload,
        delay_seconds=delay_seconds,
        message_ttl_ms=ttl_ms,
    )
    logger.info("📨 Job 메시지 발행 완료 job=%s message=%s", job.job_id, message_id)

    return message_id


def run_scheduler_cycle():
    now = datetime.now(TZ)
    with SessionLocal() as session:
        jobs = (
            session.execute(
                select(Job).where(
                    Job.scope == SCHEDULER_SCOPE,
                    Job.enabled.is_(True),
                )
            )
            .scalars()
            .all()
        )

        for job in jobs:
            try:
                interval = _job_interval_seconds(job)
                # [Jennie's Fix] Ensure last_run has timezone info
                if job.last_run_at:
                    last_run = job.last_run_at if job.last_run_at.tzinfo else job.last_run_at.replace(tzinfo=TZ)
                else:
                    last_run = now - timedelta(seconds=interval)
                next_due = last_run + timedelta(seconds=interval)

                if now < next_due:
                    continue

                ttl_seconds = max(1, int(interval * 0.8))
                schedule_job_execution(
                    session=session,
                    job=job,
                    trigger_source="scheduler",
                    params_override=None,
                    delay_seconds=0,
                    ttl_seconds=ttl_seconds,
                )
                # [Jennie's Fix] last_run_at 업데이트 추가 (중요!)
                job.last_run_at = now
                job.next_due_at = next_due
                job.last_status = "queued"
                job.last_error = None
                session.add(job)
                session.commit()
                logger.info("⏰ Scheduler triggered job=%s (interval=%ss)", job.job_id, interval)
            except Exception as exc:
                logger.exception("❌ Scheduler job 발행 실패 (%s): %s", job.job_id, exc)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def bootstrap_schema():
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    if "jobs" not in tables:
        Base.metadata.create_all(bind=engine)
        return
    columns = {column["name"] for column in inspector.get_columns("jobs")}
    required = {"reschedule_mode", "interval_seconds"}
    missing = required - columns
    if missing:
        logger.warning("jobs 테이블에 누락된 컬럼이 있어 DB를 재생성합니다: %s", missing)
        try:
            engine.dispose()
        except Exception:
            pass
        try:
            os.remove(SCHEDULER_DB_PATH)
        except FileNotFoundError:
            pass
        Base.metadata.create_all(bind=engine)


bootstrap_schema()

app = FastAPI(title="Jennie Scheduler Service", version="0.3.0")


@app.on_event("startup")
async def startup_event():
    if not apscheduler.running:
        apscheduler.add_job(
            run_scheduler_cycle,
            "interval",
            seconds=SCHEDULER_TICK_SECONDS,
            id="scheduler-loop",
            max_instances=1,
            replace_existing=True,
        )
        apscheduler.start()
        logger.info("🚀 APScheduler loop 시작 (tick=%ss)", SCHEDULER_TICK_SECONDS)


@app.on_event("shutdown")
async def shutdown_event():
    if apscheduler.running:
        apscheduler.shutdown(wait=False)
        logger.info("🛑 APScheduler loop 중지")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "scope": SCHEDULER_SCOPE,
        "tick_seconds": SCHEDULER_TICK_SECONDS,
        "db_path": SCHEDULER_DB_PATH,
    }


@app.get("/jobs", response_model=List[Dict])
def list_jobs(db: Session = Depends(get_db)):
    jobs = (
        db.execute(
            select(Job).where(Job.scope == SCHEDULER_SCOPE).order_by(Job.job_id)
        )
        .scalars()
        .all()
    )
    return [job.to_dict() for job in jobs]


@app.post("/jobs", response_model=Dict, status_code=status.HTTP_201_CREATED)
def create_job(job_data: JobCreate, db: Session = Depends(get_db)):
    job = Job(
        job_id=job_data.job_id,
        scope=SCHEDULER_SCOPE,
        reschedule_mode=job_data.reschedule_mode,
        interval_seconds=job_data.interval_seconds,
        description=job_data.description,
        queue=job_data.queue,
        cron_expr=job_data.cron_expr,
        enabled=job_data.enabled,
        max_parallel=job_data.max_parallel,
        default_params=_json_dump(job_data.default_params),
        timeout_sec=job_data.timeout_sec,
        retry_limit=job_data.retry_limit,
        telemetry_label=job_data.telemetry_label,
        next_due_at=compute_next_due(job_data.cron_expr),
    )

    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Job '{job.job_id}'가 이미 존재합니다.",
        )

    db.refresh(job)
    job_dict = job.to_dict()

    if job.enabled:
        interval = _job_interval_seconds(job)
        schedule_job_execution(
            session=db,
            job=job,
            trigger_source="bootstrap",
            params_override=None,
            delay_seconds=0,
            ttl_seconds=max(1, int(interval * 0.8)),
        )
        job_dict = job.to_dict()

    return job_dict


@app.put("/jobs/{job_id}", response_model=Dict)
def update_job(job_id: str, payload: JobUpdate, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job or job.scope != SCHEDULER_SCOPE:
        raise HTTPException(status_code=404, detail="Job을 찾을 수 없습니다.")

    for field, value in payload.dict(exclude_unset=True).items():
        if field == "default_params" and value is not None:
            setattr(job, field, _json_dump(value))
        elif field == "cron_expr" and value:
            setattr(job, field, value)
            job.next_due_at = compute_next_due(value)
        else:
            setattr(job, field, value)

    db.add(job)
    db.commit()
    db.refresh(job)
    return job.to_dict()


@app.post("/jobs/{job_id}/run", response_model=Dict)
def run_job(job_id: str, payload: ManualRunRequest, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job or job.scope != SCHEDULER_SCOPE:
        raise HTTPException(status_code=404, detail="Job을 찾을 수 없습니다.")

    interval = _job_interval_seconds(job)
    message_id = schedule_job_execution(
        session=db,
        job=job,
        trigger_source=payload.trigger_source,
        params_override=payload.params,
        delay_seconds=payload.delay_seconds,
        ttl_seconds=max(1, int(interval * 0.8)),
    )
    return {"message_id": message_id, "job": job.to_dict()}


@app.post("/jobs/{job_id}/last-run", response_model=Dict)
def mark_job_last_run(job_id: str, payload: JobRunUpdate, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    target_scope = payload.scope or SCHEDULER_SCOPE
    if not job or job.scope != target_scope:
        raise HTTPException(status_code=404, detail="Job을 찾을 수 없습니다.")

    job.last_run_at = datetime.now(TZ)
    job.last_status = "done"
    job.last_error = None
    db.add(job)
    db.commit()
    db.refresh(job)
    logger.info("📝 last_run_at 업데이트 job=%s scope=%s", job_id, target_scope)
    return job.to_dict()


@app.post("/jobs/{job_id}/pause", response_model=Dict)
def pause_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job or job.scope != SCHEDULER_SCOPE:
        raise HTTPException(status_code=404, detail="Job을 찾을 수 없습니다.")
    job.enabled = False
    job.last_status = "paused"
    db.add(job)
    db.commit()
    return job.to_dict()


@app.post("/jobs/{job_id}/resume", response_model=Dict)
def resume_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job or job.scope != SCHEDULER_SCOPE:
        raise HTTPException(status_code=404, detail="Job을 찾을 수 없습니다.")
    job.enabled = True
    if not job.next_due_at:
        job.next_due_at = compute_next_due(job.cron_expr)
    db.add(job)
    db.commit()
    db.refresh(job)

    if job.enabled:
        interval = _job_interval_seconds(job)
        schedule_job_execution(
            session=db,
            job=job,
            trigger_source="resume",
            params_override=None,
            delay_seconds=0,
            ttl_seconds=max(1, int(interval * 0.8)),
        )

    return job.to_dict()

