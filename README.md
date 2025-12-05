# My Supreme Jennie - 차세대 AI 자동매매 시스템 (MSA)

## 📋 프로젝트 소개
`my-supreme-jennie`는 기존 모놀리식 자동매매 시스템을 **MSA(Microservices Architecture)**로 재설계하여 확장성, 안정성, 비용 효율성을 극대화한 차세대 트레이딩 시스템입니다. 2025년 11월 이후 모든 인프라를 GCP에서 WSL2 + Docker Compose 환경으로 이관하여 **로컬 슈퍼컴퓨터 한 대로 Real/Mock 스택을 동시에 돌리는** 구조를 완성했습니다. 모든 서비스는 동일한 코드와 설정을 공유하고, 프로파일만 바꿔 Mock ↔ Real 모드를 즉시 전환할 수 있습니다.

## 🎯 핵심 가치
- **Architecture**: 10개의 독립적인 마이크로서비스 (실시간 WebSocket 감시 포함)
- **Performance**: 병렬 처리로 데이터 수집/분석 속도 10배 향상
- **Stability**: KIS Gateway v2를 통한 중앙화된 트래픽 제어 및 Client-side Pacing
- **Intelligence**: Multi-Agent LLM (Claude Haiku 4.5 + GPT-5-mini) + RAG 기반의 Scout-Debate-Judge 파이프라인 및 실시간 뉴스 감성 분석
- **Notification**: 실시간 텔레그램 알림으로 모든 거래 추적 및 일일 브리핑

## 🏗️ 아키텍처 (총 10개 서비스)

### 🛡️ Core Infrastructure
1. **KIS Gateway v2** (`services/kis-gateway`)
   - **Global Rate Limiting**: Redis 기반 계좌 단위 제한 (REAL: 19 req/s, MOCK: 2 req/s)
   - **Circuit Breaker**: 연속 20회 실패 시 자동 차단 및 복구 (pybreaker)
   - **Token Management**: 단일 인스턴스(Singleton) 운영으로 토큰 충돌 방지 및 안정성 확보
   - **Token Provider API**: `/api/token` 엔드포인트로 모든 서비스가 Gateway를 통해 KIS Access Token을 공유 (force refresh 지원)
2. **Dashboard** (`services/dashboard`)
   - **Tech Stack**: Streamlit, Multi-Page App
   - **Features**: 포트폴리오 모니터링, 시스템 제어, RAG 챗봇, 수동 주문
3. **Scheduler Service (NEW)** (`services/scheduler-service`)
   - **Trigger Hub**: Cloud Scheduler 대체. APScheduler + `jobs` 테이블(Real/Mock scope 분리)로 모든 마이크로서비스 트리거 관리
   - **Control Plane**: FastAPI + Telegram/Admin API로 Job 생성/수정/수동 실행/일시정지
   - **Hybrid Mode**: **중앙 스케줄러 + Bootstrap One-shot**. 각 워커는 부팅 시 1회만 Job을 요청하고, 이후 반복 실행은 Scheduler Service가 RabbitMQ 큐에 TTL이 있는 메시지를 발행하여 관리합니다.

### 📊 Trading Engine (5개 서비스)
3. **Buy Scanner** (`services/buy-scanner`)
   - **Trigger**: Scheduler Service + RabbitMQ (jobs 테이블 설정; 컨테이너는 부팅 시 Startup oneshot 메시지를 1회 발행해 즉시 첫 스캔을 수행)
   - **Role**: 기술적/펀더멘털 분석 + **실시간 뉴스 감성 점수** 반영하여 매수 후보 발굴
   - **Scheduler Queue**: `real.jobs.buy-scanner` (Scheduler Service가 interval마다 TTL이 있는 메시지를 발행, Worker는 self-reschedule 하지 않음)
4. **Buy Executor** (`services/buy-executor`)
   - **Trigger**: RabbitMQ (`buy-signals`) 또는 HTTP
   - **Role**: ⚡ DB 점수 확인 후 즉시 매수 주문 실행 (Fast Hands)
5. **Sell Executor** (`services/sell-executor`)
   - **Trigger**: RabbitMQ (`sell-orders`)
   - **Role**: 매도 주문 실행 및 복기(Reflection) 데이터 저장
6. **Price Monitor (Always-on)** (`services/price-monitor`)
   - **Trigger**: Scheduler Service Start/Stop Job (cron 기반). 컨테이너는 Startup oneshot 메시지로 즉시 “start” Job을 요청.
   - **Role**: 보유 종목 실시간 감시 (WebSocket/Polling 하이브리드)
   - **Logic**: 수익률/손절가 도달 시 Sell Executor 호출 (Cloud Tasks)
   - **Scheduler Queue**: `real.jobs.price-monitor` (start/stop 액션을 Scheduler Service가 발행)
   - **Token Source**: `KIS_TOKEN_PROVIDER_URL=http://kis-gateway:8080/api/token` (Mock: `kis-gateway-mock`)을 통해 Gateway가 발급한 토큰만 재사용
7. **Command Handler** (`services/command-handler`)
   - **Trigger**: Scheduler Service (기본 1분)
   - **Role**: Dashboard 등 외부 명령(수동 매매 등) 비동기 처리

