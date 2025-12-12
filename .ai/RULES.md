# 🤖 AI Assistant Ground Rules

> 이 파일은 Cursor, VS Code Copilot, Antigravity, Claude Code 등 어떤 AI 환경에서도 공통으로 따르기 위한 마스터 룰입니다.

---

## 📋 프로젝트 개요

- **프로젝트명**: my-ultra-jennie
- **목적**: 주식/자산 자동 매매 에이전트 개발 (LLM 기반 판단 + 실제 트레이딩)
- **기술 스택**:
  - Backend: Python (FastAPI), Node.js
  - Database: MariaDB, Redis, ChromaDB
  - Infra: Docker Compose, Jenkins
  - Trading API: KIS (한국투자증권)

---

## 🚀 세션 시작 시 (Bootstrap)

새로운 대화/에이전트 세션을 시작하면 **반드시 아래 순서**를 따릅니다:

### 1. 이전 세션 파일 확인
```
.ai/sessions/ 폴더에서 가장 최근 session-*.md 파일을 찾아 읽습니다.
```

### 2. 컨텍스트 로딩
- 세션 파일의 **"Context for Next Session"** 섹션에 명시된 파일들 확인
- **"Next Steps"** 에서 이어서 작업할 내용 파악

### 3. 사용자에게 브리핑
```
이전 세션 (YYYY-MM-DD)에서 [작업 내용]까지 진행했습니다.
다음 할 일: [목록]
[파일 목록]을 로드하고 이어서 진행할까요?
```

---

## 🛑 세션 종료 시 (Handoff)

사용자가 **"정리해줘"**, **"세션 저장"**, **"handoff"**, **"세션 종료"** 등을 말하면:

### 1. 세션 요약 파일 생성
- 파일 위치: `.ai/sessions/session-YYYY-MM-DD-HH-mm.md`
- 하루에 여러 번이면 시간으로 구분

### 2. 포함할 내용
```markdown
# Session Handoff - YYYY-MM-DD-HH-mm

## 작업 요약 (What was done)
- 완료된 작업 목록
- 변경된 파일들과 변경 내용 요약

## 현재 상태 (Current State)
- 프로젝트의 현재 상태
- 알려진 이슈나 버그

## 다음 할 일 (Next Steps)
- [ ] 🔴 우선순위 높음
- [ ] 🟡 중간
- [ ] 🟢 나중에

## Context for Next Session
다음 세션 시작 시 아래 파일들을 먼저 읽어주세요:
- `경로/파일명` - 이유

## 핵심 결정사항 (Key Decisions)
- 왜 이런 방식을 선택했는지
- 고려했던 대안들

## 주의사항 (Warnings)
- 건드리면 안 되는 것
- 의존성 이슈
```

### 3. 사용자에게 알림
```
작업 내용을 .ai/sessions/session-YYYY-MM-DD-HH-mm.md에 저장했습니다.
다음 세션에서 뵙겠습니다! ☕
```

---

## 📊 토큰 효율성 규칙

1. **주기적 체크포인트**
   - 큰 기능 완료 시마다 중간 저장 제안
   - 대화가 길어졌을 때 정리 제안

2. **컨텍스트 최소화**
   - 전체 파일보다 관련 함수/클래스만 로드
   - 변경하지 않을 파일은 구조만 파악

3. **점진적 로딩**
   - 처음엔 핵심 파일만 로드
   - 필요할 때 추가 파일 로드

---

## 📁 핵심 파일 참조

이 프로젝트에서 자주 참조하는 핵심 파일들:

| 경로 | 역할 |
|------|------|
| `docker-compose.yml` | 전체 서비스 구성 |
| `shared/` | 공통 모듈 (DB, Redis, 유틸리티) |
| `services/scout/` | 종목 스캐닝 서비스 |
| `services/trader/` | 실제 매매 실행 서비스 |
| `services/news-crawler/` | 뉴스 수집 및 감성분석 |
| `services/dashboard-v2/` | 대시보드 (React + FastAPI) |

---

## 💻 빌드 / 테스트 / 실행

```bash
# 인프라 서비스 시작
docker compose --profile infra up -d

# 실서비스 시작
docker compose --profile real up -d

# 로그 확인
docker compose logs -f [서비스명]

# Python 린트
ruff check .
```

---

## ⚠️ 위험 작업 제한

아래 작업은 **반드시 사용자 승인 후** 실행:

- 파일/디렉토리 삭제 (`rm -rf` 등)
- 데이터베이스 마이그레이션 변경
- 환경변수/시크릿 파일 수정 (`.env`, `secrets.*`)
- 10개 이상의 파일을 동시에 수정하는 대규모 리팩토링

---

## 🗣️ 커뮤니케이션 규칙

- 한국어로 대화
- 코드 주석은 영어 또는 한국어 (기존 스타일 따름)
- 작업 전 계획 공유, 작업 후 결과 요약
