# Scheduler 하이브리드 아키텍처

## 1. 개요

Ultra Jennie의 Scheduler 시스템은 WSL2 Docker Compose 환경에서 동작하며, APScheduler + RabbitMQ 기반으로 모든 마이크로서비스의 자동 실행을 관리합니다.

### 핵심 특징
- **중앙 집중형 스케줄링**: Scheduler Service가 모든 Job을 관리
- **RabbitMQ 기반 메시지 전달**: 각 서비스는 전용 큐를 통해 Job 메시지 수신
- **Mock/Real 스택 분리**: 동일 패턴으로 개발/운영 환경 격리
- **REST API 제어**: Telegram/Admin UI에서 즉시 중단/재개/수동 실행 가능

## 2. 구성 요소

| 컴포넌트 | 역할 |
|----------|------|
| **Scheduler Service** | Job 메타데이터 관리, APScheduler로 주기적 실행, RabbitMQ로 트리거 발행 |
| **Job Store (SQLite/MariaDB)** | `jobs` 테이블에 Job 설정 저장 |
| **RabbitMQ** | Job 메시지 전달, Delay Queue 지원 |
| **Worker Services** | 각 마이크로서비스의 RabbitMQ Consumer |
| **shared/scheduler_client.py** | Worker에서 Job 완료 보고용 클라이언트 |
| **shared/scheduler_runtime.py** | Job 메시지 파싱 및 재스케줄 헬퍼 |

## 3. 아키텍처 다이어그램

```
┌─────────────────────────────────────────────────────────────────┐
│                    Scheduler Service                            │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐          │
│  │ APScheduler │───▶│  Job Store  │───▶│  RabbitMQ   │         │
│  │ (5초 주기)  │    │ (SQLite/DB) │    │  Publisher  │           │
│  └─────────────┘    └─────────────┘    └─────────────┘         │
│                                               │                  │
│  REST API: /jobs, /jobs/{id}/run-now, /jobs/{id}/pause         │
└───────────────────────────────────────────────│──────────────────┘
                                                │
                    ┌───────────────────────────┼───────────────────────┐
                    │                           │                       │
                    ▼                           ▼                       ▼
        ┌───────────────────┐     ┌───────────────────┐    ┌───────────────────┐
        │ real.jobs.scout   │     │ real.jobs.buy-    │    │ real.jobs.price-  │
        │                   │     │ scanner           │    │ monitor           │
        └─────────┬─────────┘     └─────────┬─────────┘    └─────────┬─────────┘
                  │                         │                        │
                  ▼                         ▼                        ▼
        ┌───────────────────┐     ┌───────────────────┐    ┌───────────────────┐
        │   Scout Job       │     │   Buy Scanner     │    │   Price Monitor   │
        │   (Worker)        │     │   (Worker)        │    │   (Worker)        │
        └───────────────────┘     └───────────────────┘    └───────────────────┘
```

## 4. Job 테이블 스키마

