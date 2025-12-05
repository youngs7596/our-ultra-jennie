## Scheduler 하이브리드 아키텍처

### 1. 목표
- GCP Cloud Scheduler 없이도 모든 마이크로서비스가 자동 순환.
- 주기·파라미터를 코드 수정 없이 외부에서 조정.
- Telegram·Admin UI로 즉시 중단/재개/수동 실행 가능.
- Mock / Real 스택 양쪽에서 동일 패턴 재사용.

### 2. 구성 요소
| 컴포넌트 | 역할 |
| --- | --- |
| Scheduler Service | Job 메타데이터 조회, RabbitMQ로 트리거 퍼블리시, API/Telegram 제어 |
| Job Store (DB) | `jobs` 테이블: `job_id`, `reschedule_mode`, `interval_seconds`, `cron_expr`, `enabled`, `params`, `queue`, `max_parallel`, `next_due_at` |
| RabbitMQ Delay Pattern | Per-job 큐 + DLX/TTL 조합으로 “n초 후 재투입” 구현 |
| Worker Services | 각 마이크로서비스의 RabbitMQ Consumer, 부팅 시 Startup oneshot 1회 실행, Job 완료 후 Scheduler API(`/jobs/{id}/last-run`)에 보고 |
| Telegram/Admin | `/job status`, `/job run`, `/job pause` 등 명령 → Scheduler API 호출 |
| Observability | Loki 라벨(`job_id`, `trigger_source`), Prometheus `jennie_job_duration_seconds` 등 |

### 3. Job 메시지 스펙
```json
{
  "job_id": "buy-scanner-scan",
  "trigger_source": "scheduler|manual|retry",
  "params": { "scan_mode": "swing" },
  "next_delay_sec": 600,
  "timeout_sec": 120,
  "max_attempts": 3,
  "run_id": "uuid"
}
```
- Scheduler Service는 `cron_expr`를 파싱해 첫 메시지를 큐에 넣고, Worker는 실행 후 `next_delay_sec` 만큼 지연된 메시지를 다시 발행.
- Manual trigger는 `trigger_source=manual`, `next_delay_sec`는 Job 설정에서 재계산.