### 🧠 Intelligence & Data (3개 서비스)
8. **Scout Job** (`services/scout-job`)
   - **Trigger**: Scheduler Service (기본 1시간). Worker는 부팅 시 Startup oneshot 1회만 발행하고, 반복 주기는 중앙 스케줄러가 관리.
   - **Role**: 3-Phase LLM Pipeline (Scout-Debate-Judge), Watchlist 갱신, 파라미터 최적화
   - **Hybrid Scoring System (v5.0) - NEW!**:
     - **Offline Analysis**: 2년치 데이터로 팩터 예측력 분석 (IC/IR, 뉴스 카테고리별 승률)
     - **Online Scoring**: QuantScorer로 1차 필터링 -> LLM 정성 분석 -> HybridScorer로 최종 점수 결합
     - **핵심 발견**: RSI 과매도(54.6%), ROE(54.5%)가 유일하게 유효한 팩터. 뉴스 호재는 역신호(47.3%)!
     - **상세 문서**: [docs/SCOUT_V5_HYBRID_SCORING.md](docs/SCOUT_V5_HYBRID_SCORING.md)
   - **LLM Strategy (v4.0)**: 
     - Phase 1 (Hunter Scout): **Claude Haiku 4.5**로 빠르고 정확한 광역 필터링 (60점 컷오프)
     - Phase 2 (Debate): GPT-5-mini로 Bull vs Bear 심층 토론
     - Phase 3 (Judge): GPT-5-mini로 최종 스코어링 및 등급 부여 (기본 50점 기준)
   - **News Integration**: **ChromaDB 직접 조회**로 실시간 뉴스 반영 (rag-cacher 제거)
   - **Cost Optimization**: 캐싱, Cooldown, Diff 기반 호출로 99% 비용 절감
   - **Scheduler Queue**: `real.jobs.scout` (Scheduler Service 발행, self-reschedule 없음)
9. **News Crawler** (`services/news-crawler`)
   - **Trigger**: Scheduler Service (기본 10분). 컨테이너 부팅 시 Startup oneshot 메시지로 첫 Job을 즉시 실행.
   - **Role**: 네이버/구글 금융 뉴스 크롤링 -> **Gemini 2.5 Flash 실시간 감성 분석** -> DB/Redis/ChromaDB 저장
   - **Target**: **KOSPI 200 전체** (FinanceDataReader 기반, Watchlist 의존 제거)
   - **Scheduler Queue**: `real.jobs.news-crawler` (Scheduler Service 발행, self-reschedule 없음)

### 📱 Notification & Reporting
10. **Daily Briefing** (`services/daily-briefing`)
    - **Trigger**: Scheduler Service (매일 07:00)
    - **Role**: 일일 포트폴리오 현황, 자산 요약, 거래 내역을 텔레그램으로 전송
    - **Features**: 총 자산(AUM) 계산, 실현 손익 집계, 포트폴리오 구성 비율

## 🚀 핵심 기술 및 전략

### 1. ⚡ Fast Hands, Slow Brain Strategy (v3.0)
**"생각은 장 시작 전에 끝내고, 장 중에는 기계처럼 반응한다."**

- **Slow Brain (Pre-market Analysis)**: 
  - `scout-job`이 장 시작 전 LLM을 통해 유망 종목 분석
  - 매수 적합도 점수(0~100점) 및 등급(S/A/B/C/D) 산출
  
- **Fast Hands (Intraday Execution & Real-time Indicators)**:
  - **Real-time Sentiment**: 장중 발생하는 뉴스를 즉시 분석하여 감성 점수(0~100) 산출 및 Redis 캐싱
  - **Real-time Indicators**: `Buy Scanner`가 더 이상 과거 데이터에 의존하지 않고, **실시간 현재가**를 즉시 조회하여 RSI, 볼린저 밴드 등 모든 기술적 지표를 재계산.
  - **Dynamic Scoring**: 실시간으로 계산된 기술적 지표와 뉴스 감성 점수(호재 +10%, 악재 즉시 제외)를 결합하여 최종 매수 후보 선정.
  - **Execution Speed**: Redis 조회로 지연 시간 최소화 (0.1초 이내)

### 2. Real-time Telegram Notifications
- **Buy/Sell Alerts**: 매수/매도 체결 시 즉시 텔레그램 알림 전송
- **Daily Briefing**: 일일 자산 현황 및 거래 요약
- **Markdown Formatting**: 이모지 및 마크다운으로 가독성 높은 메시지 전송

### 3. KIS Gateway & Rate Limiting Strategy
- **Global Rate Limit**: Redis를 백엔드로 사용하여 모든 인스턴스 간 호출 횟수 공유.
- **Client-side Pacing**: `KISGatewayClient`에 **50ms 강제 딜레이**를 적용하여 클라이언트단에서 Burst Traffic 방지.
- **Circuit Breaker**: 장애 발생 시 즉시 차단하여 연쇄 오류 방지.

### 4. Real / Mock 프로파일 & BEAR 제어
- **환경 변수 통합**: `infrastructure/env-vars-real.yaml`, `env-vars-wsl.yaml`, `env-vars-mock.yaml`가 동일한 구조를 공유합니다. Real과 Mock은 단지 `TRADING_MODE`와 KIS Gateway/KIS Base URL만 다릅니다.
- **BEAR 장 LLM 필터**: `ALLOW_BEAR_TRADING`, `MIN_LLM_CONFIDENCE_BEAR`, `BEAR_POSITION_RATIO` 등 모든 하락장 제어 옵션을 env 파일에서 통일하여 관리합니다. Mock 스택도 동일한 조건으로 LLM 판단 결과가 "TRADABLE"인 종목만 제한적으로 매수합니다.
- **WSL2 부팅 자동화**: systemd user 서비스와 Windows Task Scheduler를 조합해 PC 부팅 시 WSL2 + Docker Compose가 즉시 기동되며, Real/Mock 스택을 동시에 올릴 수도 있습니다.

### 5. Database Flexibility (Oracle ↔ MariaDB)
- **Dual DB Support**: `DB_TYPE` 환경 변수로 Oracle/MariaDB 간 즉시 전환
- **Connection Abstraction**: `shared/db/connection.py`가 Oracle Wallet 및 MariaDB 연결 정보를 자동으로 처리
- **Repository Compatibility**: Oracle 전용 SQL 함수 제거, Python/SQLAlchemy 표준 API로 통일
- **Migration Tool**: `utilities/db_data_migrator.py`로 Oracle ↔ MariaDB 간 대량 데이터 마이그레이션 지원
- **Use Case**: 로컬 개발(MariaDB) + 운영(Oracle Cloud) 하이브리드 환경 지원

