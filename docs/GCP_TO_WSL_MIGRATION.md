# 🚀 GCP 탈출 및 WSL2 정착기 (The Great Migration)

## 1. 서론: 왜 우리는 GCP를 떠났는가? (The Motivation)

`my-supreme-jennie` 프로젝트는 당초 구글 클라우드 플랫폼(GCP)의 최첨단 서버리스 기술들(Cloud Run, Pub/Sub, Cloud Tasks, Secret Manager)을 활용한 엔터프라이즈급 MSA(Microservices Architecture)로 설계되었습니다.

하지만 프로젝트가 고도화될수록 몇 가지 현실적인 문제에 직면했습니다:

1.  **비용 폭탄의 현실화 (The Trigger)**: 단순히 '비쌀 것이다'라는 막연한 두려움이 아니었습니다. 어느 날 아침, 잠결에 받은 **카드 결제 문자**가 결정적인 트리거가 되었습니다. '숨만 쉬어도 나가는 돈(Idle Cost)'을 막기 위해 우리는 디지털 유목민처럼 클라우드를 떠나기로 결심했습니다.
2.  **관리의 복잡성**: 개인 프로젝트임에도 불구하고 IAM 권한 관리, 네트워크 설정, 배포 파이프라인 구성에 너무 많은 에너지가 소모됨.
3.  **로컬 환경과의 괴리**: 로컬 테스트 환경(Mock)과 실제 배포 환경(Cloud)의 차이로 인한 디버깅의 어려움.

이에 우리는 **"내 PC 안에 나만의 프라이빗 클라우드를 짓자!"** 라는 목표로 **WSL2(Ubuntu) + Docker Compose** 기반의 온프레미스 환경으로 대이동(Migration)을 시작했습니다.

---

## 2. 아키텍처 대전환 (Architecture Shift)

우리는 GCP의 강력한 기능들을 포기하지 않았습니다. 대신, 오픈소스 생태계에서 그에 상응하는(혹은 더 가벼운) 기술들을 찾아 **1:1로 완벽하게 치환(Replacement)** 했습니다.

무엇보다 우리는 클라우드의 1GB 무료 티어(Free Tier) VM에 억지로 서비스를 구겨 넣는 것을 거부했습니다.
**Ryzen 9 9950X3D (16 Core)**와 **64GB DDR5 RAM**, 그리고 **NVMe Gen4 SSD**라는 압도적인 로컬 컴퓨팅 파워 위에 Docker 컨테이너들을 마음껏 펼쳐놓았습니다. 이는 클라우드에서 월 수십만 원을 지불해야 얻을 수 있는 High-End 성능입니다.

### 🏛️ 기술 스택 변화 (GCP vs WSL2)

| 구분 | GCP Service (Before) | WSL2 + Docker (After) | 구현 상세 |
| :--- | :--- | :--- | :--- |
| **컴퓨팅** | Cloud Run (Serverless) | **Docker Containers** | `docker-compose`를 통해 11개의 마이크로서비스를 오케스트레이션. `restart: always`로 가용성 확보. |
| **메시징** | Cloud Pub/Sub | **RabbitMQ** (Exchange) | `Buy Scanner`가 포착한 매수 기회를 `Buy Executor`에게 비동기로 전달. |
| **작업 큐** | Cloud Tasks | **RabbitMQ** (Work Queue) | `Price Monitor`가 발생시킨 매도 주문을 `Sell Executor`가 순차적으로 처리. |
| **보안** | Secret Manager | **secrets.json** | AES 암호화 대신, 로컬 파일 시스템 권한 관리 및 `auth.py`의 스마트 로딩으로 대체. |
| **AI** | Vertex AI (Enterprise) | **Gemini API** (API Key) | 복잡한 ADC 인증 대신, Google AI Studio의 API Key를 사용하여 동일한 모델(Gemini-2.5-Pro) 구동. |
| **관제** | Cloud Logging | **Loki + Grafana** | `Promtail`이 Docker 로그를 수집하고, `Loki`가 저장하며, `Grafana`로 시각화하는 현대적인 스택 구축. |
| **네트워크** | Load Balancer / Public IP | **Cloudflare Tunnel** | 공인 IP 없이도 터널링 기술을 통해 외부(모바일 등)에서 대시보드에 안전하게 접속. |

