# 🛠️ My Supreme Jennie - Cloud Shell 디버깅 가이드

이 문서는 **Google Cloud Shell** 및 **Gemini Code Assistant** 환경에서 "My Supreme Jennie" 프로젝트의 문제를 진단하고 해결하기 위한 절차를 담고 있습니다. 제한된 컴퓨팅 리소스와 도구 환경을 고려하여, **로그 분석**과 **수동 트리거**를 중심으로 한 실용적인 디버깅 방법을 제시합니다.

---

## 📋 일반적인 디버깅 워크플로우

1.  **증상 확인**: 무엇이 안 되는지 파악 (예: 매수가 안 됨, 데이터가 안 쌓임).
2.  **로그 확인**: `gcloud logging` 명령어로 최근 에러 로그 조회.
3.  **수동 트리거**: `curl` 명령어로 해당 서비스만 단독 실행하여 재현.
4.  **코드 확인**: 문제가 의심되는 파일의 핵심 로직 검토.

---

## 1. 🚦 KIS Gateway (API 게이트웨이)

KIS Gateway는 모든 주식 데이터 요청의 관문입니다. 여기가 막히면 모든 것이 멈춥니다.

### 🚨 주요 증상 및 해결
*   **500 Internal Server Error**:
    *   **원인**: KIS API 호출 제한 초과, 동시 요청 폭주, 또는 KIS 서버 점검.
    *   **확인**: `services/kis-gateway/Dockerfile`에서 `workers=1`, `threads=1` 설정 확인 (동시성 제어).
    *   **해결**: 잠시 대기 후 재시도. 지속되면 KIS API 서버 상태 확인.
*   **Token File Lock Timeout**:
    *   **원인**: 여러 프로세스가 동시에 토큰 갱신을 시도함.
    *   **해결**: 위와 동일하게 `workers=1` 설정으로 해결됨.

### 🔍 로그 확인 명령어
```bash
# 최근 20줄 로그 확인 (에러 포함)
gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="kis-gateway"' --limit=20 --format="value(textPayload)" --project=$GCP_PROJECT_ID
```

### ⚡ 수동 헬스 체크
```bash
# 헬스 체크 엔드포인트 호출
curl -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  https://kis-gateway-641885523217.asia-northeast3.run.app/health
```

---

## 2. 🔭 Scout Job (시장 분석 및 종목 발굴)

Scout Job은 장 시작 전/중간에 시장 상황을 판단하고 Watchlist를 갱신합니다.

### 🚨 주요 증상 및 해결
*   **"KOSPI 데이터 부족" / "SIDEWAYS" 고정**:
    *   **원인**: DB에 KOSPI 일봉 데이터가 없거나 KIS Gateway 연결 실패.
    *   **해결**: `shared/market_regime.py` 로직 확인. KIS Gateway가 정상인지 먼저 확인.
*   **Snapshot 조회 실패**:
    *   **원인**: KIS Gateway 500 에러 또는 종목 코드 오류.
    *   **해결**: KIS Gateway 로그를 교차 검증.

### 🔍 로그 확인 명령어
```bash
# "Error"가 포함된 로그만 필터링
gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="scout-job" AND severity>=ERROR' --limit=20 --format="value(textPayload)" --project=$GCP_PROJECT_ID
```

### ⚡ 수동 트리거
```bash
# Scout Job 강제 실행
curl -X POST -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  https://scout-job-641885523217.asia-northeast3.run.app/scout
```

---

## 3. 📰 News Crawler (뉴스 수집 및 감성 분석)

뉴스를 수집하고 Gemini로 감성 분석을 수행한 뒤 Redis에 저장합니다.

### 🚨 주요 증상 및 해결
*   **감성 점수가 갱신되지 않음**:
    *   **원인**: Redis 연결 실패 또는 뉴스 소스(RSS) 없음.
    *   **확인**: `shared/database.py`의 `set_sentiment_score` 함수 로직 확인 (EMA 적용 여부).
    *   **로그**: `[DB] 뉴스 감성 저장 완료` 로그가 있는지 확인.
*   **Gemini API 오류**:
    *   **원인**: Quota 초과 또는 API Key 설정 오류.

### 🔍 로그 확인 명령어
```bash
gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="news-crawler"' --limit=20 --format="value(textPayload)" --project=$GCP_PROJECT_ID
```

### ⚡ 수동 트리거
```bash
curl -X POST -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  https://news-crawler-641885523217.asia-northeast3.run.app/crawl
```

---

## 4. 💰 Buy/Sell Executor (매매 실행)

실제 주문을 넣는 서비스입니다. Pub/Sub 메시지를 받아 동작합니다.

### 🚨 주요 증상 및 해결
*   **주문이 안 나감**:
    *   **원인**: Pub/Sub 메시지 수신 실패, 보유 현금 부족, 또는 이미 보유 중인 종목.
    *   **확인**: `services/buy-executor/executor.py`의 `_check_account_balance` 로직 확인.
*   **중복 주문**:
    *   **원인**: 멱등성(Idempotency) 체크 실패.
    *   **해결**: `shared/database.py`의 `check_duplicate_order` 함수가 정상 동작하는지 확인.

### 🔍 로그 확인 명령어
```bash
# Buy Executor 로그
gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="buy-executor"' --limit=20 --format="value(textPayload)" --project=$GCP_PROJECT_ID

# Sell Executor 로그
gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="sell-executor"' --limit=20 --format="value(textPayload)" --project=$GCP_PROJECT_ID
```

---

## 🧰 유용한 명령어 치트시트

### 환경 변수 설정 (필수)
```bash
export GCP_PROJECT_ID=gen-lang-client-0561302275
```

### 배포 상태 확인
```bash
# 진행 중인 빌드 확인
gcloud builds list --ongoing --project=$GCP_PROJECT_ID
```

### 서비스 URL 확인
```bash
gcloud run services list --platform managed --project=$GCP_PROJECT_ID
```

### Redis 데이터 확인 (Cloud Shell에서 직접 접속 불가 시)
직접 접속이 어렵다면 `news-crawler`나 `buy-scanner` 로그를 통해 간접적으로 확인해야 합니다.
*   `news-crawler`: "뉴스 감성 저장 완료" 로그
*   `buy-scanner`: "뉴스 호재/악재" 로그

---

## 💡 Gemini Code Assistant 활용 팁

Cloud Shell의 Gemini에게 질문할 때 다음 정보를 함께 제공하면 더 정확한 답변을 얻을 수 있습니다.

1.  **관련 파일**: "이 문제는 `services/kis-gateway/main.py`와 관련이 있어."
2.  **로그 내용**: "`gcloud logging` 결과 500 에러가 발생했어."
3.  **최근 변경**: "방금 `shared/database.py`에서 Redis 저장 로직을 수정했어."

**예시 프롬프트:**
> "`services/kis-gateway/main.py`에서 500 에러가 발생하고 있어. `gcloud logging`을 보면 'Snapshot 조회 실패'라고 나와. `Dockerfile` 설정이 `workers=1`로 되어 있는데도 이런 문제가 발생할 수 있을까?"