```sql
CREATE TABLE jobs (
    job_id          VARCHAR(100) PRIMARY KEY,
    scope           VARCHAR(50) NOT NULL DEFAULT 'real',  -- 'real' | 'mock'
    reschedule_mode VARCHAR(50) NOT NULL DEFAULT 'scheduler',  -- 'scheduler' | 'queue'
    interval_seconds INTEGER,
    cron_expr       VARCHAR(100) NOT NULL,
    queue           VARCHAR(200) NOT NULL,
    enabled         BOOLEAN DEFAULT TRUE,
    max_parallel    INTEGER DEFAULT 1,
    default_params  TEXT,  -- JSON
    timeout_sec     INTEGER DEFAULT 120,
    retry_limit     INTEGER DEFAULT 3,
    description     TEXT,
    telemetry_label VARCHAR(100),
    next_due_at     DATETIME,
    last_run_at     DATETIME,
    last_status     VARCHAR(50),
    last_error      TEXT,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

## 5. Job 메시지 스펙

```json
{
  "job_id": "buy-scanner",
  "scope": "real",
  "run_id": "uuid-v4",
  "trigger_source": "scheduler|manual|auto-reschedule",
  "params": { "scan_mode": "swing" },
  "next_delay_sec": 300,
  "auto_reschedule": true,
  "timeout_sec": 120,
  "retry_limit": 3,
  "telemetry_label": "buy_scanner_job",
  "queued_at": "2024-01-01T09:00:00Z"
}
```

## 6. 스케줄 실행 흐름

### 6.1 Scheduler 모드 (`reschedule_mode=scheduler`)
1. APScheduler가 `SCHEDULER_TICK_SECONDS` (기본 5초) 간격으로 `jobs` 테이블 조회
2. 각 Job의 `cron_expr` 또는 `interval_seconds` 기반으로 다음 실행 시점 계산
3. `now >= next_due`이면 RabbitMQ 큐에 Job 메시지 발행
4. Worker가 메시지 수신 후 작업 수행
5. Worker가 `mark_job_run(job_id)`로 완료 보고

### 6.2 Queue 모드 (`reschedule_mode=queue`)
1. Job 생성/재개 시 Scheduler가 첫 메시지 발행 (Bootstrap)
2. Worker가 작업 완료 후 `reschedule_job()`으로 다음 실행 예약
3. RabbitMQ Delay Queue를 통해 지정 시간 후 메시지 재투입
4. 이후 Worker가 자체적으로 주기 유지

## 7. Worker 구현 패턴

```python
from shared.scheduler_client import mark_job_run
from shared.scheduler_runtime import parse_job_message, reschedule_job
from shared.rabbitmq import RabbitMQWorker, RabbitMQPublisher

# RabbitMQ Worker 초기화
worker = RabbitMQWorker(
    amqp_url=RABBITMQ_URL,
    queue_name="real.jobs.buy-scanner",
    handler=handle_scheduler_job
)

def handle_scheduler_job(payload):
    job = parse_job_message(payload)
    
    try:
        # 실제 작업 수행
        run_scan(**job.params)
        
        # 완료 보고 (Scheduler 모드)
        mark_job_run(job.job_id, scope=job.scope)
        
    except Exception as e:
        logger.error(f"Job 실행 실패: {e}")
    
    finally:
        # Queue 모드인 경우 자체 재스케줄
        if job.auto_reschedule and job.next_delay_sec:
            publisher = RabbitMQPublisher(RABBITMQ_URL, queue_name)
            reschedule_job(publisher, job)

# Worker 시작
worker.start()
```

## 8. REST API

| Method | Endpoint | 설명 |
|--------|----------|------|
| `GET` | `/jobs` | 전체 Job 목록 조회 |
| `POST` | `/jobs` | 새 Job 등록 |
| `GET` | `/jobs/{job_id}` | 특정 Job 상세 조회 |
| `PUT` | `/jobs/{job_id}` | Job 설정 수정 |
| `DELETE` | `/jobs/{job_id}` | Job 삭제 |
| `POST` | `/jobs/{job_id}/run-now` | 즉시 실행 |
| `POST` | `/jobs/{job_id}/pause` | 일시 중지 |
| `POST` | `/jobs/{job_id}/resume` | 재개 |
| `POST` | `/jobs/{job_id}/last-run` | 완료 보고 (Worker용) |

## 9. Mock / Real 스택 분리

```yaml
# docker-compose.yml
services:
  # Real 스택 (port: 8095)
  scheduler-service:
    environment:
      - SCHEDULER_SCOPE=real
      - RABBITMQ_URL=amqp://guest:guest@rabbitmq:5672/
    ports:
      - "8095:8000"

  # Mock 스택 (port: 9095)
  scheduler-service-mock:
    environment:
      - SCHEDULER_SCOPE=mock
      - RABBITMQ_URL=amqp://guest:guest@rabbitmq:5672/
    ports:
      - "9095:8000"
```

- Queue 이름 규칙: `{scope}.jobs.{service-name}`
  - Real: `real.jobs.buy-scanner`, `real.jobs.scout`, ...
  - Mock: `mock.jobs.buy-scanner`, `mock.jobs.scout`, ...

## 10. Job 등록 예시

### 10.1 Real 스택 (http://localhost:8095)

```bash
# Buy Scanner (5분 주기)
curl -X POST http://localhost:8095/jobs -H "Content-Type: application/json" -d '{
  "job_id": "buy-scanner",
  "queue": "real.jobs.buy-scanner",
  "cron_expr": "*/5 * * * *",
  "reschedule_mode": "scheduler",
  "interval_seconds": 300,
  "description": "매수 스캔 자동 실행",
  "enabled": true
}'

