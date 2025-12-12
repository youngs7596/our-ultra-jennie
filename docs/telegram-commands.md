# 📱 Telegram 수동 명령어 가이드

> **Version**: v1.0 ✅ 구현 완료  
> **Last Updated**: 2024-12-09  
> **Status**: Production Ready

이 문서는 Telegram을 통해 트레이딩 봇에 수동 명령을 내리는 기능에 대한 설계 문서입니다.

---

## 목차

1. [개요](#개요)
2. [명령어 목록](#명령어-목록)
3. [구현 우선순위](#구현-우선순위)
4. [시스템 아키텍처](#시스템-아키텍처)
5. [보안 고려사항](#보안-고려사항)

---

## 개요

### 목적
- 운영자가 Telegram 메시지로 트레이딩 봇을 실시간 제어
- 긴급 상황 대응 (매수 중지, 전체 청산 등)
- 수동 매매 실행 및 상태 조회

### 기본 원칙
- 모든 명령은 `/` 슬래시로 시작
- 종목 검색은 종목명 또는 종목코드 모두 지원
- 위험한 명령은 확인 절차 필요 (예: `/sellall 확인`)

---

## 명령어 목록

### 1️⃣ 매매 제어 (Trading Control)

| 명령 | 설명 | 사용 예시 | 응답 예시 |
|------|------|----------|----------|
| `/pause` | 오늘 매수 중지 | `/pause` 또는 `/pause 변동성 크다` | ✅ 매수가 중지되었습니다. `/resume`으로 재개하세요. |
| `/resume` | 매수 재개 | `/resume` | ✅ 매수가 재개되었습니다. |
| `/stop` | 긴급 전체 중지 (매수/매도 모두) | `/stop` | ⚠️ 긴급 중지! 모든 거래가 중단됩니다. |
| `/dryrun` | DRY_RUN 모드 전환 | `/dryrun on` 또는 `/dryrun off` | ✅ DRY_RUN 모드: ON |

---

### 2️⃣ 수동 매매 (Manual Trading)

| 명령 | 설명 | 사용 예시 | 응답 예시 |
|------|------|----------|----------|
| `/buy 종목 [수량]` | 즉시 매수 | `/buy 삼성전자 10` | 💰 삼성전자 10주 매수 주문 완료 (72,000원) |
| | | `/buy 005930 5` | |
| | | `/buy 카카오` (수량 미지정 시 자동 계산) | |
| `/sell 종목 [수량]` | 즉시 매도 | `/sell 삼성전자 10` | 💵 삼성전자 10주 매도 완료 (+3.5%) |
| | | `/sell 005930 전량` | |
| `/sellall` | 전체 포지션 청산 (확인 필요) | `/sellall 확인` | ⚠️ 3개 종목 전체 청산 완료 |

> ⚠️ **주의**: 수량 미지정 시 포지션 사이징 알고리즘으로 자동 계산

---

### 3️⃣ 조회 (Status & Info)

| 명령 | 설명 | 사용 예시 | 응답 예시 |
|------|------|----------|----------|
| `/status` | 시스템 상태 확인 | `/status` | 🟢 시스템 정상 운영 중 |
| `/portfolio` | 현재 포트폴리오 | `/portfolio` | (아래 예시 참조) |
| `/pnl` | 오늘 손익 현황 | `/pnl` | 📊 오늘 수익: +125,000원 (+1.2%) |
| `/balance` | 계좌 잔고 | `/balance` | 💰 가용 현금: 5,230,000원 |
| `/price 종목` | 현재가 조회 | `/price 삼성전자` | 삼성전자(005930): 72,300원 (+1.2%) |

**`/portfolio` 응답 예시:**
```
📊 현재 포트폴리오 (3종목)

1. 삼성전자 (005930)
   보유: 50주 | 평단: 71,000원
   현재: 72,300원 | 수익: +1.83%

2. SK하이닉스 (000660)
   보유: 30주 | 평단: 180,000원
   현재: 185,000원 | 수익: +2.78%

3. 카카오 (035720)
   보유: 20주 | 평단: 52,000원
   현재: 51,200원 | 수익: -1.54%

💰 총 평가금액: 15,230,000원
📈 총 수익률: +2.1%
```

---

### 4️⃣ 설정 변경 (Settings)

| 명령 | 설명 | 사용 예시 | 응답 예시 |
|------|------|----------|----------|
| `/risk 레벨` | 리스크 레벨 변경 | `/risk conservative` | ✅ 리스크 레벨: CONSERVATIVE |
| | | `/risk moderate` | |
| | | `/risk aggressive` | |
| `/minscore 점수` | 최소 LLM 점수 변경 | `/minscore 80` | ✅ 최소 LLM 점수: 80점 |
| `/maxbuy 횟수` | 일일 최대 매수 횟수 | `/maxbuy 3` | ✅ 일일 최대 매수: 3회 |
| `/config` | 현재 설정 조회 | `/config` | (현재 설정값 목록 출력) |

---

### 5️⃣ 알림 제어 (Notifications)

| 명령 | 설명 | 사용 예시 | 응답 예시 |
|------|------|----------|----------|
| `/mute 시간` | N분간 알림 끄기 | `/mute 30` | 🔇 30분간 알림이 꺼집니다. |
| `/unmute` | 알림 다시 켜기 | `/unmute` | 🔔 알림이 다시 켜졌습니다. |
| `/alert 종목 가격` | 가격 알림 설정 | `/alert 삼성전자 80000` | ⏰ 삼성전자 80,000원 도달 시 알림 |
| `/alerts` | 설정된 알림 목록 | `/alerts` | (알림 목록 출력) |

---

### 6️⃣ 관심종목 (Watchlist)

| 명령 | 설명 | 사용 예시 | 응답 예시 |
|------|------|----------|----------|
| `/watch 종목` | 관심종목 추가 | `/watch 삼성전자` | ✅ 관심종목에 추가: 삼성전자 |
| `/unwatch 종목` | 관심종목 제거 | `/unwatch 005930` | ✅ 관심종목에서 제거: 삼성전자 |
| `/watchlist` | 관심종목 목록 | `/watchlist` | (관심종목 목록 출력) |

---

### 7️⃣ 도움말 (Help)

| 명령 | 설명 | 사용 예시 |
|------|------|----------|
| `/help` | 명령어 도움말 | `/help` |
| `/help 명령어` | 특정 명령어 상세 | `/help buy` |

---

## 구현 우선순위

### Phase 1 (MVP) 🎯
> 핵심 운영 기능

| 우선순위 | 명령어 | 난이도 | 필요 연동 |
|---------|--------|--------|----------|
| 1 | `/pause`, `/resume` | 🟢 쉬움 | Redis flag |
| 2 | `/status` | 🟢 쉬움 | 각 서비스 health check |
| 3 | `/portfolio` | 🟢 쉬움 | DB 조회 |
| 4 | `/balance` | 🟢 쉬움 | KIS API |
| 5 | `/buy` | 🟡 보통 | buy-executor RabbitMQ |
| 6 | `/sell` | 🟡 보통 | sell-executor / DB |

### Phase 2 (확장)
> 편의 기능

- `/pnl`, `/price`
- `/dryrun`, `/config`
- `/minscore`, `/maxbuy`

### Phase 3 (고급)
> 알림 및 관심종목

- `/alert`, `/alerts`
- `/watch`, `/unwatch`
- `/mute`, `/unmute`

---

## 시스템 아키텍처

### 전체 흐름

```
┌─────────────────────────────────────────────────────────────┐
│                      Telegram                                │
│  User: "/buy 삼성전자 10"                                     │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  command-handler 서비스                                       │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ Telegram Listener (polling 방식)                         │ │
│  │  - getUpdates() 주기적 호출                               │ │
│  │  - 새 메시지 수신 및 파싱                                  │ │
│  └─────────────────────────────────────────────────────────┘ │
│                            │                                  │
│                            ▼                                  │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ Command Parser                                           │ │
│  │  - 명령어 파싱 ("/buy 삼성전자 10")                        │ │
│  │  - 종목 코드 변환 (삼성전자 → 005930)                      │ │
│  │  - 파라미터 추출                                          │ │
│  └─────────────────────────────────────────────────────────┘ │
│                            │                                  │
│                            ▼                                  │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ Command Dispatcher                                       │ │
│  │  - Redis: /pause, /resume, /dryrun (flag 저장)           │ │
│  │  - RabbitMQ: /buy → buy-signals 큐                       │ │
│  │  - Direct: /portfolio, /status (즉시 응답)                │ │
│  └─────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
           │                        │                    │
           ▼                        ▼                    ▼
      ┌────────┐            ┌────────────┐        ┌──────────┐
      │ Redis  │            │ RabbitMQ   │        │ DB Query │
      │ Flags  │            │ buy-signals│        │ Results  │
      └────────┘            └────────────┘        └──────────┘
           │                        │
           ▼                        ▼
┌─────────────────────┐    ┌─────────────────────┐
│ buy-scanner         │    │ buy-executor        │
│ - PAUSE flag 확인   │    │ - 메시지 수신       │
│ - flag ON이면 skip  │    │ - 매수 실행         │
└─────────────────────┘    └─────────────────────┘
```

### Redis Flag 구조

```python
# 키 네이밍 컨벤션
trading:pause           = "true" / "false"
trading:dryrun          = "true" / "false"
trading:stop            = "true" / "false"
config:min_llm_score    = "80"
config:max_buy_per_day  = "3"
notification:mute       = "1702012800"  # Unix timestamp (해제 시간)
```

---

## 보안 고려사항

### 1. 인증 (Authentication)
- **Chat ID 검증**: 허용된 Chat ID만 명령 처리
- 환경변수 `TELEGRAM_ALLOWED_CHAT_IDS`로 화이트리스트 관리

```python
ALLOWED_CHAT_IDS = os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "").split(",")

def is_authorized(chat_id):
    return str(chat_id) in ALLOWED_CHAT_IDS
```

### 2. 위험 명령 보호
- `/sellall`, `/stop` 등 위험 명령은 확인 키워드 필요
- 예: `/sellall 확인`, `/stop 긴급`

### 3. Rate Limiting
- 동일 명령 반복 실행 방지 (최소 간격 5초)
- 일일 수동 거래 횟수 제한

### 4. 로깅
- 모든 명령 로그 기록 (who, when, what)
- 의심스러운 활동 알림

---

## 구현 참고사항

### Telegram Bot API 연동

```python
import requests

class TelegramListener:
    def __init__(self, token, allowed_chat_ids):
        self.token = token
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.allowed_chat_ids = allowed_chat_ids
        self.last_update_id = 0
    
    def get_updates(self):
        """새 메시지 폴링"""
        url = f"{self.base_url}/getUpdates"
        params = {
            "offset": self.last_update_id + 1,
            "timeout": 30  # Long polling
        }
        response = requests.get(url, params=params)
        return response.json().get("result", [])
    
    def send_message(self, chat_id, text):
        """메시지 전송"""
        url = f"{self.base_url}/sendMessage"
        data = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown"
        }
        requests.post(url, data=data)
```

### 종목 코드 변환

```python
def resolve_stock_code(name_or_code: str) -> tuple:
    """
    종목명 또는 코드를 받아 (code, name) 튜플 반환
    
    Args:
        name_or_code: "삼성전자" 또는 "005930"
    
    Returns:
        ("005930", "삼성전자")
    """
    # 6자리 숫자면 코드로 간주
    if name_or_code.isdigit() and len(name_or_code) == 6:
        # DB에서 종목명 조회
        stock_name = database.get_stock_name(name_or_code)
        return (name_or_code, stock_name)
    else:
        # DB에서 종목코드 조회
        stock_code = database.get_stock_code_by_name(name_or_code)
        return (stock_code, name_or_code)
```

---

## 다음 단계

1. [x] command-handler 서비스에 Telegram Listener 추가
2. [x] Redis flag 연동 (`/pause`, `/resume`)
3. [x] docker-compose.yml에 command-handler 서비스 등록
4. [x] Phase 1 명령어 구현 및 테스트
5. [x] 보안 설정 (Chat ID 화이트리스트)

> **✅ 모든 핵심 기능이 구현 완료되었습니다.** (2025-12-12)