### 4. DB 스키마 초안 (`scheduler.jobs`)
```sql
price-monitor  | [2025-12-01 13:40:59 +0000] [7] [ERROR] Exception in worker process
price-monitor  | Traceback (most recent call last):
price-monitor  |   File "/usr/local/lib/python3.11/site-packages/gunicorn/arbiter.py", line 609, in spawn_worker
price-monitor  |     worker.init_process()
price-monitor  |   File "/usr/local/lib/python3.11/site-packages/gunicorn/workers/base.py", line 134, in init_process
price-monitor  |     self.load_wsgi()
price-monitor  |   File "/usr/local/lib/python3.11/site-packages/gunicorn/workers/base.py", line 146, in load_wsgi
price-monitor  |     self.wsgi = self.app.wsgi()
price-monitor  |                 ^^^^^^^^^^^^^^^
price-monitor  |   File "/usr/local/lib/python3.11/site-packages/gunicorn/app/base.py", line 67, in wsgi
price-monitor  |     self.callable = self.load()
price-monitor  |                     ^^^^^^^^^^^
price-monitor  |   File "/usr/local/lib/python3.11/site-packages/gunicorn/app/wsgiapp.py", line 58, in load
price-monitor  |     return self.load_wsgiapp()
price-monitor  |            ^^^^^^^^^^^^^^^^^^^
price-monitor  |   File "/usr/local/lib/python3.11/site-packages/gunicorn/app/wsgiapp.py", line 48, in load_wsgiapp
price-monitor  |     return util.import_app(self.app_uri)
price-monitor  |            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
price-monitor  |   File "/usr/local/lib/python3.11/site-packages/gunicorn/util.py", line 371, in import_app
price-monitor  |     mod = importlib.import_module(module)
price-monitor  |           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
price-monitor  |   File "/usr/local/lib/python3.11/importlib/__init__.py", line 126, in import_module
price-monitor  |     return _bootstrap._gcd_import(name[level:], package, level)
price-monitor  |            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
price-monitor  |   File "<frozen importlib._bootstrap>", line 1204, in _gcd_import
price-monitor  |   File "<frozen importlib._bootstrap>", line 1176, in _find_and_load
price-monitor  |   File "<frozen importlib._bootstrap>", line 1147, in _find_and_load_unlocked
price-monitor  |   File "<frozen importlib._bootstrap>", line 690, in _load_unlocked
price-monitor  |   File "<frozen importlib._bootstrap_external>", line 940, in exec_module
price-monitor  |   File "<frozen importlib._bootstrap>", line 241, in _call_with_frames_removed
price-monitor  |   File "/app/main.py", line 294, in <module>
price-monitor  |     raise RuntimeError("Service initialization failed")
price-monitor  | RuntimeError: Service initialization failed
price-monitor  | 2025-12-01 13:40:59,170 - pika.adapters.utils.connection_workflow - INFO - Pika version 1.3.2 connecting to ('172.18.0.7', 5672)
price-monitor  | [2025-12-01 13:40:59 +0000] [7] [INFO] Worker exiting (pid: 7)
price-monitor  | 2025-12-01 13:40:59,171 - pika.adapters.utils.io_services_utils - INFO - Socket connected: <socket.socket fd=15, family=2, type=1, proto=6, laddr=('172.18.0.18', 59396), raddr=('172.18.0.7', 5672)>
price-monitor  | 2025-12-01 13:40:59,171 - pika.adapters.utils.connection_workflow - INFO - Streaming transport linked up: (<pika.adapters.utils.io_services_utils._AsyncPlaintextTransport object at 0x70c982dc89d0>, _StreamingProtocolShim: <SelectConnection PROTOCOL transport=<pika.adapters.utils.io_services_utils._AsyncPlaintextTransport object at 0x70c982dc89d0> params=<URLParameters host=rabbitmq port=5672 virtual_host=/ ssl=False>>).
price-monitor  | 2025-12-01 13:40:59,184 - pika.adapters.utils.connection_workflow - INFO - AMQPConnector - reporting success: <SelectConnection OPEN transport=<pika.adapters.utils.io_services_utils._AsyncPlaintextTransport object at 0x70c982dc89d0> params=<URLParameters host=rabbitmq port=5672 virtual_host=/ ssl=False>>
price-monitor  | 2025-12-01 13:40:59,184 - pika.adapters.utils.connection_workflow - INFO - AMQPConnectionWorkflow - reporting success: <SelectConnection OPEN transport=<pika.adapters.utils.io_services_utils._AsyncPlaintextTransport object at 0x70c982dc89d0> params=<URLParameters host=rabbitmq port=5672 virtual_host=/ ssl=False>>
price-monitor  | 2025-12-01 13:40:59,184 - pika.adapters.blocking_connection - INFO - Connection workflow succeeded: <SelectConnection OPEN transport=<pika.adapters.utils.io_services_utils._AsyncPlaintextTransport object at 0x70c982dc89d0> params=<URLParameters host=rabbitmq port=5672 virtual_host=/ ssl=False>>
price-monitor  | 2025-12-01 13:40:59,184 - pika.adapters.blocking_connection - INFO - Created channel=1
price-monitor  | [2025-12-01 13:40:59 +0000] [1] [ERROR] Worker (pid:7) exited with code 3
price-monitor  | [2025-12-01 13:40:59 +0000] [1] [ERROR] Shutting down: Master
price-monitor  | [2025-12-01 13:40:59 +0000] [1] [ERROR] Reason: Worker failed to boot.
```
- `next_due_at`는 Scheduler Service가 계산해 저장 → 재시작 시에도 이어받기 가능.
- `reschedule_mode="queue"` 인 Job은 Worker가 RabbitMQ Delay 큐를 사용해 자체적으로 다음 실행을 예약합니다. Scheduler는 최초 시드 및 Resume 시에만 메시지를 주입합니다.

