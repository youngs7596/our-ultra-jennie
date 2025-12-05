# Ultra Jennie Architecture Diagrams (Mermaid)

## 1. 전체 아키텍처
```mermaid
flowchart LR
  subgraph Infra[인프라]
    RDB[(MariaDB)]
    Redis[(Redis)]
    Chroma[(ChromaDB)]
    MQ[(RabbitMQ)]
    Grafana[Grafana/Loki]
  end

  subgraph Gateways
    KISGW[KIS Gateway]
    KISMock[KIS Mock]
  end

  subgraph DataIngest
    NewsCrawler[News Crawler]
    DailyBrief[Daily Briefing]
    PriceMonitor[Price Monitor]
  end

  subgraph AI_Core
    Scout[Scout Job v5.1]
    Hybrid[Hybrid Scoring]
    LLM[JennieBrain]
    Classifier[NewsClassifier]
    CompAnalyzer[Competitor Analyzer]
    PairStrat[Pair Trading Strat]
    Backtester[Competitor Backtester]
  end

  subgraph Exec
    BuyScan[Buy Scanner]
    BuyExec[Buy Executor]
    SellExec[Sell Executor]
  end

  subgraph Dash
    DashV2[Dashboard_V2_React_FastAPI]
  end

  NewsCrawler --> RDB
  NewsCrawler --> Redis
  NewsCrawler --> Chroma

  PriceMonitor --> MQ
  DailyBrief --> RDB

  Scout -->|Read/Write| RDB
  Scout -->|cache| Redis
  Scout --> Chroma
  Scout --> LLM
  Scout --> CompAnalyzer
  Scout --> Hybrid

  CompAnalyzer --> RDB
  CompAnalyzer --> Redis

  Hybrid --> RDB
  PairStrat --> RDB
  Backtester --> RDB

  BuyScan --> MQ
  MQ --> BuyExec
  MQ --> SellExec
  BuyExec --> RDB
  SellExec --> RDB

  DashV2 --> RDB
  DashV2 --> Redis

  KISGW -.-> KISMock
  BuyExec --> KISGW
  SellExec --> KISGW
  PriceMonitor --> KISGW
```

## 2. 매수/매도 의사결정 흐름
```mermaid
sequenceDiagram
  participant PM as Price Monitor
  participant MQ as RabbitMQ
  participant BS as Buy Scanner
  participant SJ as Scout Job (LLM/Hybrid)
  participant BE as Buy Executor
  participant SE as Sell Executor
  participant DB as MariaDB
  participant RD as Redis
  participant LLM as JennieBrain

  PM->>MQ: 실시간 이벤트/신호 발행
  MQ->>BS: 매수 스캔 요청
  BS->>DB: 시황/팩터 조회
  BS->>SJ: 후보 종목 전달(Phase1/2)
  SJ->>LLM: Hunter/Judge 분석 + 뉴스/RAG
  SJ->>RD: 경쟁사 수혜 점수 조회/가산
  SJ->>DB: Watchlist 업데이트(상위 N)
  SJ->>MQ: 매수 후보/명령 퍼블리시
  MQ->>BE: 매수 실행 명령
  BE->>KISGW: 주문 전송
  BE->>DB: TradeLog 기록

  PM->>MQ: 손절/익절 조건 이벤트
  MQ->>SE: 매도 실행 요청
  SE->>KISGW: 매도 주문
  SE->>DB: TradeLog 기록
```

## 3. MSA 주요 모듈 연계
```mermaid
flowchart TB
  NewsCrawler --> ChromaDB
  NewsCrawler --> Redis
  NewsCrawler --> RDB[(MariaDB)]

  ScoutJob --> ChromaDB
  ScoutJob --> Redis
  ScoutJob --> RDB
  ScoutJob --> MQ[(RabbitMQ)]

  BuyScanner --> RDB
  BuyScanner --> MQ

  PriceMonitor --> MQ
  DailyBriefing --> RDB

  MQ --> BuyExecutor
  MQ --> SellExecutor

  BuyExecutor --> KISGateway
  SellExecutor --> KISGateway
  KISGateway -->|실거래| KIS API

  DashboardV1 --> RDB
  DashboardV1 --> Redis
  DashboardV2 --> RDB
  DashboardV2 --> Redis

  subgraph Scoring/AI
    HybridScoring
    CompetitorAnalyzer
    PairTrading
    Backtester
    NewsClassifier
    JennieBrain
  end

  ScoutJob --- HybridScoring
  ScoutJob --- CompetitorAnalyzer
  ScoutJob --- NewsClassifier
  NewsCrawler --- NewsClassifier
  ScoutJob --- JennieBrain
  CompetitorAnalyzer --- Redis
  PairTrading --- RDB
  Backtester --- RDB
```

## 4. Competitor Benefit Flow
```mermaid
flowchart LR
  News[뉴스 수집] --> Classify[뉴스 분류/악재 식별]
  Classify -->|NEGATIVE + 수혜 카테고리| CompMap[섹터/경쟁사 매핑]
  CompMap --> Analyzer[Competitor Analyzer]
  Analyzer --> RedisCache[Redis: competitor_benefit:*]
  Analyzer --> EventsDB[COMPETITOR_BENEFIT_EVENTS 테이블]
  RedisCache --> Scout[Scout Job 보너스 점수]
  EventsDB --> PairStrat[Pair Trading 전략]
```

## 5. LLM Decision Chain (Hunter/Judge)
```mermaid
flowchart LR
  Quant[Quant Score\n(팩터/시장)] --> Hunter[Hunter (Claude)]
  NewsCtx[RAG 뉴스 컨텍스트] --> Hunter
  CompBenefit[경쟁사 수혜 점수] --> Hunter
  Hunter --> Debate[Debate (Bull vs Bear)]
  Debate --> Judge[Judge (OpenAI)]
  Judge --> Decision[최종 승인/거부 + 수량]
  Decision --> Watchlist[Watchlist 업데이트]
```

## 6. Scheduler/Job Flow (RabbitMQ Delay 패턴)
```mermaid
flowchart LR
  subgraph Scheduler
    Jobs[Jobs 메타데이터]
    Publish[트리거 메시지 발행]
  end
  subgraph MQ[RabbitMQ]
    Queue[Job Queue]
    DLX[Delay/TTL DLX]
  end
  Worker[서비스 Worker] -->|완료 후 next_delay| Publish
  Publish --> Queue
  Queue --> Worker
  Queue --> DLX --> Queue
  Jobs --> Publish
```

