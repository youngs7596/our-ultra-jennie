# Scout v5.0 하이브리드 스코어링 시스템

## 📋 개요

Scout v5.0은 기존 LLM 기반 스코어링의 한계를 극복하기 위해 **정량 분석(Quant)**과 **정성 분석(LLM)**을 결합한 하이브리드 스코어링 시스템입니다.

### 기존 문제점 (v4.0 이전)
- LLM의 "감(Intuition)"에만 의존
- 일관성 부족 (같은 종목도 컨디션에 따라 다른 점수)
- 설명 불가능한 의사결정
- 비용 비효율 (모든 종목에 LLM 호출)

### 해결 방향
> **"감(LLM)을 믿기 전에, 통계(Data)로 검증하고, 비용(Cost)을 통제한다."**

---

## 🏗️ 시스템 아키텍처

### 2-Layer 구조

```
┌─────────────────────────────────────────────────────────────┐
│                   OFFLINE ANALYSIS LAYER                     │
│  (주간/일간 배치)                                              │
├─────────────────────────────────────────────────────────────┤
│  FactorAnalyzer                                              │
│  ├── 팩터 예측력 분석 (IC/IR 계산)                             │
│  ├── 뉴스 카테고리별 영향도 분석                               │
│  ├── 복합 조건 분석 (뉴스+수급)                                │
│  └── 공시 영향도 분석 (DART)                                  │
└─────────────────────────────────────────────────────────────┘
                              ↓
                    FACTOR_METADATA (DB)
                    NEWS_FACTOR_STATS (DB)
                              ↓
┌─────────────────────────────────────────────────────────────┐
│                   ONLINE SCORING LAYER                       │
│  (실시간)                                                     │
├─────────────────────────────────────────────────────────────┤
│  QuantScorer                                                 │
│  ├── 정량 점수 계산 (100점 만점)                               │
│  ├── 1차 필터링 (하위 50% 탈락)                               │
│  └── LLM용 컨텍스트 생성                                      │
│                              ↓                               │
│  LLM 정성 분석 (상위 후보만)                                   │
│  ├── 뉴스/공시 맥락 해석                                      │
│  └── 치명적 리스크 체크                                       │
│                              ↓                               │
│  HybridScorer                                                │
│  ├── 정량 + 정성 점수 결합                                    │
│  ├── Safety Lock (점수 차이 30점↑ 시 보수적 처리)              │
│  └── 상위 15개 종목 선정                                      │
└─────────────────────────────────────────────────────────────┘
```

---

## 📊 팩터 분석 결과 (2025-12-05 기준)

### 팩터 예측력 분석

| 팩터 | IC | IR | 적중률 | 표본 수 | 평가 |
|------|-----|-----|--------|---------|------|
| **RSI 과매도** | +0.029 | 0.13 | **54.6%** | 90,420 | ⭐⭐⭐ 최고 |
| **ROE (수익성)** | +0.024 | 0.00 | **54.5%** | 11,088 | ⭐⭐⭐ 우수 |
| **PER (저평가)** | -0.039 | 0.00 | **52.1%** | 10,495 | ⭐⭐ 효과 있음 |
| 외국인 순매수 | 0.000 | 0.00 | 50.8% | 93,003 | ❌ 데이터 문제 |
| 6개월 모멘텀 | -0.005 | -0.02 | 49.4% | 69,363 | ❌ 역방향 |
| 1개월 모멘텀 | -0.040 | -0.18 | 49.6% | 89,063 | ❌ 역방향 |
| PBR (저평가) | -0.065 | 0.00 | 48.6% | 11,088 | ❌ 효과 없음 |

### 🔥 핵심 인사이트
1. **RSI 과매도 + ROE가 유일하게 50% 넘는 전략**
2. **모멘텀 추종은 한국 시장에서 역효과** (상승 종목 추격매수 ❌)
3. **54.6% 승률의 복리 효과** - 카지노(51~52%)보다 좋은 확률

---

### 뉴스 카테고리별 영향도 (충격적인 결과!)

| 카테고리 | 승률 | 평균수익률 | 표본 | 해석 |
|----------|------|-----------|------|------|
| 실적 | 48.4% | +0.33% | 1,445 | 🤔 동전보다 못함 |
| M&A | 48.3% | +0.36% | 29 | ⚠️ 표본 부족 |
| 신사업 | 46.9% | +0.02% | 563 | ❌ 효과 없음 |
| 수주 | **43.7%** | +0.06% | 588 | ❌ **역신호!** |
| 배당 | **37.6%** | **-0.36%** | 117 | ⚠️ **강한 역신호!** |
| 전체 | 47.3% | +0.25% | 4,135 | ❌ 동전보다 못함 |

