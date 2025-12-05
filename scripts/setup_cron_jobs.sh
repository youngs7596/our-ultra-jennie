#!/usr/bin/env bash
# =============================================================================
# Ultra Jennie - Cron Job 설정 스크립트
# =============================================================================
# 주간 팩터 분석 배치 작업을 cron에 등록합니다.
#
# 사용법:
#   ./scripts/setup_cron_jobs.sh          # cron job 등록
#   ./scripts/setup_cron_jobs.sh --remove # cron job 제거
#   ./scripts/setup_cron_jobs.sh --list   # 현재 cron job 확인
#
# 등록되는 작업:
#   - 주간 팩터 분석: 매주 일요일 오전 3시
#   - 일일 브리핑: 평일 오후 5시
# =============================================================================

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PYTHON="${PROJECT_ROOT}/.venv/bin/python"
SYSTEM_PYTHON="python3"

# Python 경로 결정
if [[ -f "$VENV_PYTHON" ]]; then
    PYTHON_PATH="$VENV_PYTHON"
else
    PYTHON_PATH="$SYSTEM_PYTHON"
fi

# Cron job 정의
CRON_MARKER="# Ultra Jennie Cron Jobs"
WEEKLY_FACTOR_SCRIPT="${PROJECT_ROOT}/scripts/weekly_factor_analysis_batch.py"
DAILY_BRIEFING_SCRIPT="${PROJECT_ROOT}/scripts/run_daily_briefing.py"
LOG_DIR="${PROJECT_ROOT}/logs/cron"

# 로그 디렉토리 생성
mkdir -p "$LOG_DIR"

show_help() {
    cat << EOF
Ultra Jennie Cron Job 설정 스크립트

사용법:
  $0              cron job 등록
  $0 --remove     cron job 제거
  $0 --list       현재 cron job 확인
  $0 --help       도움말 표시

등록되는 작업:
  📅 주간 팩터 분석 (매주 일요일 오전 3시)
     - 네이버 뉴스 수집
     - 뉴스 감성/카테고리 태깅
     - DART 공시 수집
     - 외국인/기관 수급 데이터 수집
     - 분기별 재무 데이터 수집
     - 팩터 분석 실행

  📊 일일 브리핑 (평일 오후 5시)
     - 포트폴리오 현황
     - 오늘 거래 내역
     - AI 추천 종목 TOP 5
     - 텔레그램 발송
EOF
}

list_jobs() {
    echo "📋 현재 등록된 Ultra Jennie cron jobs:"
    echo "----------------------------------------"
    crontab -l 2>/dev/null | grep -A1 "$CRON_MARKER" || echo "(등록된 작업 없음)"
}

remove_jobs() {
    echo "🗑️  Ultra Jennie cron jobs 제거 중..."
    
    # 기존 crontab에서 Ultra Jennie 관련 항목 제거
    crontab -l 2>/dev/null | grep -v "$CRON_MARKER" | grep -v "weekly_factor_analysis_batch.py" | crontab - 2>/dev/null || true
    
    echo "✅ cron jobs 제거 완료!"
}

install_jobs() {
    echo "📝 Ultra Jennie cron jobs 등록 중..."
    echo "   Python: $PYTHON_PATH"
    echo "   Project: $PROJECT_ROOT"
    echo ""
    
    # 기존 crontab 백업 및 Ultra Jennie 항목 제거
    EXISTING_CRON=$(crontab -l 2>/dev/null | grep -v "$CRON_MARKER" | grep -v "weekly_factor_analysis_batch.py" || true)
    
    # 새 cron job 정의
    NEW_CRON="$CRON_MARKER
# 주간 팩터 분석 - 매주 일요일 오전 3시
0 3 * * 0 cd ${PROJECT_ROOT} && PYTHONPATH=${PROJECT_ROOT} ${PYTHON_PATH} ${WEEKLY_FACTOR_SCRIPT} >> ${LOG_DIR}/weekly_factor_\$(date +\\%Y\\%m\\%d).log 2>&1
# 일일 브리핑 - 평일 오후 5시 (월~금) - Docker 서비스 직접 호출
0 17 * * 1-5 curl -s -X POST http://localhost:8086/report >> ${LOG_DIR}/daily_briefing_\$(date +\\%Y\\%m\\%d).log 2>&1"

    # crontab 업데이트
    if [[ -n "$EXISTING_CRON" ]]; then
        echo -e "${EXISTING_CRON}\n\n${NEW_CRON}" | crontab -
    else
        echo "$NEW_CRON" | crontab -
    fi
    
    echo "✅ cron jobs 등록 완료!"
    echo ""
    echo "📅 등록된 스케줄:"
    echo "   - 주간 팩터 분석: 매주 일요일 오전 3시"
    echo "   - 일일 브리핑: 평일(월~금) 오후 5시"
    echo ""
    echo "📁 로그 위치: ${LOG_DIR}/"
    echo ""
    echo "💡 수동 실행 방법:"
    echo "   # 주간 팩터 분석"
    echo "   cd ${PROJECT_ROOT}"
    echo "   PYTHONPATH=${PROJECT_ROOT} ${PYTHON_PATH} ${WEEKLY_FACTOR_SCRIPT}"
    echo ""
    echo "   # 일일 브리핑 (Docker 서비스 호출)"
    echo "   curl -X POST http://localhost:8086/report"
}

# 메인 로직
case "${1:-}" in
    --help|-h)
        show_help
        ;;
    --list|-l)
        list_jobs
        ;;
    --remove|-r)
        remove_jobs
        ;;
    *)
        install_jobs
        ;;
esac