### 6. Scout v5.0 데이터 수집 유틸리티 (NEW!)
Scout v5.0 하이브리드 스코어링을 위한 데이터 수집 스크립트:

| 스크립트 | 역할 | 수집 대상 |
| --- | --- | --- |
| `scripts/collect_naver_news.py` | 네이버 금융 뉴스 수집 | KOSPI 200 종목별 2년치 뉴스 |
| `scripts/tag_news_sentiment.py` | 뉴스 감성/카테고리 태깅 | 규칙 기반 분류 (실적/수주/M&A 등) |
| `scripts/collect_quarterly_financials.py` | 분기별 재무 데이터 수집 | PER/PBR/ROE 시점 매칭용 |
| `scripts/collect_investor_trading.py` | 외국인/기관 매매 수집 | pykrx 기반 수급 데이터 |
| `scripts/collect_dart_filings.py` | DART 공시 수집 | OpenDartReader 기반 공시 |
| `scripts/run_factor_analysis.py` | 팩터 분석 실행 | FactorAnalyzer 오케스트레이션 |

```bash
# 전체 데이터 수집 (2년치)
DB_TYPE=MARIADB python scripts/collect_naver_news.py --codes 200 --days 711
DB_TYPE=MARIADB python scripts/tag_news_sentiment.py --days 750
DB_TYPE=MARIADB python scripts/collect_quarterly_financials.py --codes 200
DB_TYPE=MARIADB python scripts/collect_investor_trading.py --codes 200 --days 711

# 팩터 분석 실행
DB_TYPE=MARIADB python scripts/run_factor_analysis.py --codes 200
```

## 🛠️ 설치 및 실행 가이드

### 사전 요구사항
- Windows 11 + WSL2 (Ubuntu 22.04 이상) 또는 순수 Linux 환경
- Docker & Docker Compose
- Oracle Cloud Infrastructure (OCI) 계정 및 Wallet 파일
- `secrets.json`에 KIS/Gemini/Telegram 등 필수 시크릿 입력

### 환경 프로파일 선택
| 파일 | 용도 | 주요 차이점 |
| --- | --- | --- |
| `infrastructure/env-vars-real.yaml` | REAL 모드 (WSL 운영) | `TRADING_MODE=REAL`, `kis-gateway` 엔드포인트 |
| `infrastructure/env-vars-wsl.yaml`  | REAL 모드 (WSL 사용자별 커스터마이즈) | 기본값 동일, 개인 환경에 맞춰 수정 |
| `infrastructure/env-vars-mock.yaml` | MOCK 모드 (기능 테스트) | `TRADING_MODE=MOCK`, `kis-gateway-mock`, `kis-mock` API |

> 모든 파일이 동일한 하락장 제어 옵션과 RabbitMQ/Scheduler 설정을 공유하므로, 필요 시 한 곳만 수정하면 전체 스택에 반영됩니다.
> 모든 스케줄러블 서비스는 컨테이너 부팅 시 `startup_oneshot` 메시지를 1회 발행하고, 반복 주기는 Scheduler Service의 `jobs` 테이블에서만 관리합니다. 주기 변경/중지는 FastAPI + Telegram 명령으로 중앙에서 제어하세요.

#### 중앙 스케줄링 대상 서비스
| 서비스 | Scheduler Job | 기본 인터벌 | 비고 |
| --- | --- | --- | --- |
| Buy Scanner | `jobs.buy-scanner` | 300초 | Startup oneshot 이후 Scheduler Service가 주기적으로 큐에 발행 |
| News Crawler | `jobs.news-crawler` | Real 600초 / Mock 300초 | Watchlist 기반 뉴스 수집/감성 분석 |
| Scout Job | `jobs.scout` | 3600초 (1시간) | **KOSPI 200** 대상 3-Phase LLM 파이프라인 (Universe 200개) |
| Price Monitor | `jobs.price-monitor-start/stop` | Start 09:00 / Stop 15:30 (예시) | Startup oneshot으로 즉시 start Job 발행 |

#### RabbitMQ 스케줄 큐 초기화
- 중앙 스케줄러는 `jobs` 테이블만 보고 주기를 계산하므로, 큐를 비워도 다음 Tick에서 다시 메시지를 발행합니다. 재배포 후 과거 self-reschedule 메시지가 남아 있다면 아래 순서로 정리하세요.
  1. 워커 중단:
     ```bash
     docker compose stop buy-scanner news-crawler scout-job price-monitor
     ```
  2. 큐/딜레이 큐 비우기(Real 예시):
     ```bash
     docker compose exec rabbitmq rabbitmqctl purge_queue real.jobs.buy-scanner
     docker compose exec rabbitmq rabbitmqctl purge_queue real.jobs.buy-scanner.delay
     docker compose exec rabbitmq rabbitmqctl purge_queue real.jobs.news-crawler
     docker compose exec rabbitmq rabbitmqctl purge_queue real.jobs.news-crawler.delay
     docker compose exec rabbitmq rabbitmqctl purge_queue real.jobs.scout
     docker compose exec rabbitmq rabbitmqctl purge_queue real.jobs.scout.delay
     docker compose exec rabbitmq rabbitmqctl purge_queue real.jobs.price-monitor
     docker compose exec rabbitmq rabbitmqctl purge_queue real.jobs.price-monitor.delay
     ```
     > Mock 스택을 동시에 운용 중이면 `real.` 대신 `mock.` prefix로 동일하게 실행하세요.
  3. 워커 재시작:
     ```bash
     docker compose start buy-scanner news-crawler scout-job price-monitor
     ```

### 로컬 테스트 (Mock)
```bash
# 1. Mock 프로파일 전체 실행
docker compose --profile mock up -d --build

# 2. 뉴스 감성 분석 로직 검증
python3 scripts/verify_news_sentiment.py
```

### WSL2 + Docker Compose 배포 (Real)
1. `secrets.example.json`을 복사하여 `secrets.json`을 만들고 실제 키를 채웁니다.
2. `infrastructure/env-vars-wsl.yaml`(또는 `env-vars-real.yaml`)에서 투자 금액, BEAR 옵션, 게이트웨이 URL 등을 조정합니다.
3. Docker Compose 실행:
   ```bash
   docker compose --profile real up -d --build
   ```