### 🔥 "Sell the News" 증명
> **"뉴스 보고 매수하면 고점에 물린다"**

- 수주 뉴스 승률 43.7% → 반대로 하면 56.3%
- 배당 뉴스 승률 37.6% → 반대로 하면 62.4%
- 뉴스가 나올 때는 **이미 가격에 반영**되어 있음

---

## 🗄️ 데이터베이스 스키마

### 신규 테이블

```sql
-- 팩터 메타데이터 (분석 결과)
CREATE TABLE FACTOR_METADATA (
    FACTOR_KEY VARCHAR(50) PRIMARY KEY,
    FACTOR_NAME VARCHAR(100),
    MARKET_REGIME VARCHAR(20),
    IC_MEAN DECIMAL(10,6),
    IC_STD DECIMAL(10,6),
    IR DECIMAL(10,6),
    HIT_RATE DECIMAL(10,6),
    RECOMMENDED_WEIGHT DECIMAL(10,6),
    SAMPLE_COUNT INT,
    ANALYSIS_START_DATE DATE,
    ANALYSIS_END_DATE DATE
);

-- 뉴스 팩터 통계
CREATE TABLE NEWS_FACTOR_STATS (
    NEWS_CATEGORY VARCHAR(50),
    STOCK_GROUP VARCHAR(50),
    MARKET_REGIME VARCHAR(20),
    WIN_RATE DECIMAL(10,6),
    AVG_RETURN DECIMAL(10,6),
    SAMPLE_COUNT INT,
    CONFIDENCE_LEVEL VARCHAR(10),
    HOLDING_DAYS INT,
    RECENT_WIN_RATE DECIMAL(10,6),
    RECENT_SAMPLE_COUNT INT,
    RECENCY_WEIGHT DECIMAL(10,6),
    UPDATED_DATE DATE,
    PRIMARY KEY (NEWS_CATEGORY, STOCK_GROUP, MARKET_REGIME, HOLDING_DAYS)
);

-- 분기별 재무 지표 (PER/PBR/ROE 시점 매칭용)
CREATE TABLE FINANCIAL_METRICS_QUARTERLY (
    STOCK_CODE VARCHAR(20),
    QUARTER_DATE DATE,
    QUARTER_NAME VARCHAR(20),
    EPS DECIMAL(15,2),
    BPS DECIMAL(15,2),
    PER DECIMAL(10,2),
    PBR DECIMAL(10,2),
    ROE DECIMAL(10,4),
    CLOSE_PRICE DECIMAL(15,2),
    PRIMARY KEY (STOCK_CODE, QUARTER_DATE)
);
```

---

## 🔧 주요 모듈

### 1. FactorAnalyzer (`shared/hybrid_scoring/factor_analyzer.py`)
- **역할**: 오프라인 배치 분석
- **기능**:
  - 팩터별 IC/IR 계산
  - 뉴스 카테고리별 D+5 승률/수익률 분석
  - 복합 조건 분석 (뉴스+수급)
  - 공시 영향도 분석

### 2. QuantScorer (`shared/hybrid_scoring/quant_scorer.py`)
- **역할**: 실시간 정량 점수 계산
- **배점** (100점 만점):
  - 모멘텀: 25점 → **5점** (역방향으로 감소)
  - 품질(ROE): 20점 → **25점** (효과 있음)
  - 가치(PER): 15점
  - 기술적(RSI): 10점 → **25점** (최고 효과)
  - 뉴스: 15점 → **10점** (역신호 반영)
  - 수급: 15점 → **10점**

### 3. HybridScorer (`shared/hybrid_scoring/hybrid_scorer.py`)
- **역할**: 정량+정성 점수 결합
- **Safety Lock**: 정량/정성 차이 30점↑ 시 낮은 쪽 우선
- **시장 국면별 가중치** 자동 조정

---

## 📁 유틸리티 스크립트