### 5. 스케줄 흐름
1. APScheduler가 `SCHEDULER_TICK_SECONDS` 간격으로 `jobs` 테이블을 조회합니다.
2. 각 Job에 대해 `last_run_at` 또는 `cron_expr` 기반으로 다음 실행 시점(`next_due`)을 계산합니다.
3. `now >= next_due`이면 RabbitMQ 큐에 Job 메시지를 발행하며, 메시지 TTL은 `interval_seconds * 0.8` 로 설정해 Worker가 다운돼도 큐에 메시지가 무한히 쌓이지 않게 합니다.
4. Worker 서비스는 메시지를 소비한 뒤 실제 작업을 수행하고, 성공 시 `shared.scheduler_client.mark_job_run(job_id)` 를 호출하여 Scheduler Service의 `last_run_at`을 갱신합니다.
5. 각 Worker는 컨테이너 부팅 시 `startup_oneshot` 메시지를 1회 발행해 첫 실행이 지연되지 않도록 합니다.

### 6. Worker 패턴
```python
from shared.scheduler_client import mark_job_run
from shared.scheduler_runtime import parse_job_message

worker = RabbitMQWorker(queue="real.jobs.buy-scanner")

def handle(payload):
    job = parse_job_message(payload)
    run_scan(**job.params)
    mark_job_run(job.job_id, scope=job.scope)
```
- Worker는 더 이상 delay 큐를 직접 만지지 않고, 중앙 Scheduler가 발행해준 메시지만 소비합니다.
- Startup 시 `startup_oneshot` 메시지를 1회 발행해 첫 실행이 지연되지 않도록 합니다.
- 메시지는 TTL이 설정되어 있으므로 Worker가 장시간 중지돼도 큐에 무한히 쌓이지 않습니다.

### 7. Telegram / Admin 통합
- Telegram Bot 명령 → `scheduler-service` REST API:
  - `POST /jobs/{job_id}/run-now`
  - `POST /jobs/{job_id}/pause`
  - `POST /jobs/{job_id}/resume`
  - `GET /jobs` (상태 요약)
- Admin UI는 동일 API를 사용, Grafana/Streamlit 대시보드에 iframe 또는 링크로 연결.

### 8. Mock / Real 동시 운영
- Scheduler Service는 `profile` 파라미터를 받아 `jobs` 테이블 내 `scope` 컬럼(`mock`/`real`)을 구분.
- RabbitMQ도 `queue = f"{scope}.{job_id}"` 방식으로 격리.
- Mock Compose 프로필에서 Scheduler + Worker 모두 `scope=mock`으로 기동.

### 9. 단계적 도입 플랜
1. Scheduler Service 스켈레톤 + DB 마이그레이션 작성.
2. RabbitMQ Delay Helper (`shared/rabbitmq_scheduler.py`) 추가.
3. buy-scanner → price-monitor → news-crawler → scout-job 순으로 Worker 패턴 적용.
4. Telegram/Admin 명령선 추가 및 Grafana 패널 구성.
5. Mock 스택 분리 및 CI 테스트 시나리오 정립.

위 아키텍처를 기준으로 다음 단계(서비스 구현)에 착수합니다.

### 10. Job 등록 치트시트