4. 서비스 상태 확인:
   ```bash
   docker compose ps
   docker compose logs -f kis-gateway
   ```
5. Windows 재부팅 후에도 자동 기동하려면 user systemd 서비스를 확인합니다.
   ```bash
   systemctl --user status my-supreme-jennie.service
   # 필요 시 재시작
   systemctl --user restart my-supreme-jennie.service
   # 사용자 세션이 없을 때도 유지하려면 1회만 실행
   loginctl enable-linger youngs75
   ```

### RabbitMQ 매도 큐
- Compose에 기본 포함된 `rabbitmq` 서비스가 매도 이벤트 큐(`sell-orders`)를 담당합니다.
- 관리 콘솔: http://localhost:15672 (계정 `guest/guest`)
- `price-monitor`는 큐에 매도 요청을 게시하고, `sell-executor`는 동일 큐를 소비하여 자동으로 주문을 실행합니다. 로컬에서는 Cloud Tasks 대신 이 큐를 사용하므로 추가 GCP 자격증명이 필요 없습니다.

### 로컬 LLM (Gemini API Key)
- `news-crawler`, `rag-cacher`, `JennieBrain` 등 LLM/임베딩 의존 서비스는 이제 `GOOGLE_API_KEY` 기반으로 `gemini-2.5-pro` 및 `text-embedding-004`을 사용합니다.
- `secrets.json`의 `gemini-api-key` 값만 채워두면 Docker 컨테이너에서 자동으로 로드되고, 별도의 GCP ADC 설정이 필요 없습니다.
- 뉴스 감성 분석 속도는 `MAX_SENTIMENT_DOCS_PER_RUN`(기본 40)과 `SENTIMENT_COOLDOWN_SECONDS`(기본 0.2초)로 제어할 수 있습니다. 값은 `infrastructure/env-vars-*.yaml`에서 조정하세요.

### Streamlit Dashboard + Cloudflare Tunnel
- `docker compose up -d dashboard dashboard-tunnel` 실행 후 `http://localhost:8501`에서 Streamlit 지휘 통제실을 확인합니다.
- `dashboard-tunnel`은 Cloudflare Quick Tunnel을 사용해 임시 `https://*.trycloudflare.com` URL을 발급합니다. 주소가 필요하면 `docker compose logs dashboard-tunnel --tail=20`으로 확인하세요.
- 고정 도메인이 필요하면 Cloudflare 계정에서 Named Tunnel을 만들고, 발급받은 token을 `secrets.json`의 `cloudflare-tunnel-token` 키에 저장합니다(버전 관리 X). 컨테이너는 해당 값을 자동으로 읽어 `cloudflared tunnel run --token ...` 명령을 실행합니다.

### 로그 관측 (Loki + Grafana)
```bash
# Loki/Promtail/Grafana만 재시작
docker compose up -d loki promtail grafana

# Loki 상태 확인
curl http://localhost:3100/ready
```
- Grafana: http://localhost:3000 (기본 `admin / admin`) → Loki 데이터소스를 자동 인식하므로 Explore 탭에서 `{job="docker"}` 쿼리로 로그를 확인할 수 있습니다.

### 배포 (Real)
```bash
# 전체 서비스 배포
./scripts/deploy_all.sh
```

### Mock 스택 실행 (기능 검증용)
```bash
# Mock 전용 서비스 기동
docker compose --profile mock up -d --build

# Mock + Real 동시 실행 (필요 시)
docker compose --profile real --profile mock up -d
```
> Mock 스택도 `infrastructure/env-vars-mock.yaml`만 교체하면 Real과 동일한 로직·하락장 조건을 그대로 테스트할 수 있습니다. 차이는 `TRADING_MODE=MOCK`, `kis-gateway-mock`, `kis-mock` URL 뿐입니다.

### Scheduler Job 등록 예시
> Scheduler REST API는 Real `http://localhost:8095`, Mock `http://localhost:9095` 입니다.

**Real (scope=real)**
```bash
# Buy Scanner (5분)
curl -X POST http://localhost:8095/jobs -H "Content-Type: application/json" -d '{
  "job_id": "buy-scanner",
  "queue": "real.jobs.buy-scanner",
  "cron_expr": "*/5 * * * *",
  "reschedule_mode": "queue",
  "interval_seconds": 300,
  "enabled": true
}'

# News Crawler (10분)
curl -X POST http://localhost:8095/jobs -H "Content-Type: application/json" -d '{
  "job_id": "news-crawler",
  "queue": "real.jobs.news-crawler",
  "cron_expr": "*/10 * * * *",
  "reschedule_mode": "queue",
  "interval_seconds": 600,
  "enabled": true
}'

# Price Monitor Start / Stop
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

# Scout Job
curl -X POST http://localhost:8095/jobs -H "Content-Type: application/json" -d '{
  "job_id": "scout-daily",
  "queue": "real.jobs.scout",
  "cron_expr": "0 8 * * 1-5",
  "reschedule_mode": "queue",
  "interval_seconds": 86400,
  "enabled": true
}'
```

**Mock (scope=mock)**
```bash
curl -X POST http://localhost:9095/jobs -H "Content-Type: application/json" -d '{
  "job_id": "mock-buy-scanner",
  "queue": "mock.jobs.buy-scanner",
  "cron_expr": "*/5 * * * *",
  "reschedule_mode": "queue",
  "interval_seconds": 120,
  "enabled": true
}'

curl -X POST http://localhost:9095/jobs -H "Content-Type: application/json" -d '{
  "job_id": "mock-news",
  "queue": "mock.jobs.news-crawler",
  "cron_expr": "*/10 * * * *",
  "reschedule_mode": "queue",
  "interval_seconds": 600,
  "enabled": true
}'

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

curl -X POST http://localhost:9095/jobs -H "Content-Type: application/json" -d '{
  "job_id": "mock-scout",
  "queue": "mock.jobs.scout",
  "cron_expr": "0 9 * * 1-5",
  "reschedule_mode": "queue",
  "interval_seconds": 86400,
  "enabled": true
}'
```

