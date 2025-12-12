# CLAUDE.md

## 필수 규칙
모든 대화 시작 시 `.ai/RULES.md` 파일을 먼저 읽고 해당 규칙을 따르세요.

## 세션 핸드오프
- **컨텍스트 저장 위치**: `.ai/sessions/`
- **상세 규칙**: `.ai/RULES.md` 참조
- **트리거 키워드**: "정리해줘", "세션 저장", "handoff", "세션 종료"

## 프로젝트 정보
- **프로젝트**: my-ultra-jennie
- **목적**: 주식/자산 자동 매매 에이전트 (LLM 기반)
- **기술 스택**: Python, FastAPI, Docker, MariaDB, Redis, KIS API

## 빌드 & 실행
```bash
# 인프라 서비스 시작
docker compose --profile infra up -d

# 실서비스 시작
docker compose --profile real up -d

# 로그 확인
docker compose logs -f [서비스명]
```
