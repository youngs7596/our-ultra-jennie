# ⚡ Fast Hands, Slow Brain: 하이브리드 퀀트 전략

## 1. 개요 (Overview)
**"Fast Hands, Slow Brain"**은 LLM의 '지능(Intelligence)'과 알고리즘의 '속도(Speed)'를 결합한 하이브리드 트레이딩 아키텍처입니다.

기존 시스템의 가장 큰 병목이었던 **"매수 시점의 LLM 동기 호출(3~5초 지연)"**을 제거하고, **사전 결재(Pre-approval)** 방식을 도입하여 **민첩성(Agility)**을 극대화하는 것이 목표입니다.

---

## 2. 핵심 철학 (Core Philosophy)

> **"생각은 장 시작 전에 끝내고, 장 중에는 기계처럼 반응한다."**

*   **Slow Brain (참모총장/LLM)**: 시간이 걸리더라도 깊이 있는 분석(뉴스, 펀더멘털, 모멘텀)을 수행하여 전략을 수립합니다. (주로 장 시작 전/후)
*   **Fast Hands (저격수/Algorithm)**: 사전에 승인된 명령에 따라, 조건이 충족되는 즉시 방아쇠를 당깁니다. (장중 0.1초 내 실행)

---

## 3. 아키텍처 변경 (Architecture Changes)

### 🔄 Before (기존 방식)
1.  **Signal**: `buy-scanner`가 기술적 신호 포착
2.  **Blocking**: `buy-executor`가 LLM에게 "살까요?" 문의 **(⏳ 3~5초 대기)**
3.  **Decision**: LLM이 뉴스/재무 분석 후 응답
4.  **Execution**: 주문 전송 (이미 호가 급등, 슬리피지 발생)

### ⚡ After (신규 방식 - v1.0)
1.  **Pre-Analysis (Slow Brain)**:
    *   장 시작 전(`scout-job`), 멀티 LLM이 유망 종목을 분석
    *   **Scout Pipeline v1.0**:
        - Phase 1: QuantScorer (정량 분석, LLM 미사용)
        - Phase 2: Claude Hunter (펀더멘털 + 뉴스 RAG 분석, 60점 이상 통과)
        - Phase 3: Claude Debate (낙관론자/비관론자 AI 토론)
        - Phase 4: OpenAI Judge (최종 승인, 75점 이상)
    *   결과를 **매수 적합도 점수(0~100)**와 **등급(S/A/B)**으로 DB에 미리 저장
2.  **Signal & Check (Fast Hands)**:
    *   `buy-scanner`가 신호 포착.
    *   `buy-executor`는 DB에서 `LLM_SCORE`만 조회 **(⚡ 0.01초)**.
3.  **Execution**:
    *   점수가 `MIN_LLM_SCORE` 이상이면 **즉시 주문 전송**.
    *   `MIN_LLM_SCORE`: 환경변수로 설정 (REAL: 70, MOCK: 50)
4.  **Post-Analysis**:
    *   매수 체결 후, LLM이 비동기로 "매수 사유 및 전략" 리포트 생성.

---

## 4. 구현 상세 (Implementation Details)

### 4.1 DB 스키마 확장 (`WatchList` 테이블)
LLM의 사전 분석 결과를 저장하기 위해 컬럼을 추가했습니다.
*   `LLM_SCORE` (NUMBER): 매수 적합도 점수 (0~100)
*   `LLM_REASON` (VARCHAR2): 분석 근거 및 전략 코멘트
*   `LLM_UPDATED_AT` (TIMESTAMP): 분석 시점

### 4.2 `scout-job` 로직 (Scout Pipeline v1.0)
*   **Phase 1**: `QuantScorer` (정량 분석)
    *   모멘텀, RSI, ROE, 수급 등 정량 지표 분석
    *   LLM 비용 $0 (정량 분석만)
*   **Phase 2**: `Claude Hunter` (펀더멘털 + 뉴스 RAG)
    *   경쟁사 수혜 가산점 반영
    *   60점 이상 통과
*   **Phase 3**: `Claude Debate` (AI 토론)
    *   낙관론자 vs 비관론자 토론
*   **Phase 4**: `OpenAI Judge` (최종 승인)
    *   75점 이상 최종 Watchlist 등록
    *   `is_tradable=True` 설정

### 4.3 `buy-executor` 로직 (v1.0)
*   **현재 상태** (`services/buy-executor/executor.py`)
    *   LLM 동기 호출이 완전히 제거되었습니다.
    *   `candidates` 리스트를 `llm_score` 기준으로 정렬한 뒤 최고점 후보만 선택합니다.
    *   점수 < `MIN_LLM_SCORE`(환경변수)인 경우 즉시 Skip 하여 보수적으로 동작합니다.
    *   `risk_setting.position_size_ratio`를 적용해 최종 수량을 조정하고, Smart Skip(50% 미만) 규칙으로 자투리 체결을 방지합니다.
    *   체결 결과에는 적용된 `risk_setting`을 함께 저장하여 사후 분석과 회귀 테스트에 활용합니다.

### 4.4 환경변수 설정
| 환경변수 | REAL 모드 | MOCK 모드 | 설명 |
|----------|-----------|-----------|------|
| `MIN_LLM_SCORE` | 70 | 50 | 매수 최소 기준 점수 |
| `TRADING_MODE` | REAL | MOCK | 트레이딩 모드 |
| `DRY_RUN` | false | true | 실제 주문 여부 |

---

## 5. 기대 효과 (Benefits)

1.  **속도 혁신**: 주문 실행 지연 시간 99% 단축 (5초 → 0.05초).
2.  **슬리피지 최소화**: 급등주 포착 시 시장가로 빠르게 진입하여 목표 수익률 확보.
3.  **비용 절감**: 장중 빈번한 LLM 호출을 줄이고, 배치 처리로 토큰 효율성 증대.
4.  **안정성**: 외부 API(Claude/OpenAI) 장애가 발생해도 트레이딩 시스템은 멈추지 않음 (DB 기반 동작).
5.  **멀티 LLM 검증**: Claude + OpenAI 이중 검증으로 판단 신뢰도 향상.

---

## 6. 완료된 과제 (Completed)

- [x] `buy-scanner`를 **Scheduler 기반**으로 전환하여 스캔 공백 제거.
- [x] `MIN_LLM_SCORE` 환경변수화 (REAL/MOCK 모드별 설정 가능).
- [x] Scout Pipeline 멀티 LLM 구조 (Claude Hunter → Claude Debate → OpenAI Judge).
- [x] 텔레그램 알림에 MOCK 모드/DRY RUN 표시 추가.

---

## 7. 향후 과제 (Next Steps)

- [ ] `buy-executor`에서 Gateway를 거치지 않는 **Direct KIS API** 호출로 네트워크 레이턴시 최소화.
- [ ] 실시간 시장 국면 모니터링 자동화.
- [ ] 전략별 성과 대시보드 구축.

---

*작성: Ultra Jennie v1.0 (2025-12-05)*