## 📅 스케줄러 설정 (KST 기준)
- **Scout Job**: 평일 08:00
- **Price Monitor**: 평일 09:00 ~ 15:30
- **News Crawler**: 장중 10분 간격
- **Buy Scanner**: 장중 5분 간격 (뉴스 감성 반영)
- **Daily Briefing**: 평일 17:00
- Scheduler Service API에서 `reschedule_mode="queue"` + `interval_seconds`로 Job을 생성하면 RabbitMQ delay 큐(`SCHEDULER_QUEUE_*`)를 통해 자동 순환합니다.  
  - 기본 큐 이름: `real.jobs.buy-scanner`, `real.jobs.news-crawler` (Mock 모드는 `mock.*`)
  - 반복 주기/실행 시간 변경은 Scheduler Service API(또는 `jobs` 테이블)에서 직접 조정하며, env 파일에는 Queue 이름과 Worker 활성화 토글만 남깁니다.

## 📚 주요 문서
- **로드맵**: [Next Level Roadmap](docs/NEXT_LEVEL_ROADMAP.md) - 향후 발전 계획
- **전략**: `docs/FAST_HANDS_SLOW_BRAIN_STRATEGY.md`
- **구현**: `docs/FAST_HANDS_IMPLEMENTATION_LOG.md`
- **마이그레이션 스토리**: [GCP 탈출 및 WSL2 정착기](docs/GCP_TO_WSL_MIGRATION.md) (NEW!)
- **스케줄러 아키텍처**: [Scheduler Hybrid Architecture](docs/SCHEDULER_ARCHITECTURE.md)
  - Real Scheduler API: `http://localhost:8095`, Mock Scheduler API: `http://localhost:9095`

### 2025-12-05 (🚀 Scout v5.1 - Dual Track Strategy & Weekly Batch Job)
**Contributors**: Claude Opus 4.5

- **📊 Dual Track 투자 전략 도입**:
  - **Short-term Sniper**: D+5 기준 단기 매매 (RSI 과매도 + 외국인 순매수)
  - **Long-term Hunter**: D+60 기준 장기 보유 (ROE + 실적/배당 뉴스)
  - **뉴스 시간축 비밀**: 단기 악재(47.3%) → 장기 호재(52.8%) 역전 현상 발견!
  - **QuantScorer 분리**: `calculate_short_term_score()` / `calculate_long_term_score()` 독립 계산

- **🔧 섹터 모멘텀 버그 수정**:
  - **문제**: FinanceDataReader의 `Changes`(금액) vs `ChagesRatio`(비율) 혼동으로 14500% 같은 비정상 수익률 표시
  - **해결**: `ChagesRatio` 우선 사용 + 50% 초과 값 필터링
  - **결과**: 현실적인 섹터 수익률 표시 (자동차 2.73%, IT/전자 2.50%)

- **⏰ 주간 팩터 분석 배치 잡 추가**:
  - **스크립트**: `scripts/weekly_factor_analysis_batch.py`
  - **기능**: 6단계 자동 실행 (뉴스 수집 → 태깅 → DART → 수급 → 재무 → 분석)
  - **모드**: `--full-refresh` (2년치) / 기본 (7일 증분) / `--analysis-only`
  - **Cron 설정**: `0 6 * * 0` (매주 일요일 06:00)
  - **결과**: FACTOR_METADATA, FACTOR_PERFORMANCE, NEWS_FACTOR_STATS 테이블 자동 업데이트
  - **Scout-job 연동**: QuantScorer가 DB에서 최신 가중치 자동 로드

- **🗂️ 유틸리티 폴더 대청소**:
  - **정리 전**: scripts 17개, utilities 15개 (총 32개)
  - **정리 후**: scripts 7개, utilities 4개 (총 11개 핵심만!)
  - **이동**: 테스트/검증/일회성/부속 파일 21개 → `old_utilities/` 폴더로 이동
  - **README**: `old_utilities/README.md`에 이동 사유 및 복구 방법 기록

- **📦 핵심 유틸리티 구조 (2025-12-05 기준)**:
  ```
  scripts/                           # 데이터 수집 & 분석
  ├── collect_naver_news.py          # 네이버 뉴스 크롤링
  ├── tag_news_sentiment.py          # 뉴스 감성/카테고리 태깅
  ├── collect_dart_filings.py        # DART 공시 수집
  ├── collect_investor_trading.py    # 외국인/기관 수급
  ├── collect_quarterly_financials.py # 분기별 재무 (PER/PBR/ROE)
  ├── collect_full_market_data_parallel.py # 주가 데이터 (병렬)
  ├── run_factor_analysis.py         # 팩터 분석 실행
  └── weekly_factor_analysis_batch.py # 주간 배치 잡 (NEW!)
  
  utilities/                         # 핵심 기능
  ├── backtest.py                    # 메인 백테스트 엔진
  ├── backtest_gpt_v2.py             # GPT 버전 백테스트
  ├── update_stock_master.py         # STOCK_MASTER 관리
  └── naver_finance_scraper.py       # 네이버 금융 스크래퍼
  ```

### 2025-12-03 (🔧 MariaDB 완전 호환 & Scout Universe 확장)
**Contributors**: Claude Opus 4.5

- **🎯 Scout 1차 Universe 확장 (50개 → 200개)**:
  - **FinanceDataReader 통합**: 네이버 스크래핑 대신 `FinanceDataReader`로 KOSPI 시가총액 상위 200개 종목 조회
  - **환경변수 제어**: `SCOUT_UNIVERSE_SIZE`로 Universe 크기 동적 조절 가능
  - **Fallback 전략**: FinanceDataReader 실패 시 네이버 금융 스크래핑으로 자동 전환
  - **KOSPI 200 커버리지**: Kodex 200 ETF와 동일한 유니버스로 대형주 전체 커버