---

## 3. 주요 기술적 극복 사례

### 3.1. 비동기 통신의 혁신 (Pub/Sub & Cloud Tasks → RabbitMQ)
GCP의 **Pub/Sub**과 **Cloud Tasks**는 각각 '이벤트 전파'와 '작업 실행'을 담당했습니다. 로컬에서는 이 두 가지 역할을 **RabbitMQ** 하나로 통합했습니다.
- **`shared/rabbitmq.py`** 모듈을 개발하여 생산자(Publisher)와 소비자(Worker) 패턴을 표준화했습니다.
- `pika` 라이브러리를 사용하여 네트워크 단절 시 자동 재연결, 메시지 지속성(Persistence)을 보장했습니다.

### 3.2. AI 두뇌의 경량화 (Vertex AI → Local Gemini)
GCP 내부에서만 동작하던 무거운 `Vertex AI` 인증 라이브러리를 걷어냈습니다.
- `google.auth` 의존성을 제거하고, `shared/gemini.py` 헬퍼를 통해 `secrets.json`에서 API Key를 로드하도록 변경했습니다.
- LangChain의 컴포넌트를 `VertexAIEmbeddings`에서 `GoogleGenerativeAIEmbeddings`로 교체하여 로컬에서도 RAG(검색 증강 생성)가 완벽하게 동작합니다.

### 3.3. 관제 시스템의 시각화 (Stackdriver → Grafana)
GCP 콘솔의 딱딱한 로그 뷰어 대신, 개발자들의 로망인 **Grafana** 대시보드를 구축했습니다.
- **Promtail**의 설정을 커스터마이징하여 Docker 컨테이너의 라벨(`com.docker.compose.service`)을 기반으로 로그를 자동 분류했습니다.
- 이제 `LogQL`을 통해 `{service="buy-scanner"}` 처럼 직관적으로 로그를 검색할 수 있습니다.

### 3.4. 외부 접속의 마법 (Cloudflare Tunnel)
집에 있는 PC는 유동 IP를 사용하므로 외부 접속이 어렵습니다. 이를 해결하기 위해 **Cloudflare Tunnel**을 도입했습니다.
- `dashboard-tunnel` 컨테이너를 띄워, 방화벽 포트 개방 없이도 `https://dashboard.yj-ai-lab.com` 도메인을 통해 안전하게 내부 Streamlit 앱에 접속합니다.

---

## 4. AI와의 협업 (The AI-Native Development)

이 거대한 마이그레이션은 혼자가 아니었습니다. **Cursor IDE**와 **LLM (Jennie, GPT)**을 활용한 **AI Pair Programming**을 통해, 인프라 설계부터 코드 리팩토링, 문서화까지 단기간에 완료할 수 있었습니다.

이는 단순한 코딩이 아니라, AI와 인간이 대화하며 아키텍처를 진화시킨 **AI 시대의 새로운 개발 패러다임**을 증명한 사례입니다.

## 5. 결론 (Conclusion)

이제 `my-supreme-jennie`는 **클라우드 비용 "0원"**으로 동작하는 **고성능 온프레미스 AI 트레이딩 시스템**이 되었습니다.

- **Cost**: Free (전기세 제외)
- **Performance**: 로컬 네트워크(localhost) 통신으로 지연 시간 최소화
- **Control**: 모든 데이터와 인프라를 내 손안에서 통제

우리는 더 이상 클라우드 비용을 걱정하며 서버를 끄지 않습니다. 24시간 잠들지 않는 제니(Jennie)가 우리의 자산을 지킵니다. 🚀