#### 10.1 Real (Scheduler API: `http://localhost:8095`)
```bash
# Buy Scanner (5분 주기)
curl -X POST http://localhost:8095/jobs -H "Content-Type: application/json" -d '{
  "job_id": "buy-scanner",
  "queue": "real.jobs.buy-scanner",
  "cron_expr": "*/5 * * * *",
  "reschedule_mode": "queue",
  "interval_seconds": 300,
  "description": "Buy Scanner auto cycle",
  "enabled": true
}'

# News Crawler (10분 주기)
curl -X POST http://localhost:8095/jobs -H "Content-Type: application/json" -d '{
  "job_id": "news-crawler",
  "queue": "real.jobs.news-crawler",
  "cron_expr": "*/10 * * * *",
  "reschedule_mode": "queue",
  "interval_seconds": 600,
  "description": "Real news crawler auto",
  "enabled": true
}'

# Price Monitor 시작/종료 (평일 09:00 / 15:30)
curl -X POST http://localhost:8095/jobs -H "Content-Type: application/json" -d '{
  "job_id": "price-monitor-start",
  "queue": "real.jobs.price-monitor",
  "cron_expr": "0 9 * * 1-5",
  "reschedule_mode": "queue",
  "default_params": { "action": "start", "interval_seconds": 86400 },
  "enabled": true
}'

curl -X POST http://localhost:8095/jobs -H "Content-Type: application/json" -d '{
  "job_id": "price-monitor-stop",
  "queue": "real.jobs.price-monitor",
  "cron_expr": "30 15 * * 1-5",
  "reschedule_mode": "queue",
  "default_params": { "action": "stop", "interval_seconds": 86400 },
  "enabled": true
}'

# Scout Job (평일 08:00)
curl -X POST http://localhost:8095/jobs -H "Content-Type: application/json" -d '{
  "job_id": "scout-daily",
  "queue": "real.jobs.scout",
  "cron_expr": "0 8 * * 1-5",
  "reschedule_mode": "queue",
  "interval_seconds": 86400,
  "description": "Scout daily run",
  "enabled": true
}'
```
- Job 입력 시 `queue`는 `real.jobs.*` 형태를 사용하고, Price Monitor처럼 액션이 필요한 경우 `default_params.action`을 활용합니다.

#### 10.2 Mock (Scheduler API: `http://localhost:9095`)
```bash
# Buy Scanner (2분 주기)
curl -X POST http://localhost:9095/jobs -H "Content-Type: application/json" -d '{
  "job_id": "mock-buy-scanner",
  "queue": "mock.jobs.buy-scanner",
  "cron_expr": "*/5 * * * *",
  "reschedule_mode": "queue",
  "interval_seconds": 120,
  "description": "Mock buy scanner auto",
  "enabled": true
}'

# News Crawler (10분 주기)
curl -X POST http://localhost:9095/jobs -H "Content-Type: application/json" -d '{
  "job_id": "mock-news",
  "queue": "mock.jobs.news-crawler",
  "cron_expr": "*/10 * * * *",
  "reschedule_mode": "queue",
  "interval_seconds": 600,
  "enabled": true
}'

# Price Monitor Start & Stop
curl -X POST http://localhost:9095/jobs -H "Content-Type: application/json" -d '{
  "job_id": "mock-price-start",
  "queue": "mock.jobs.price-monitor",
  "cron_expr": "0 0 * * *",
  "reschedule_mode": "queue",
  "default_params": { "action": "start", "interval_seconds": 86400 },
  "enabled": true
}'

curl -X POST http://localhost:9095/jobs -H "Content-Type: application/json" -d '{
  "job_id": "mock-price-stop",
  "queue": "mock.jobs.price-monitor",
  "cron_expr": "0 1 * * *",
  "reschedule_mode": "queue",
  "default_params": { "action": "stop", "interval_seconds": 86400 },
  "enabled": true
}'

# Scout Job
curl -X POST http://localhost:9095/jobs -H "Content-Type: application/json" -d '{
  "job_id": "mock-scout",
  "queue": "mock.jobs.scout",
  "cron_expr": "0 9 * * 1-5",
  "reschedule_mode": "queue",
  "interval_seconds": 86400,
  "description": "Mock scout job",
  "enabled": true
}'
```
- Mock Scheduler는 `SCHEDULER_SCOPE=mock` 인스턴스(`scheduler-service-mock`)에서 기동되며, Queue 이름도 `mock.*`를 사용합니다.
- Queue 모드 Job은 생성/재개 시 한 번만 Scheduler가 메시지를 시드하며, 이후에는 서비스 Worker가 RabbitMQ delay 큐를 통해 주기적으로 자가 실행합니다.