- **📊 데이터 수집 스크립트 MariaDB 호환**:
  - **collect_full_market_data_parallel.py**: Oracle `MERGE INTO` → MariaDB `INSERT ... ON DUPLICATE KEY UPDATE`
  - **collect_full_market_data.py**: 동일하게 MariaDB 호환 SQL로 변환
  - **backfill_watchlist_history.py**: Oracle 플레이스홀더 → MariaDB `%s` 플레이스홀더
  - **STOCK_DAILY_PRICES_3Y 테이블**: 958개 KOSPI 종목 3년치 일봉 데이터 수집 완료

- **🗄️ MariaDB 완전 호환성 확보**:
  - **CONFIG 테이블**: `CONFIG_VALUE` 컬럼을 `VARCHAR(255)` → `LONGTEXT`로 변경 (대용량 JSON 저장 지원)
  - **save_to_watchlist**: Oracle `:n` 플레이스홀더 → MariaDB `%s` 플레이스홀더 분기 처리
  - **save_all_daily_prices**: Oracle `MERGE INTO` → MariaDB `INSERT ... ON DUPLICATE KEY UPDATE` 변환
  - **update_all_stock_fundamentals**: Oracle 문법 → MariaDB 호환 SQL로 변환
  - **update_watchlist_financial_data**: Oracle `:name` 플레이스홀더 → MariaDB `%s` 플레이스홀더 분기 처리
  - **STOCK_DAILY_PRICES 테이블**: MariaDB에 누락된 테이블 생성

- **🐛 버그 수정**:
  - **Scheduler Job ID 불일치**: `scout-job` → `scout-daily`로 환경변수 추가 (`SCHEDULER_SCOUT_JOB_ID`)
  - **KOSPI 데이터 파싱 오류**: DataFrame 컬럼명 유연 처리 (`price`/`close_price`, `high`/`high_price` 등)
  - **pymysql 의존성**: 모든 트레이딩 서비스에 `pymysql>=1.0.0` 추가
  - **FinanceDataReader 의존성**: scout-job에 `FinanceDataReader` 추가

### 2025-12-02 (🚀 Scout-Debate-Judge Pipeline & DB Migration & Dashboard V2)
**Contributors**: GPT-5.1-Codex High, Gemini-3.0-Pro, Claude Sonnet 4.5, Claude Opus 4.5

- **🧠 Multi-Agent LLM Pipeline (Scout-Debate-Judge)**:
  - **Phase 1 - Hunter Scout**: `Claude Haiku 4.5`를 사용한 빠른 광역 필터링 (60점 컷오프)
  - **Phase 2 - Bull vs Bear Debate**: `GPT-5-mini`를 사용한 심층 토론 및 리스크 평가
  - **Phase 3 - Judge**: `GPT-5-mini`를 사용한 최종 의사결정 및 등급 부여 (기본 50점, S/A/B/C/D)
  - **LLM Provider Abstraction**: `BaseLLMProvider` 인터페이스 도입으로 Claude/Gemini/OpenAI 간 쉬운 전환 지원
  - **Tiered Stock Selection**: 최종 점수 기반으로 종목을 계층화하여 신뢰도 높은 후보 우선 매매

- **💰 LLM 비용 최적화 (99% 절감)**:
  - **Caching Strategy**: LLM 결과 캐싱으로 동일 입력에 대한 중복 호출 방지 (TTL: 24시간)
  - **Cooldown Guard**: 최소 호출 간격 설정으로 과도한 API 요청 차단 (60분)
  - **Candidate Diff-based Calls**: 재무/가격 데이터 변경 시에만 LLM 호출
  - **하이브리드 모델 전략**: Claude Haiku 4.5 (빠르고 정확) + GPT-5-mini (고품질 reasoning) + Gemini-Flash (뉴스 감성)
  - **Scout-Job 주기 조정**: 10분 → 1시간으로 변경
  - **예상 비용**: $50-80/day → $0.52/day (약 **99% 절감**)

- **🗄️ Oracle → MariaDB 완전 전환**:
  - **Primary DB**: MariaDB (WSL2 Local) - 모든 서비스가 MariaDB 사용
  - **Backup DB**: Oracle Cloud (ATP) - 백업 및 마이그레이션 소스
  - **DB Type Selector**: `DB_TYPE` 환경 변수로 Oracle/MariaDB 자동 선택
  - **Connection Abstraction**: `shared/db/connection.py`에서 Oracle Wallet 및 MariaDB 연결 통합 관리
  - **Repository Refactoring**: `shared/db/repository.py`에서 Oracle 전용 함수(`func.trunc`, `func.systimestamp` 등) 제거, Python `datetime` 및 SQLAlchemy 표준 함수로 대체
  - **Timezone-aware Datetime**: 모든 `datetime.now()` → `datetime.now(timezone.utc)` 변경으로 naive/aware datetime 비교 오류 해결
  - **Data Migration**: Oracle Cloud DB → 로컬 MariaDB 완전 마이그레이션 (919,697행)
  - **Schema Sync**: `Portfolio`, `WatchList` 등 누락된 컬럼 추가 및 모델 정합성 확보
  - **Scheduler Service**: SQLite → MariaDB 전환 완료

- **🔧 Scheduler Service 버그 수정**:
  - **Critical Bug**: `last_run_at` 미업데이트로 인한 Job 중복 발행 (5초마다 반복)
  - **Fix**: `run_scheduler_cycle()`에서 메시지 발행 후 `job.last_run_at = now` 추가
  - **Impact**: news-crawler, scout-job 등 모든 스케줄링 Job이 설정된 주기대로 정확히 실행