### 데이터 수집
```bash
# 네이버 뉴스 수집 (KOSPI 200, 2년치)
python scripts/collect_naver_news.py --codes 200 --days 711

# 뉴스 감성/카테고리 태깅
python scripts/tag_news_sentiment.py --days 750

# 분기별 재무 데이터 수집
python scripts/collect_quarterly_financials.py --codes 200

# 외국인/기관 매매 데이터 수집
python scripts/collect_investor_trading.py --codes 200 --days 711

# DART 공시 수집
python scripts/collect_dart_filings.py --codes 200 --days 711
```

### 팩터 분석
```bash
# FactorAnalyzer 전체 분석 실행
DB_TYPE=MARIADB python scripts/run_factor_analysis.py --codes 200
```

---

## 📈 전략 수정 권장사항

### 가중치 조정 (Phase 1)

| 팩터 | 기존 | 변경 | 근거 |
|------|------|------|------|
| 모멘텀 | 25점 | **5점** | IC 음수, 역방향 |
| RSI 과매도 | 10점 | **25점** | 적중률 54.6% (최고) |
| ROE | 20점 | **25점** | 적중률 54.5% |
| 뉴스 | 15점 | **10점** | 승률 47.3% (역신호) |

### 뉴스 점수 로직 수정

```python
# 기존: 뉴스 호재 = 가산점
if news_category in ['수주', '배당']:
    # 승률 50% 미만 → 가산점 제거 또는 패널티
    news_bonus = 0  # 또는 -5점

# LLM 프롬프트에 메타 지시 추가
"⚠️ 주의: 수주/배당 뉴스는 통계상 역신호입니다. 추격매수 금지."
```

---

## 🔮 향후 개발 계획

### Phase 2 (단기) ✅ 완료
- [x] 복합 조건 분석 디버깅 (날짜 매칭 수정)
- [x] 공시 영향도 결과 출력 추가
- [x] LLM 프롬프트에 뉴스 역신호 경고 추가

### Phase 3 (중기) ✅ 완료
- [x] 분석 조건 세분화 (대형/소형, 시장 상승/하락)
- [x] 백테스트 시뮬레이션 함수 추가
- [x] 시장 국면 자동 감지 (`detect_market_regime`)

### Phase 4 (장기)
- [ ] 실시간 시장 국면 모니터링
- [ ] 자동 가중치 업데이트 스케줄러
- [ ] 전략별 성과 대시보드

---

## 📚 참고 문서

- [Fast Hands, Slow Brain 전략](./FAST_HANDS_SLOW_BRAIN_STRATEGY.md)
- [로컬 테스트 가이드](./LOCAL_FAST_HANDS_TEST_GUIDE.md)
- [Mock vs Real 모드 분석](./MOCK_VS_REAL_MODE_ANALYSIS.md)

---

## 🏷️ 버전 히스토리

| 버전 | 날짜 | 주요 변경 |
|------|------|----------|
| v5.0.0 | 2025-12-05 | 하이브리드 스코어링 시스템 초기 구현 |
| v5.0.1 | 2025-12-05 | 분기별 PER/PBR/ROE 시점 매칭 구현 |
| v5.0.2 | 2025-12-05 | 뉴스 카테고리별 영향도 분석 추가 |
| v5.0.3 | 2025-12-05 | Oracle/MariaDB 호환성 개선 |
| v5.0.4 | 2025-12-05 | NaN 처리 및 날짜 매칭 버그 수정 |
| v5.0.5 | 2025-12-05 | **팩터 분석 결과 반영**: 가중치 조정(RSI↑, ROE↑, 모멘텀↓), 뉴스 역신호 처리, 시장 국면 감지, 백테스트 |

---

## 📊 v5.0.5 핵심 변경사항

### 가중치 조정 (팩터 분석 결과 기반)
```
모멘텀:  25% → 5%  (역방향, IC 음수)
RSI:     7% → 20% (54.6% 승률, 최고)
ROE:    12% → 20% (54.5% 승률)
뉴스:   15% → 7%  (47.3% 역신호)
PBR:     8% → 3%  (48.6% 무효)
```

### 뉴스 역신호 처리
- **역신호 카테고리**: 수주(43.7%), 배당(37.6%)
- 해당 카테고리 뉴스 발생 시 패널티 적용
- LLM 프롬프트에 "추격매수 금지" 경고 추가

### 신규 기능
- `detect_market_regime()`: KOSPI 6개월 수익률 기반 시장 국면 감지
- `classify_stock_group()`: 시가총액 기준 대형/중형/소형 분류
- `run_backtest()`: 간단한 전략 시뮬레이션

---

*작성: Claude Opus 4.5 (2025-12-05)*

