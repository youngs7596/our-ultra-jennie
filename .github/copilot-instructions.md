# GitHub Copilot Instructions

## 필수 규칙
모든 대화 시작 시 `.ai/RULES.md` 파일을 먼저 읽고 해당 규칙을 따르세요.

## 세션 핸드오프
이 프로젝트는 세션 간 컨텍스트 전달을 위해 `.ai/sessions/` 폴더를 사용합니다.

### 새 대화 시작 시
1. `.ai/sessions/` 폴더에서 가장 최근 `session-*.md` 파일 확인
2. 해당 파일의 "Context for Next Session" 섹션에 명시된 파일들 파악
3. "Next Steps"에서 이어서 작업할 내용 확인 후 브리핑

### 대화 종료 시
`.ai/RULES.md`의 세션 종료 규칙에 따라 핸드오프 파일 생성

## 프로젝트 컨텍스트
- **프로젝트**: my-ultra-jennie (주식 자동 매매 에이전트)
- **기술 스택**: Python, FastAPI, Docker, MariaDB, Redis
- **언어**: 한국어 대화, 코드 주석은 기존 스타일 따름