- **🎨 Dashboard V2 (React + FastAPI) - 완전 새로 개발** *(by Claude Opus 4.5)*:
  - **Frontend**: React 18 + TypeScript + Vite + Tailwind CSS + Framer Motion
  - **Backend**: FastAPI + JWT 인증 (7일 유효) + WebSocket
  - **인증 개선**: JWT LocalStorage 저장으로 **F5 새로고침해도 로그인 유지!** (기존 Streamlit 문제 해결)
  - **Scout Pipeline 시각화**: 3-Phase (Hunter → Debate → Judge) 실시간 애니메이션
  - **System Status**: WSL2 + Docker 컨테이너 상태 모니터링 + RabbitMQ 큐 + Scheduler Jobs
  - **🆕 실시간 로그 뷰어**: Docker 컨테이너 클릭 시 Loki에서 실시간 로그 조회 (5초 자동 갱신)
  - **🆕 실시간 현재가 연동**: KIS Gateway API를 통한 포트폴리오 실시간 평가 (v3.8)
  - **🆕 TradingView 차트**: Lightweight Charts 라이브러리 연동 (캔들스틱 + 볼륨)
  - **디자인**: 다크 테마 + 글래스모피즘 + Glow 효과 (Jennie Pink/Purple/Blue)
  - **접속**: https://dashboard.yj-ai-lab.com (Cloudflare Tunnel)

- **📊 Observability 강화**:
  - Grafana + Loki를 통한 Phase별 LLM 호출 로그 추적
  - RabbitMQ 큐 상태 모니터링으로 메시지 적체 감지
  - Scheduler 메시지 발행 이력 로그로 중복 발행 여부 검증
  - **🆕 Dashboard에서 직접 컨테이너 로그 조회** (Loki API 연동)

### 2025-12-03 (🗄️ Scout v4.3 - LLM Cache 테이블 도입) *(by Claude Opus 4.5)*

- **🗄️ LLM_EVAL_CACHE 전용 테이블 도입 (v4.3)**:
  - **기존 문제**: Config 테이블에 JSON으로 캐시 저장 → 비효율적, 쿼리 불가, TTL 관리 어려움
  - **신규 테이블**: `LLM_EVAL_CACHE` (종목별 LLM 평가 결과 전용)
    ```sql
    STOCK_CODE, STOCK_NAME, EVAL_DATE, PRICE_BUCKET, VOLUME_BUCKET, NEWS_HASH,
    HUNTER_SCORE, JUDGE_SCORE, LLM_GRADE, LLM_REASON, IS_APPROVED, IS_TRADABLE
    ```
  - **해시 로직 제거 → 직접 비교**: 더 빠르고 명확한 캐시 무효화
    - 날짜 비교: `EVAL_DATE != 오늘` → 재평가
    - 가격 비교: `PRICE_BUCKET` 5% 버킷 변동 → 재평가
    - 뉴스 비교: `NEWS_HASH` 변경 → 재평가
  - **효과**: 캐시 히트 시 LLM 비용 0원, 미스 원인 정확히 파악 가능

- **📰 뉴스 해시 반영**: 
  - ChromaDB 뉴스 내용의 MD5 해시를 `candidate_stocks`에 추가
  - **뉴스 바뀌면 자동으로 LLM 재호출** (시장 호재/악재 즉시 반영)
  - 해시에 타임스탬프 포함 → 같은 뉴스라도 시간 다르면 재평가

- **💰 Claude Tier 2 업그레이드**:
  - **Tier 1**: 50 RPM, 40,000 TPM → 429 에러 빈발
  - **Tier 2**: 1,000 RPM, 80,000 TPM → **429 에러 0건!**
  - 4개 워커 병렬 처리로 200개 종목 Phase 1 완료: ~4분

### 2025-12-03 (🧠 Scout v4.2 - Slow Brain Optimization) *(by Claude Opus 4.5)*

- **⚡ Scout 성능 대폭 최적화 (v4.2)**:
  - **사전 조회 (Prefetch)**: Phase 1 시작 전 KIS 스냅샷/ChromaDB 뉴스 일괄 조회
    - 병렬 스레드 안 API 호출 제거 → Rate Limit 회피 + 속도 향상
    - KIS 스냅샷: 200개 종목 8개 워커 병렬 조회 (~80초)
    - ChromaDB 뉴스: 200개 종목 8개 워커 병렬 조회 (~17초)
  - **Phase 1 LLM만 실행**: 캐시에서 데이터 조회, Claude API만 호출
  - **Phase 2 Top 50 제한**: 상위 50개만 Debate-Judge 진입 (속도 50% 단축)
  - **총 실행 시간**: 15분+ → **~2분** (캐시 히트 시)

- **🔄 해시 기반 캐시 무효화 개선 (v4.1)**:
  - **해시에 포함되는 데이터**:
    - 오늘 날짜 (YYYY-MM-DD) → 매일 자동 재평가 보장
    - 가격 버킷 (5% 단위) → 가격 변동 시 재평가
    - 거래량 버킷 (10만주 단위) → 급등락 감지
  - **효과**: 시장 상황 변화 시 자동으로 LLM 재호출

- **📦 UPSERT 로직 도입 (TRUNCATE 제거)**:
  - **이전**: 매번 WatchList TRUNCATE → INSERT (종목 손실)
  - **이후**: UPSERT (신규 INSERT, 기존 UPDATE) + 24시간 TTL
  - **효과**: 1시간마다 실행해도 이전 종목 유지, 누적 관리

- **🧠 Scout 3-Phase LLM 파이프라인 최적화 (v4.0)**:
  - **Phase 1 (Hunter)**: Gemini-Flash → **Claude Haiku 4.5**로 전환 (빠르고 정확한 프롬프트 이행)
  - **Phase 2 (Debate)**: GPT-5-mini Bull vs Bear 심층 토론 (공격적 캐릭터 설정)
  - **Phase 3 (Judge)**: GPT-5-mini 최종 판결 (**기본 50점 기준 명시**, 균형 잡힌 평가)
  - **통과 기준 튜닝**: Phase 1 60점, Judge 50점으로 최적화
  - **쿼터제**: 최종 Top 15개만 Watchlist 등록