# Scout Job (평일 08:00)
curl -X POST http://localhost:8095/jobs -H "Content-Type: application/json" -d '{
  "job_id": "scout-daily",
  "queue": "real.jobs.scout",
  "cron_expr": "0 8 * * 1-5",
  "reschedule_mode": "scheduler",
  "interval_seconds": 86400,
  "description": "Scout 일일 실행",
  "enabled": true
}'

# Price Monitor 시작 (평일 09:00)
curl -X POST http://localhost:8095/jobs -H "Content-Type: application/json" -d '{
  "job_id": "price-monitor-start",
  "queue": "real.jobs.price-monitor",
  "cron_expr": "0 9 * * 1-5",
  "reschedule_mode": "scheduler",
  "default_params": { "action": "start" },
  "interval_seconds": 86400,
  "enabled": true
}'

# Price Monitor 종료 (평일 15:30)
curl -X POST http://localhost:8095/jobs -H "Content-Type: application/json" -d '{
  "job_id": "price-monitor-stop",
  "queue": "real.jobs.price-monitor",
  "cron_expr": "30 15 * * 1-5",
  "reschedule_mode": "scheduler",
  "default_params": { "action": "stop" },
  "interval_seconds": 86400,
  "enabled": true
}'

# News Crawler (10분 주기)
curl -X POST http://localhost:8095/jobs -H "Content-Type: application/json" -d '{
  "job_id": "news-crawler",
  "queue": "real.jobs.news-crawler",
  "cron_expr": "*/10 * * * *",
  "reschedule_mode": "scheduler",
  "interval_seconds": 600,
  "description": "뉴스 크롤링 자동 실행",
  "enabled": true
}'
```

### 10.2 Mock 스택 (http://localhost:9095)

```bash
# Buy Scanner (2분 주기 - 테스트용)
curl -X POST http://localhost:9095/jobs -H "Content-Type: application/json" -d '{
  "job_id": "mock-buy-scanner",
  "queue": "mock.jobs.buy-scanner",
  "cron_expr": "*/2 * * * *",
  "reschedule_mode": "scheduler",
  "interval_seconds": 120,
  "description": "Mock 매수 스캔",
  "enabled": true
}'

# Scout Job (테스트용)
curl -X POST http://localhost:9095/jobs -H "Content-Type: application/json" -d '{
  "job_id": "mock-scout",
  "queue": "mock.jobs.scout",
  "cron_expr": "0 * * * *",
  "reschedule_mode": "scheduler",
  "interval_seconds": 3600,
  "description": "Mock Scout",
  "enabled": true
}'
```

## 11. 환경 변수

| 변수명 | 기본값 | 설명 |
|--------|--------|------|
| `SCHEDULER_SCOPE` | `real` | 스케줄러 스코프 (real/mock) |
| `SCHEDULER_TICK_SECONDS` | `5` | APScheduler 체크 주기 |
| `SCHEDULER_TIMEZONE` | `Asia/Seoul` | 타임존 |
| `SCHEDULER_DB_PATH` | `/app/data/scheduler.db` | SQLite DB 경로 |
| `DB_TYPE` | `SQLITE` | DB 타입 (SQLITE/MARIADB) |
| `RABBITMQ_URL` | `amqp://guest:guest@rabbitmq:5672/` | RabbitMQ 연결 URL |

## 12. Telegram 연동 (예정)

```
/job list          - 전체 Job 목록
/job status <id>   - 특정 Job 상태
/job run <id>      - 즉시 실행
/job pause <id>    - 일시 중지
/job resume <id>   - 재개
```

## 13. 모니터링

- **Prometheus 메트릭**: `jennie_job_duration_seconds`, `jennie_job_runs_total`
- **로그 라벨**: `job_id`, `trigger_source`, `scope`
- **Grafana 대시보드**: Job 실행 현황, 성공/실패율, 지연 시간
