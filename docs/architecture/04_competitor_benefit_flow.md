# Ultra Jennie - Competitor Benefit Flow (Mermaid)

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

