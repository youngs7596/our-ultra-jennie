# 🚀 Ultra Jennie 설치 가이드

이 문서는 Ultra Jennie를 처음 설치하는 사용자를 위한 단계별 가이드입니다.

---

## 📋 목차

1. [시스템 요구사항](#1-시스템-요구사항)
2. [API 키 발급](#2-api-키-발급)
3. [MariaDB 설치](#3-mariadb-설치)
4. [프로젝트 설치](#4-프로젝트-설치)
5. [secrets.json 설정](#5-secretsjson-설정)
6. [Docker 환경 설정](#6-docker-환경-설정)
7. [데이터베이스 초기화](#7-데이터베이스-초기화)
8. [서비스 실행](#8-서비스-실행)
9. [검증 및 테스트](#9-검증-및-테스트)
10. [트러블슈팅](#10-트러블슈팅)

---

## 1. 시스템 요구사항

### 하드웨어
| 항목 | 최소 | 권장 |
|------|------|------|
| CPU | 4코어 | 8코어+ |
| RAM | 8GB | 16GB+ |
| 저장소 | 50GB SSD | 100GB+ SSD |

### 소프트웨어
| 항목 | 버전 | 확인 명령어 |
|------|------|------------|
| OS | Ubuntu 20.04+ / WSL2 | `lsb_release -a` |
| Python | 3.10+ | `python3 --version` |
| Docker | 24.0+ | `docker --version` |
| Docker Compose | 2.20+ | `docker compose version` |
| Git | 2.30+ | `git --version` |

### 필수 소프트웨어 설치

```bash
# Ubuntu/WSL2
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-pip python3-venv git curl wget

# Docker 설치
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker $USER

# Docker Compose (이미 Docker에 포함됨)
docker compose version
```

---

## 2. API 키 발급

### 2.1 한국투자증권 (KIS) API

> 실제 트레이딩에 필요한 핵심 API입니다.

1. **계좌 개설**
   - [한국투자증권](https://www.truefriend.com/) 홈페이지에서 비대면 계좌 개설

2. **API 신청**
   - 로그인 후 [Open API 서비스](https://apiportal.koreainvestment.com/) 접속
   - "API 신청" 클릭
   - 모의투자용(Virtual) + 실전투자용(Real) 모두 신청

3. **발급받을 키**
   | 키 | 용도 | secrets.json 키 |
   |---|------|----------------|
   | 모의 App Key | 테스트용 | `kis-v-app-key` |
   | 모의 App Secret | 테스트용 | `kis-v-app-secret` |
   | 모의 계좌번호 | 테스트용 | `kis-v-account-no` |
   | 실전 App Key | 실거래용 | `kis-r-app-key` |
   | 실전 App Secret | 실거래용 | `kis-r-app-secret` |
   | 실전 계좌번호 | 실거래용 | `kis-r-account-no` |

### 2.2 Claude API (Anthropic)

> Scout Pipeline의 Hunter, Debate에 사용됩니다.

1. [Anthropic Console](https://console.anthropic.com/) 가입
2. API Keys 메뉴에서 새 키 생성
3. `secrets.json`의 `claude-api-key`에 저장

**요금**: 
- Claude 3.5 Sonnet: $3/1M input tokens, $15/1M output tokens
- 월 예상 비용: $10-50 (사용량에 따라)

### 2.3 OpenAI API

> Scout Pipeline의 Judge(최종 판단)에 사용됩니다.

1. [OpenAI Platform](https://platform.openai.com/) 가입
2. API Keys에서 새 키 생성
3. `secrets.json`의 `openai-api-key`에 저장

**요금**:
- GPT-4o-mini: $0.15/1M input, $0.60/1M output
- 월 예상 비용: $5-20

### 2.4 Gemini API (Google)

> 뉴스 임베딩(ChromaDB)에 사용됩니다.

1. [Google AI Studio](https://aistudio.google.com/) 접속
2. "Get API Key" 클릭
3. 새 프로젝트에서 API 키 생성
4. `secrets.json`의 `gemini-api-key`에 저장

**요금**: 무료 티어 (분당 60회 요청)

### 2.5 DART API (금융감독원)

> 공시 정보 수집에 사용됩니다.

1. [DART 오픈API](https://opendart.fss.or.kr/) 가입
2. 인증키 신청
3. 이메일로 발급된 키를 `secrets.json`의 `dart-api-key`에 저장

**요금**: 무료 (일 10,000회 제한)

### 2.6 Telegram Bot (선택사항)

> 매수/매도 알림 수신용입니다.

1. Telegram에서 [@BotFather](https://t.me/BotFather) 대화
2. `/newbot` 명령어로 봇 생성
3. 발급된 토큰을 `telegram-bot-token`에 저장
4. 봇과 대화 시작 후 [@userinfobot](https://t.me/userinfobot)에서 Chat ID 확인
5. Chat ID를 `telegram-chat-id`에 저장

### 2.7 Cloudflare Tunnel (선택사항)

> 외부에서 로컬 서비스(대시보드 등)에 안전하게 접근하기 위한 설정입니다.

1. **Cloudflare 계정 생성**
   - [Cloudflare](https://www.cloudflare.com/) 가입
   - 도메인 추가 (기존 도메인 또는 새로 구매)

2. **Zero Trust 설정**
   - Cloudflare 대시보드 → Zero Trust 클릭
   - Access → Tunnels 메뉴 이동

3. **Tunnel 생성**
   - "Create a tunnel" 클릭
   - Tunnel 이름 입력 (예: `ultra-jennie-tunnel`)
   - 환경 선택: Docker
   - 토큰 복사하여 `secrets.json`의 `cloudflare-tunnel-token`에 저장

4. **Public Hostname 설정**
   - Tunnel 생성 후 "Public Hostnames" 탭
   - Add a public hostname:
     | Subdomain | Domain | Service |
     |-----------|--------|---------|
     | `jennie` | `yourdomain.com` | `http://localhost:80` |
     | `api` | `yourdomain.com` | `http://localhost:8090` |

5. **Docker Compose에서 실행**
   ```bash
   # docker-compose.yml에 cloudflared 서비스가 이미 포함되어 있음
   docker compose up -d cloudflared
   ```

6. **접속 확인**
   - 브라우저에서 `https://jennie.yourdomain.com` 접속
   - 대시보드가 표시되면 성공

**보안 팁**:
- Zero Trust → Access → Applications에서 이메일 인증 추가 권장
- 특정 이메일만 접근 허용 설정 가능

---

## 3. MariaDB 설치

### 3.1 Ubuntu/WSL2에 직접 설치

```bash
# MariaDB 설치
sudo apt install -y mariadb-server mariadb-client

# 서비스 시작 및 자동 시작 설정
sudo systemctl start mariadb
sudo systemctl enable mariadb

# 보안 설정 (root 비밀번호 설정)
sudo mysql_secure_installation
```

### 3.2 초기 설정

```bash
# MariaDB 접속
sudo mysql -u root -p

# 데이터베이스 및 사용자 생성
CREATE DATABASE jennie_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'jennie'@'localhost' IDENTIFIED BY 'your_secure_password';
CREATE USER 'jennie'@'127.0.0.1' IDENTIFIED BY 'your_secure_password';
GRANT ALL PRIVILEGES ON jennie_db.* TO 'jennie'@'localhost';
GRANT ALL PRIVILEGES ON jennie_db.* TO 'jennie'@'127.0.0.1';
FLUSH PRIVILEGES;
EXIT;
```

### 3.3 연결 테스트

```bash
mysql -u jennie -p -h 127.0.0.1 jennie_db -e "SELECT 1;"
```

---

## 4. 프로젝트 설치

### 4.1 저장소 클론

```bash
cd ~/projects  # 또는 원하는 디렉토리
git clone https://github.com/youngs7596/my-ultra-jennie.git
cd my-ultra-jennie
```

### 4.2 Python 가상환경 설정

```bash
# 가상환경 생성
python3 -m venv .venv

# 활성화
source .venv/bin/activate

# 의존성 설치
pip install --upgrade pip
pip install -r requirements.txt
```

### 4.3 환경 확인

```bash
# Python 버전 확인
python --version  # 3.10+

# 주요 패키지 확인
pip list | grep -E "flask|sqlalchemy|pandas|langchain"
```

---

## 5. secrets.json 설정

### 5.1 예제 파일 복사

```bash
cp secrets.example.json secrets.json
```

### 5.2 secrets.json 편집

```bash
nano secrets.json  # 또는 선호하는 편집기
```

```json
{
  "gemini-api-key": "AIza...(Google AI Studio에서 발급)",
  "openai-api-key": "sk-...(OpenAI에서 발급)",
  "claude-api-key": "sk-ant-...(Anthropic에서 발급)",
  "dart-api-key": "...(DART에서 발급)",
  
  "kis-r-account-no": "12345678-01",
  "kis-r-app-key": "...(실전투자용)",
  "kis-r-app-secret": "...(실전투자용)",
  
  "kis-v-account-no": "12345678-01",
  "kis-v-app-key": "...(모의투자용)",
  "kis-v-app-secret": "...(모의투자용)",
  
  "telegram-bot-token": "123456:ABC-DEF...",
  "telegram-chat-id": "123456789",
  
  "cloudflare-tunnel-token": "(선택사항)",
  
  "mariadb-user": "jennie",
  "mariadb-password": "your_secure_password",
  "mariadb-host": "127.0.0.1",
  "mariadb-database": "jennie_db"
}
```

### 5.3 파일 권한 설정

```bash
chmod 600 secrets.json  # 소유자만 읽기/쓰기
```

---

## 6. Docker 환경 설정

### 6.1 인프라 서비스 시작

```bash
# 인프라 프로파일로 Redis, RabbitMQ, ChromaDB, Loki, Grafana 등 시작
docker compose --profile infra up -d

# 상태 확인
docker compose ps
```

### 6.2 서비스 상태 확인

```bash
# Redis 연결 테스트
docker exec -it $(docker ps -qf "name=redis") redis-cli PING
# 예상 출력: PONG

# RabbitMQ 관리 콘솔
# http://localhost:15672 (guest/guest)

# ChromaDB 헬스체크
curl http://localhost:8000/api/v1/heartbeat

# Grafana 대시보드
# http://localhost:3300 (admin/admin)
```

---

## 7. 데이터베이스 초기화

### 7.1 테이블 스키마 생성

```bash
# 가상환경 활성화 확인
source .venv/bin/activate

# 스키마 생성 스크립트 실행
python scripts/init_database.py
```

### 7.2 기본 데이터 로드

```bash
# KOSPI 200 종목 마스터 데이터
python utilities/update_stock_master.py

# 일봉 데이터 수집 (최근 3년)
python scripts/collect_daily_prices.py --days 1095

# 뉴스 데이터 수집 (선택사항, 시간 소요)
python scripts/collect_naver_news.py --codes 200 --days 30
```

### 7.3 데이터 확인

```bash
mysql -u jennie -p jennie_db -e "
SELECT COUNT(*) as stock_count FROM STOCK_MASTER;
SELECT COUNT(*) as price_count FROM STOCK_DAILY_PRICES_3Y;
"
```

---

## 8. 서비스 실행

### 8.1 Mock 모드 (테스트용)

```bash
# 인프라 서비스가 먼저 실행되어 있어야 합니다
docker compose --profile infra up -d

# Mock 프로필로 애플리케이션 스택 실행
docker compose --profile mock up -d

# 로그 확인
docker compose logs -f kis-gateway-mock buy-scanner-mock
```

### 8.2 Real 모드 (실거래)

> ⚠️ 실제 자금이 사용됩니다. 충분한 테스트 후 사용하세요!

```bash
# 장 시간 확인 (09:00~15:30 KST)
# DRY_RUN=true로 먼저 테스트 권장

# 인프라 서비스 시작 (이미 실행 중이면 생략)
docker compose --profile infra up -d

# Real 프로필 실행
docker compose --profile real up -d

# 또는 한 번에 시작
docker compose --profile infra --profile real up -d
```

### 8.3 개별 서비스 실행 (개발용)

```bash
# KIS Gateway
cd services/kis-gateway && python main.py

# Buy Scanner
cd services/buy-scanner && python main.py

# Scout Job (수동 실행)
cd services/scout-job && python scout.py
```

---

## 9. 검증 및 테스트

### 9.1 API 연결 테스트

```bash
# KIS API 테스트
python -c "
from shared.kis import KISClient
kis = KISClient()
print(kis.get_stock_snapshot('005930'))  # 삼성전자
"
```

### 9.2 백테스트 실행

```bash
# 기본 백테스트 (180일)
python utilities/backtest_gpt_v2.py --days 180

# Out-of-Sample 테스트
python utilities/backtest_gpt_v2.py --days 180 --train-ratio 0.7
```

### 9.3 Scout Job 수동 실행

```bash
# Mock 모드로 Scout 실행
TRADING_MODE=MOCK python services/scout-job/scout.py
```

### 9.4 Telegram 알림 테스트

```bash
python -c "
from shared.notification import TelegramBot
bot = TelegramBot()
bot.send_message('🧪 Ultra Jennie 테스트 메시지입니다!')
"
```

---

## 10. 트러블슈팅

### 10.1 DB 연결 실패

```
❌ DB: MariaDB 연결 실패!
```

**해결:**
```bash
# MariaDB 상태 확인
sudo systemctl status mariadb

# 재시작
sudo systemctl restart mariadb

# 사용자 권한 확인
mysql -u root -p -e "SHOW GRANTS FOR 'jennie'@'127.0.0.1';"
```

### 10.2 API 키 오류

```
❌ Invalid API Key
```

**해결:**
```bash
# secrets.json 확인
cat secrets.json | python -m json.tool

# 환경변수 확인
echo $SECRETS_FILE
```

### 10.3 Docker 네트워크 오류

```
Error: network jennie-network not found
```

**해결:**
```bash
docker network create jennie-network
docker compose down && docker compose up -d
```

### 10.4 메모리 부족

```
Killed (OOM)
```

**해결:**
```bash
# Docker 메모리 제한 조정 (docker-compose.yml)
deploy:
  resources:
    limits:
      memory: 2G
```

### 10.5 KIS API Rate Limit

```
⚠️ Rate limit exceeded
```

**해결:**
- Circuit Breaker 설정 확인 (`CIRCUIT_BREAKER_FAIL_MAX`)
- 요청 간격 조정 (`RATE_LIMIT_DELAY_MS`)

---

## 📚 다음 단계

1. [백테스트 가이드](./BACKTEST_GUIDE.md) - 전략 최적화 방법
2. [Scout 하이브리드 스코어링](./SCOUT_HYBRID_SCORING.md) - 종목 발굴 시스템
3. [스케줄러 아키텍처](./SCHEDULER_ARCHITECTURE.md) - 자동화 설정
4. [README](../README.md) - 프로젝트 개요

---

## 🆘 도움이 필요하시면

- **GitHub Issues**: 버그 리포트 및 기능 요청
- **Discussions**: 질문 및 토론

---

*작성: Ultra Jennie v1.0 (2025-12-05)*