- **📰 News Integration 개선**:
  - **rag-cacher 서비스 제거**: 불필요한 중간 레이어 삭제
  - **Scout → ChromaDB 직접 조회**: 실시간 뉴스 검색 및 LLM 프롬프트 전달
  - **News Crawler 대상 확장**: Watchlist 의존 → **KOSPI 200 전체** 뉴스 수집

- **🔗 RabbitMQ 연결 안정성 강화**:
  - **StreamLostError 복구**: `_safe_ack`/`_safe_nack` 구현으로 연결 끊김 시 graceful 처리
  - **ACK-before-Process**: 장시간 작업 시 연결 끊김 방지
  - **Heartbeat 설정**: `heartbeat=60`, `blocked_connection_timeout=300`
  - **Exponential Backoff**: 재연결 시 지수 백오프 적용 (1초 → 최대 30초)

- **💰 실제 매수 성공 (첫 실전 거래!)**:
  - **DB하이텍 (000990)**: 127주 매수, A등급 (78점)
  - **기아 (000270)**: 80주 매수, A등급 (車 관세 인하 호재 반영)
  - Scout-Debate-Judge 파이프라인 정상 작동 검증 완료!

- **🎨 Dashboard V2 UI 개선**:
  - Scout Pipeline 페이지에 **"Slow Brain"** 철학 반영
  - Phase 1 LLM: "Gemini-2.5-Flash" → **"Claude Haiku 4.5"** 표시
  - Phase 2-3 LLM: "GPT-4o-mini" → **"GPT-5-mini"** 표시
  - "Slow Brain 🧠 → Fast Hand ⚡" 시각화 추가

- **📊 섹터/테마 분석 기능 추가**:
  - FinanceDataReader 기반 KOSPI 섹터별 모멘텀 분석
  - 핫 섹터(반도체, 자동차, 배터리 등) 종목 우선 후보 등록
  - 섹터별 평균 수익률 실시간 계산

- **🛡️ Docker 헬스체크 및 자동 복구**:
  - 모든 Trading 서비스에 healthcheck 설정 추가
  - `restart: unless-stopped` 정책으로 장애 시 자동 복구
  - 30초 간격 헬스체크, 3회 실패 시 재시작

- **📈 WATCHLIST_HISTORY 백필**:
  - 2년치(730일) 과거 데이터 시뮬레이션 생성
  - 기술적 지표(MA120, RSI, 모멘텀) 기반 우량주 선정 로직
  - MariaDB 호환 SQL 문법 적용

- **💹 Dashboard V2 실시간 현재가**:
  - KIS Gateway `/api/market-data/snapshot` API 연동
  - 포트폴리오 실시간 평가금액/수익률 계산
  - TradingView Lightweight Charts 캔들스틱 차트 추가

### 2025-11-24 (Real-time Enhancement)
- **⚡️ 실시간 감시 도입 (WebSocket)**:
  - `price-monitor`가 HTTP Polling 방식에서 **WebSocket** 방식으로 전환.
  - 보유 종목의 가격 변동을 지연 없이 실시간으로 감지하여 매도 신호의 정확성과 반응 속도 극대화.
  - 안정적인 연결 유지를 위해 `price-monitor`는 `min-instances=1` (Always-on)으로 설정.
  
- **🎯 실시간 지표 계산 (Fast Hands 강화)**:
  - `buy-scanner`가 스캔 시점에 **실시간 현재가**를 조회하여 모든 기술적 지표(RSI, 이평선 등)를 즉시 재계산.
  - 장중 가격 변동을 정확히 반영하여 매수 신호의 신뢰도 향상.

### 2025-11-23 (Stability & Sentiment EMA)
- **🛡️ KIS Gateway 안정화**:
  - **Single-Threaded Execution**: Gunicorn `workers=1`, `threads=1` 설정으로 토큰 충돌 및 500 에러 원천 차단
  - **Token Lock 문제 해결**: 동시 인증 요청으로 인한 파일 락 타임아웃 제거
  - **검증 완료**: Scout Job 실행 시 KIS Gateway 에러 0건 달성
  
- **📰 뉴스 감성 점수 EMA 적용**:
  - **문제**: 동일 종목에 대한 여러 뉴스가 덮어쓰기되어 최신 점수만 반영
  - **해결**: 지수 이동 평균(EMA) 적용 - 기존 70% + 신규 30% 가중치로 스무딩
  - **효과**: 급격한 점수 변동 완화 및 전체 뉴스 흐름 반영
  - **검증**: 단위 테스트(Mock Redis)로 EMA 계산 로직 검증 완료
  
- **📖 디버깅 가이드 추가**:
  - `DEBUGGING_GUIDE.md` 생성 - Cloud Shell 환경에서 사용 가능한 디버깅 절차 문서화
  - KIS Gateway, Scout Job, News Crawler, Executors 문제 해결 방법 포함  

### 2025-11-20 (Initial Release)
- **실시간 뉴스 감성 분석 (Real-time Sentiment Analysis)**:
  - `News Crawler`가 수집한 뉴스를 `Gemini 2.5 Flash`로 즉시 분석하여 0~100점 점수화
  - Redis에 실시간 캐싱 및 Oracle DB에 영구 저장
  - `Buy Scanner`가 매수 시점에 감성 점수를 조회하여 **호재(+10% 가산점)** 및 **악재(즉시 필터링)** 반영

- **인프라 개선**:
  - Redis 통합: 모든 서비스(`news-crawler`, `buy-scanner` 등)에 Redis 의존성 추가 및 연동
  - Google Generative AI: Vertex AI 대신 Google AI Studio API(`google-generativeai`) 활용으로 최신 모델 접근성 확보

- **성능 최적화**:
  - Gemini 2.5 Flash 도입: 뉴스 분석 속도 및 비용 효율성 최적화 (Fallback: 1.5 Flash)
  - 중복 분석 방지: 뉴스 URL 기반 중복 체크로 API 호출 비용 절감

## 📜 라이선스
Private Project - Unauthorized copying is strictly prohibited.  
