# KIS Gateway Service

KIS API 호출을 중앙화하여 Rate Limiting과 Circuit Breaker를 제공하는 API Gateway입니다.

## 🎯 주요 기능

### 1. Rate Limiting (Flask-Limiter)
- **제한**: 초당 3회 (엔드포인트별)
- **백엔드**: Redis (ChromaDB VM: `10.178.0.2:6379`)
- **전략**: Fixed Window
- **동작**: 동시 요청 자동 큐잉 및 제어

### 2. Circuit Breaker (pybreaker)
- **임계값**: 연속 20회 실패 시 OPEN
- **복구 시간**: 60초 후 HALF_OPEN으로 전환
- **제외 예외**: `KeyError`, `ValueError` (비즈니스 로직 오류)
- **성공 조건**: 예외 발생 시에만 failure로 카운트 (None 반환은 성공)

### 3. 지원 API

| Endpoint | Method | 기능 | Rate Limit |
|----------|--------|------|------------|
| `/health` | GET | Health Check | - |
| `/stats` | GET | 통계 조회 | - |
| `/api/market-data/snapshot` | POST | 주식 현재가 조회 | 3/s |
| `/api/market-data/daily-prices` | POST | 일봉 데이터 조회 | 3/s |
| `/api/trading/buy` | POST | 매수 주문 | 3/s |
| `/api/trading/sell` | POST | 매도 주문 | 3/s |
| `/api/account/balance` | POST | 계좌 잔고 조회 | 3/s |

## 🏗️ 아키텍처

```
Scout Job / Buy Executor / Sell Executor
                |
                ↓ (VPC Connector: jennie-vpc-connector)
         KIS Gateway (Cloud Run)
                |
         ┌──────┴──────┐
         ↓             ↓
   Flask-Limiter   pybreaker
         ↓             
  Redis (ChromaDB VM: 10.178.0.2:6379)
         ↓
   KIS API 서버
```

## 📦 기술 스택

- **Flask**: 3.0.0+
- **Gunicorn**: 21.2.0+
- **Flask-Limiter**: 3.5.0+ (Rate Limiting)
- **pybreaker**: 1.0.1+ (Circuit Breaker)
- **redis**: 5.0.0+ (Python Client)

## 🚀 배포

### 환경 변수

```yaml
# infrastructure/env-vars-real.yaml
REDIS_URL: "redis://10.178.0.2:6379"  # VPC Connector를 통해 접근
CIRCUIT_BREAKER_FAIL_MAX: "20"        # Circuit Breaker 임계값
CIRCUIT_BREAKER_TIMEOUT: "60"         # Circuit Breaker 복구 시간 (초)
```

### Cloud Run 설정

```yaml
# services/kis-gateway/cloudbuild.yaml
--vpc-connector: jennie-vpc-connector
--vpc-egress: private-ranges-only
--max-instances: 3
--min-instances: 0
--cpu: 2
--memory: 1Gi
--timeout: 300s
--concurrency: 80
```

### 방화벽 규칙

```bash
# Redis 포트 (6379) 허용
gcloud compute firewall-rules create allow-redis-internal \
    --network=default \
    --action=ALLOW \
    --rules=tcp:6379 \
    --source-ranges=10.8.0.0/28,10.178.0.0/20 \
    --target-tags=chroma-server
```

### Redis 설정 (ChromaDB VM)

```bash
# 외부 접속 허용
sudo sed -i 's/^bind 127.0.0.1 -::1/bind 0.0.0.0/' /etc/redis/redis.conf
sudo sed -i 's/^protected-mode yes/protected-mode no/' /etc/redis/redis.conf

# Redis 재시작
sudo systemctl restart redis-server
sudo systemctl status redis-server

# 연결 테스트
redis-cli ping  # 응답: PONG
```

## 🧪 테스트

### Health Check

```bash
TOKEN=$(gcloud auth print-identity-token)
curl -H "Authorization: Bearer $TOKEN" \
  https://kis-gateway-jlyuvlt3ra-du.a.run.app/health
```

### Stats 조회

```bash
curl -H "Authorization: Bearer $TOKEN" \
  https://kis-gateway-jlyuvlt3ra-du.a.run.app/stats
```

### API 호출 예시

```bash
# 주식 현재가 조회
curl -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"stock_code": "005930", "is_index": false}' \
  https://kis-gateway-jlyuvlt3ra-du.a.run.app/api/market-data/snapshot
```

## 📊 모니터링

### Cloud Logging

```bash
# 최근 로그 확인
gcloud logging read \
  "resource.type=cloud_run_revision AND resource.labels.service_name=kis-gateway" \
  --limit=50 \
  --project=gen-lang-client-0561302275

# 에러 로그만 확인
gcloud logging read \
  "resource.type=cloud_run_revision AND resource.labels.service_name=kis-gateway AND severity>=ERROR" \
  --limit=20 \
  --project=gen-lang-client-0561302275
```

### Redis 모니터링

```bash
# ChromaDB VM 접속 후
redis-cli info memory
redis-cli info stats
redis-cli monitor  # 실시간 명령어 모니터링
```

## 🔧 문제 해결

### Redis 연결 실패

```bash
# 1. Redis 서비스 상태 확인
sudo systemctl status redis-server

# 2. Redis 포트 리스닝 확인 (0.0.0.0:6379이어야 함)
sudo netstat -tulpn | grep 6379

# 3. 방화벽 규칙 확인
gcloud compute firewall-rules list --filter="name:allow-redis-internal"

# 4. VPC Connector 확인
gcloud compute networks vpc-access connectors list --region=asia-northeast3
```

### Circuit Breaker OPEN

```bash
# KIS Gateway 재시작 (Circuit Breaker 리셋)
gcloud run services update-traffic kis-gateway \
  --region=asia-northeast3 \
  --to-latest \
  --project=gen-lang-client-0561302275
```

## 📈 주요 개선 사항

### Before (수동 구현)
- ❌ 수동 구현 Circuit Breaker (100+ 줄)
- ❌ threading.Lock 기반 Rate Limiter
- ❌ 임계값 5회 (너무 민감)
- ❌ None 반환도 failure로 카운트
- ❌ 단일 인스턴스만 지원

### After (Flask-Limiter + pybreaker + Redis)
- ✅ 검증된 오픈소스 라이브러리
- ✅ Redis 기반 분산 Rate Limiting
- ✅ 임계값 20회로 상향
- ✅ 예외만 failure로 카운트
- ✅ 여러 인스턴스 지원
- ✅ 동시 요청 자동 큐잉 및 제어
- ✅ 비용 절감 ($50/월, GCP Memorystore 대신 ChromaDB VM 활용)

## 📝 참고 자료

- Flask-Limiter: https://github.com/alisaifee/flask-limiter
- pybreaker: https://github.com/danielfm/pybreaker
- Redis: https://redis.io/docs/
- VPC Connector: https://cloud.google.com/vpc/docs/configure-serverless-vpc-access
