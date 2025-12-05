# 🤖 Ultra Jennie - AI 기반 자율 트레이딩 에이전트

<div align="center">

![Version](https://img.shields.io/badge/version-5.1.1-blue)
![Python](https://img.shields.io/badge/python-3.11-green)
![Docker](https://img.shields.io/badge/docker-compose-2496ED)
![License](https://img.shields.io/badge/license-MIT-yellow)

**멀티 LLM 기반 한국 주식 자율 트레이딩 시스템**

*"감(LLM)을 믿기 전에, 통계(Data)로 검증하고, 비용(Cost)을 통제한다."*

</div>

---

## 📋 목차

- [개요](#-개요)
- [핵심 기능](#-핵심-기능)
- [시스템 아키텍처](#-시스템-아키텍처)
- [서비스 구성](#-서비스-구성)
- [기술 스택](#-기술-스택)
- [빠른 시작](#-빠른-시작)
- [프로젝트 구조](#-프로젝트-구조)
- [주요 모듈](#-주요-모듈)
- [데이터베이스 스키마](#-데이터베이스-스키마)
- [API 문서](#-api-문서)
- [설정](#-설정)

---

## 🎯 개요

**Ultra Jennie**는 한국투자증권 API를 활용한 AI 기반 자율 트레이딩 에이전트입니다. 멀티 LLM(Gemini, Claude, OpenAI)을 활용하여 투자 판단을 내리고, 하이브리드 스코어링 시스템으로 정량적/정성적 분석을 결합합니다.

### 주요 특징

| 기능 | 설명 |
|------|------|
| 🧠 **멀티 LLM 판단** | Gemini(Scout), Claude(Hunter), OpenAI(Judge) 3단계 LLM 심사 |
| 📊 **하이브리드 스코어링** | 정량 팩터(60%) + LLM 정성 분석(40%) 결합 |
| 🎯 **경쟁사 수혜 분석** | 경쟁사 악재 발생 시 반사이익 자동 포착 |
| 📰 **실시간 뉴스 분석** | RAG 기반 뉴스 감성 분석 및 카테고리 분류 |
| ⚖️ **페어 트레이딩** | 롱/숏 페어 신호 자동 생성 |
| 📈 **백테스트** | 디커플링 전략 통계 검증 |

---

## 🚀 핵심 기능

### 1. Scout Pipeline (종목 발굴)

```
KOSPI 200 Universe
       ↓
[Phase 1] Quant Scoring (정량 분석)
   - 모멘텀, 가치, 수급, 기술적 지표
   - 비용: $0 (LLM 미사용)
       ↓
[Phase 2] Hunter Analysis (Claude)
   - 기본점수 + 경쟁사 수혜 가산
   - 통과 기준: 60점 이상
       ↓
[Phase 3] Debate (Bull vs Bear)
   - 낙관론자/비관론자 토론
       ↓
[Phase 4] Judge Decision (OpenAI)
   - 최종 승인 기준: 75점 이상
       ↓
Watchlist (상위 15개)
```

### 2. 경쟁사 수혜 분석 시스템

```python
# 쿠팡 개인정보 유출 시나리오
from shared.hybrid_scoring import CompetitorAnalyzer

analyzer = CompetitorAnalyzer()
report = analyzer.analyze('035420')  # NAVER

# 결과
# - 섹터: 이커머스
# - 경쟁사 이벤트: 보안사고
# - 수혜 점수: +10점
# - 디커플링 승률: 62%
# - 추천: 매수 검토
```

### 3. 뉴스 카테고리 자동 분류

| 카테고리 | 키워드 | 심각도 | 경쟁사 수혜 |
|----------|--------|--------|-------------|
| 보안사고 | 해킹, 유출, 개인정보 | -15점 | +10점 |
| 서비스장애 | 장애, 먹통, 접속불가 | -10점 | +8점 |
| 리콜 | 리콜, 결함, 불량 | -12점 | +7점 |
| 오너리스크 | 구속, 기소, 횡령 | -12점 | +3점 |
| 규제 | 과징금, 제재, 공정위 | -8점 | +5점 |

### 4. 페어 트레이딩 전략

```python
from shared.strategies import PairTradingStrategy

strategy = PairTradingStrategy()
signal = strategy.generate_pair_signal({
    'affected_code': 'CPNG',
    'affected_company': '쿠팡',
    'event_type': '보안사고',
    'severity': -15
})

# 결과
# 롱: NAVER (035420)
# 숏: 쿠팡 (CPNG)
# 디커플링 승률: 62%
# 예상 스프레드: +10.3%
```

---

## 🏗 시스템 아키텍처

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          Ultra Jennie System                            │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌───────────────┐    ┌───────────────┐    ┌───────────────┐           │
│  │  News Crawler │───▶│   ChromaDB    │◀───│  Scout Job    │           │
│  │   (v9.1)      │    │   (RAG)       │    │   (v5.1)      │           │
│  └───────────────┘    └───────────────┘    └───────────────┘           │
│         │                                          │                    │
│         ▼                                          ▼                    │
│  ┌───────────────┐    ┌───────────────┐    ┌───────────────┐           │
│  │    Redis      │◀───│  KIS Gateway  │───▶│  Buy Scanner  │           │
│  │   (Cache)     │    │   (v3.0)      │    │   (v3.5)      │           │
│  └───────────────┘    └───────────────┘    └───────────────┘           │
│         │                    │                     │                    │
│         ▼                    ▼                     ▼                    │
│  ┌───────────────┐    ┌───────────────┐    ┌───────────────┐           │
│  │   MariaDB     │◀───│ Price Monitor │───▶│ Buy Executor  │           │
│  │  (Persistent) │    │   (Realtime)  │    │               │           │
│  └───────────────┘    └───────────────┘    └───────────────┘           │
│                              │                     │                    │
│                              ▼                     ▼                    │
│                       ┌───────────────┐    ┌───────────────┐           │
│                       │ Sell Executor │◀───│   RabbitMQ    │           │
│                       │               │    │   (Message)   │           │
│                       └───────────────┘    └───────────────┘           │
│                                                                         │
├─────────────────────────────────────────────────────────────────────────┤
│  Dashboard V2 (React + FastAPI) │ Grafana      │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 📦 서비스 구성

| 서비스 | 포트 | 설명 |
|--------|------|------|
| **kis-gateway** | 8080 | 한국투자증권 API 게이트웨이 |
| **scout-job** | 8087 | AI 기반 종목 발굴 파이프라인 |
| **buy-scanner** | 8081 | 매수 신호 스캔 |
| **buy-executor** | 8082 | 매수 주문 실행 |
| **sell-executor** | 8083 | 매도 주문 실행 |
| **price-monitor** | 8088 | 실시간 가격 모니터링 |
| **news-crawler** | 8089 | 뉴스 수집 및 경쟁사 수혜 분석 |
| **daily-briefing** | 8086 | 일간 브리핑 생성 |
| **scheduler-service** | 8095 | 작업 스케줄링 |
| **dashboard-v2** | 80, 8090 | React + FastAPI 대시보드 |

### 인프라 서비스

| 서비스 | 포트 | 설명 |
|--------|------|------|
| **chromadb** | 8000 | 벡터 DB (RAG) |
| **redis** | 6379 | 캐시 및 실시간 데이터 |
| **rabbitmq** | 5672, 15672 | 메시지 큐 |
| **grafana** | 3000 | 모니터링 대시보드 |
| **loki** | 3100 | 로그 집계 |

---

## 🛠 기술 스택

### Backend
- **Python 3.11** - 핵심 언어
- **Flask / FastAPI** - REST API
- **SQLAlchemy** - ORM
- **Gunicorn / Uvicorn** - WSGI/ASGI 서버

### AI / ML
- **Google Gemini** - 1차 스크리닝 (Scout)
- **Anthropic Claude** - 2차 심층 분석 (Hunter)
- **OpenAI GPT** - 최종 판단 (Judge)
- **LangChain** - LLM 오케스트레이션
- **ChromaDB** - 벡터 저장소 (RAG)

### Data
- **MariaDB** - 영구 저장소
- **Redis** - 캐시 및 실시간 상태
- **FinanceDataReader** - 주가 데이터
- **Pandas / NumPy** - 데이터 분석

### Infrastructure
- **Docker Compose** - 컨테이너 오케스트레이션
- **RabbitMQ** - 메시지 큐
- **Cloudflare Tunnel** - 외부 접근
- **Grafana / Loki** - 모니터링

### Frontend
- **React + TypeScript** - Dashboard V2
- **Vite** - 빌드 도구

---

## 🚀 빠른 시작

### 사전 요구사항

- Docker & Docker Compose
- MariaDB (WSL2 mirrored mode 또는 별도 서버)
- Python 3.11+

### 1. 환경 설정

```bash
# 저장소 클론
git clone https://github.com/yourusername/my-ultra-jennie.git
cd my-ultra-jennie

# 시크릿 파일 생성
cp secrets.example.json secrets.json
# secrets.json 편집하여 API 키 입력
```

### 2. secrets.json 설정

```json
{
  "KIS_API_KEY": "your-kis-api-key",
  "KIS_API_SECRET": "your-kis-api-secret",
  "KIS_ACCOUNT": "your-account-number",
  "GOOGLE_API_KEY": "your-gemini-api-key",
  "ANTHROPIC_API_KEY": "your-claude-api-key",
  "OPENAI_API_KEY": "your-openai-api-key"
}
```

### 3. 서비스 실행

```bash
# Real 모드 (실제 거래)
docker compose --profile real up -d

# Mock 모드 (시뮬레이션)
docker compose --profile mock up -d

# 서비스 상태 확인
docker compose ps
```

### 4. 초기 데이터 설정

```bash
# 경쟁사 수혜 분석 테이블 및 데이터 초기화
docker compose run --rm scout-job python scripts/init_competitor_data.py
```

---

## 📁 프로젝트 구조

```
my-ultra-jennie/
├── services/                    # 마이크로서비스
│   ├── scout-job/              # AI 종목 발굴
│   ├── buy-scanner/            # 매수 신호 스캔
│   ├── buy-executor/           # 매수 실행
│   ├── sell-executor/          # 매도 실행
│   ├── price-monitor/          # 가격 모니터링
│   ├── news-crawler/           # 뉴스 수집
│   ├── daily-briefing/         # 일간 브리핑
│   ├── kis-gateway/            # KIS API 게이트웨이
│   ├── scheduler-service/      # 스케줄러
│   └── dashboard-v2/           # React 대시보드
│       ├── backend/            # FastAPI
│       └── frontend/           # React + TypeScript
│
├── shared/                      # 공유 모듈
│   ├── llm.py                  # LLM 오케스트레이션 (JennieBrain)
│   ├── database.py             # 데이터베이스 유틸리티
│   ├── news_classifier.py      # 뉴스 카테고리 분류
│   ├── market_regime.py        # 시장 국면 분석
│   ├── db/                     # SQLAlchemy 모델
│   │   ├── models.py           # ORM 모델 정의
│   │   └── connection.py       # DB 연결 관리
│   ├── hybrid_scoring/         # 하이브리드 스코어링
│   │   ├── quant_scorer.py     # 정량 점수
│   │   ├── hybrid_scorer.py    # 하이브리드 점수
│   │   ├── factor_analyzer.py  # 팩터 분석
│   │   └── competitor_analyzer.py  # 경쟁사 수혜 분석
│   ├── strategies/             # 트레이딩 전략
│   │   ├── pair_trading.py     # 페어 트레이딩
│   │   └── competitor_backtest.py  # 백테스트
│   └── kis/                    # 한국투자증권 API
│       ├── client.py           # KIS 클라이언트
│       └── gateway_client.py   # 게이트웨이 클라이언트
│
├── prompts/                     # LLM 프롬프트
│   └── competitor_benefit_prompt.py  # 경쟁사 수혜 프롬프트
│
├── infrastructure/             # 인프라 설정
│   ├── env-vars-wsl.yaml       # WSL2 환경변수
│   └── env-vars-mock.yaml      # Mock 환경변수
│
├── observability/              # 모니터링
│   ├── grafana/                # Grafana 설정
│   ├── loki/                   # Loki 설정
│   └── promtail/               # Promtail 설정
│
├── scripts/                    # 유틸리티 스크립트
│   ├── init_competitor_data.py # 경쟁사 데이터 초기화
│   └── run_factor_analysis.py  # 팩터 분석 실행
│
├── docker-compose.yml          # Docker Compose 설정
└── secrets.json                # API 키 (gitignore)
```

---

## 📚 주요 모듈

### JennieBrain (shared/llm.py)

LLM 기반 의사결정 엔진. 멀티 프로바이더(Gemini, Claude, OpenAI)를 지원합니다.

```python
from shared.llm import JennieBrain

brain = JennieBrain()

# 종목 분석
result = brain.get_jennies_analysis_score_v5(decision_info, quant_context)
# Returns: {'score': 75, 'grade': 'B', 'reason': '...'}

# 뉴스 감성 분석
sentiment = brain.analyze_news_sentiment(title, summary)
# Returns: {'score': 30, 'reason': '악재로 판단'}

# Debate 세션
debate_log = brain.run_debate_session(decision_info)

# Judge 최종 판단
judge_result = brain.run_judge_scoring(decision_info, debate_log)
```

### CompetitorAnalyzer (shared/hybrid_scoring/competitor_analyzer.py)

경쟁사 수혜 분석 모듈.

```python
from shared.hybrid_scoring import CompetitorAnalyzer

analyzer = CompetitorAnalyzer()

# 종목 분석
report = analyzer.analyze('035420')
print(f"수혜 기회: {report.has_opportunity}")
print(f"수혜 점수: +{report.total_benefit_score}")

# 섹터별 경쟁사 조회
competitors = analyzer.get_competitors_by_sector('ECOM')

# 디커플링 통계 조회
stats = analyzer.get_decoupling_stats('ECOM')
```

### NewsClassifier (shared/news_classifier.py)

뉴스 카테고리 자동 분류.

```python
from shared.news_classifier import get_classifier

classifier = get_classifier()
result = classifier.classify("쿠팡 3370만명 개인정보 유출")

print(result.category)           # '보안사고'
print(result.sentiment)          # 'NEGATIVE'
print(result.base_score)         # -15
print(result.competitor_benefit) # +10
```

### PairTradingStrategy (shared/strategies/pair_trading.py)

페어 트레이딩 전략 생성.

```python
from shared.strategies import PairTradingStrategy

strategy = PairTradingStrategy()
signal = strategy.generate_pair_signal({
    'affected_code': 'CPNG',
    'affected_company': '쿠팡',
    'event_type': '보안사고',
    'severity': -15
})

if signal:
    print(strategy.format_signal_for_display(signal))
```

---

## 🗃 데이터베이스 스키마

### 핵심 테이블

| 테이블 | 설명 |
|--------|------|
| `WATCHLIST` | 관심 종목 목록 + LLM 점수 |
| `PORTFOLIO` | 보유 포트폴리오 |
| `TRADELOG` | 거래 이력 |
| `NEWS_SENTIMENT` | 뉴스 감성 분석 결과 |
| `STOCK_DAILY_PRICES_3Y` | 3년 일봉 데이터 |

### 경쟁사 수혜 분석 테이블

| 테이블 | 설명 |
|--------|------|
| `INDUSTRY_COMPETITORS` | 산업/경쟁사 매핑 (7개 섹터, 15개 종목) |
| `EVENT_IMPACT_RULES` | 이벤트 영향 규칙 (5개 유형) |
| `SECTOR_RELATION_STATS` | 섹터 디커플링 통계 |
| `COMPETITOR_BENEFIT_EVENTS` | 실시간 수혜 이벤트 기록 |

### 하이브리드 스코어링 테이블

| 테이블 | 설명 |
|--------|------|
| `FACTOR_STATS` | 팩터별 IC/IR 통계 |
| `CONDITION_PERFORMANCE` | 복합 조건 성과 |
| `NEWS_FACTOR_STATS` | 뉴스 카테고리별 성과 |

---

## 📡 API 문서

### KIS Gateway (8080)

```
GET  /health              # 헬스 체크
GET  /api/token           # 토큰 발급
POST /api/order/buy       # 매수 주문
POST /api/order/sell      # 매도 주문
GET  /api/stock/{code}    # 종목 정보 조회
GET  /api/balance         # 잔고 조회
```

### Scout Job (8087)

```
GET  /health              # 헬스 체크
POST /run                 # Scout 파이프라인 실행
GET  /status              # 파이프라인 상태
```

### Dashboard V2 Backend (8090)

```
GET  /health              # 헬스 체크
GET  /api/watchlist       # Watchlist 조회
GET  /api/portfolio       # 포트폴리오 조회
GET  /api/trades          # 거래 내역
GET  /api/pipeline/status # 파이프라인 상태
POST /api/commands        # 에이전트 명령
```

---

## ⚙️ 설정

### 환경변수 (infrastructure/env-vars-wsl.yaml)

```yaml
# 데이터베이스
DB_TYPE: MARIADB
MARIADB_HOST: 127.0.0.1
MARIADB_PORT: 3306
MARIADB_USER: root
MARIADB_PASSWORD: your-password
MARIADB_DBNAME: jennie_db

# Redis
REDIS_URL: redis://127.0.0.1:6379/0

# ChromaDB
CHROMA_SERVER_HOST: 127.0.0.1

# 거래 모드
TRADING_MODE: REAL  # or MOCK

# API Keys (secrets.json에서 로드)
SECRETS_FILE: /app/config/secrets.json
```

### Docker Compose 프로파일

```bash
# Real 모드 - 실제 거래
docker compose --profile real up -d

# Mock 모드 - 시뮬레이션
docker compose --profile mock up -d
```

### Mock 모드 설정

Mock 모드는 실제 거래 없이 전체 파이프라인을 테스트할 수 있는 환경입니다.

| 설정 | Real 모드 | Mock 모드 | 설명 |
|------|-----------|-----------|------|
| `TRADING_MODE` | REAL | MOCK | 거래 모드 |
| `DRY_RUN` | false | true | 실제 주문 실행 여부 |
| `MIN_LLM_SCORE` | 70 | 50 | 매수 최소 점수 기준 |
| `KIS_BASE_URL` | 실서버 | Mock 서버 | KIS API 엔드포인트 |

#### Mock 모드 특징

- 🧪 **[MOCK 테스트]** 표시가 텔레그램 알림에 추가
- ⚠️ **[DRY RUN]** 표시로 실제 주문이 아님을 명시
- 💰 LLM 토큰 절약 (토론 생성 건너뜀)
- 📊 기존 캐시된 LLM 점수 활용

#### Mock 모드 테스트 방법

```bash
# Mock 스택 실행
docker compose --profile mock up -d

# Buy Scanner 수동 트리거
docker exec buy-scanner-mock python3 -c "
import pika, json
conn = pika.BlockingConnection(pika.URLParameters('amqp://guest:guest@localhost:5672/'))
ch = conn.channel()
ch.queue_declare(queue='mock.jobs.buy-scanner', durable=True)
ch.basic_publish(exchange='', routing_key='mock.jobs.buy-scanner', 
    body=json.dumps({'trigger': 'manual_test'}),
    properties=pika.BasicProperties(delivery_mode=2))
conn.close()
"

# 로그 확인
docker logs buy-executor-mock --since 2m
```

---

## 📊 모니터링

### Grafana 대시보드

- URL: http://localhost:3000
- 기본 계정: admin / admin

### 로그 조회 (Loki)

```bash
# 특정 서비스 로그
docker compose logs scout-job --tail 50

# Grafana에서 Loki 쿼리
{container_name="scout-job"} |= "ERROR"
```

---

## 🔒 보안 고려사항

- `secrets.json`은 절대 커밋하지 않음 (`.gitignore` 포함)
- API 키는 환경변수 또는 Secret Manager 사용
- 실제 거래 모드에서는 충분한 테스트 후 운영

---

## 📝 변경 이력

### v5.1.1 (2025-12-05)

**Mock 모드 개선**
- ✅ `MIN_LLM_SCORE` 환경변수 분리 (Real: 70점, Mock: 50점)
- ✅ 텔레그램 알림에 Mock/DRY RUN 표시 추가
  - 🧪 **[MOCK 테스트]** - Mock 모드일 때 표시
  - ⚠️ **[DRY RUN - 실제 주문 없음]** - DRY_RUN 모드일 때 표시
- ✅ Mock 모드 매수/매도 전체 파이프라인 테스트 검증 완료

**문서 개선**
- Mock 모드 설정 및 테스트 방법 문서화

### v5.1.0 (2025-12-04)

- 경쟁사 수혜 분석 시스템 추가
- 하이브리드 스코어링 (정량 60% + LLM 40%)
- 페어 트레이딩 전략
- GCP → WSL2 Docker Compose 마이그레이션

---

## 📝 라이선스

MIT License

---

## 🤝 기여

이 프로젝트는 Claude, Gemini, GPT 등 여러 AI 모델의 협업으로 개발되었습니다.

---

<div align="center">

**Ultra Jennie** - *AI가 발굴하고, 통계가 검증하고, 사람이 결정한다.*

</div>
