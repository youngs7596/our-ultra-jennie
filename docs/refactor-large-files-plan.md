# 대형 파일 리팩터링 계획 (요약)

## 목표 범위
- `services/scout-job/scout.py`
- `services/command-handler/handler.py`
- `shared/hybrid_scoring/quant_scorer.py`
- `shared/hybrid_scoring/factor_analyzer.py`
- `shared/database.py`
- `shared/llm.py`

## 실행 단계
1) 현행 로직 슬림화 후보 식별
   - 각 파일의 주요 책임/블록 파악
   - 죽은 코드·중복 로직·장황한 템플릿/로깅 위치 표시
   - 분리 대상 유틸/상수/템플릿 리스트업

2) 모듈 분리 설계
   - 파일별 서브모듈/헬퍼/상수 분리안 수립
   - 공용 인터페이스(함수 시그니처)와 영향 범위 정의
   - 외부 호출점 변경 최소화 방안 확정

3) 리팩터링 적용
   - `scout.py`: 단계별 함수/서브모듈로 분리(수집/스코어링/랭킹/출력), 죽은 코드 분리
   - `command-handler/handler.py`: 명령별 핸들러 분리, 템플릿 헬퍼 추출, 레이트리밋/DRY_RUN 공통화
   - `quant_scorer.py`, `factor_analyzer.py`: 팩터 계산/통계 헬퍼 분리, 중복 제거
   - `database.py`: DB/Redis 유틸 분리(레포지토리/연결/캐시 헬퍼 모듈화)
   - `llm.py`: 프로바이더/프롬프트/체인 로직 분리, 공통 util 추출

4) 회귀 대응
   - 핵심 공개 함수/메서드 시그니처 유지 또는 어댑터 제공
   - 단위 테스트 또는 기존 호출 흐름 기준으로 간단 검증

## 단계적 진행 제안
- 1차: `command-handler/handler.py`, `shared/llm.py`
- 2차: `shared/hybrid_scoring/quant_scorer.py`, `shared/hybrid_scoring/factor_analyzer.py`
- 3차: `shared/database.py`, `services/scout-job/scout.py`

## 주안점
- 기능 변화 없이 구조 개선 우선
- 삭제가 두려운 로직은 헬퍼로 격리 후 후속 제거 용이하게 구성
- 로깅/템플릿/상수는 별도 모듈로 이동해 핵심 로직을 슬림화
