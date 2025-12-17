#!/usr/bin/env python3
# Version: v1.0
# 작업 LLM: Claude Sonnet 4.5, Claude Opus 4.5
"""
Scout Job v1.0 - 종목 발굴 파이프라인
- 깐깐한 필터링 (기본점수 20, Hunter 통과 60점, Judge 승인 75점)
- [v1.0] 쿼터제 도입: 최종 Watchlist 상위 15개만 저장
- [v1.0] Debate 프롬프트 강화: Bull/Bear 캐릭터 극단적으로 설정
- Redis 상태 저장: Dashboard에서 실시간 파이프라인 진행 상황 확인 가능
- 경쟁사 수혜 점수 반영: 경쟁사 악재 시 Hunter 점수에 가산
"""

import logging
import os
import sys
import time
import re
import threading
import json
import hashlib
from typing import Dict, Tuple, List, Optional
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
import requests
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
import redis

# 로깅 설정을 모든 import 보다 먼저 수행
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - [%(funcName)s] - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

logger = logging.getLogger(__name__)

# 공용 라이브러리 임포트를 위한 경로 설정
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # /app
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import shared.auth as auth
import shared.database as database
from shared.db.connection import session_scope, ensure_engine_initialized
from shared.kis import KISClient as KIS_API
from shared.kis.gateway_client import KISGatewayClient
from shared.llm import JennieBrain
from shared.financial_data_collector import batch_update_watchlist_financial_data
from shared.gemini import ensure_gemini_api_key  # [v3.0] Local Gemini Auth 추가
from shared.archivist import Archivist  # [v6.0] Data Strategy Logger

import chromadb
from langchain_chroma import Chroma
# from langchain_google_vertexai import VertexAIEmbeddings # [v3.0] Vertex AI 제거
from langchain_google_genai import GoogleGenerativeAIEmbeddings # [v3.0] Gemini API Key 기반

# [v3.8] FinanceDataReader for KOSPI 200 Universe
try:
    import FinanceDataReader as fdr
    FDR_AVAILABLE = True
    logger.info("✅ FinanceDataReader 모듈 로드 성공")
except ImportError:
    FDR_AVAILABLE = False
    logger.warning("⚠️ FinanceDataReader 미설치 - 네이버 금융 스크래핑으로 폴백")

# [v2.2 수정] backtest 모듈 임포트
try:
    from utilities.backtest import Backtester
    logger.info("✅ Backtester 모듈 임포트 성공")
except ImportError as e:
    logger.warning(f"⚠️ Backtester 모듈 임포트 실패 (백테스트 기능 비활성화): {e}")
    Backtester = None

# Chroma 서버
CHROMA_SERVER_HOST = os.getenv("CHROMA_SERVER_HOST", "10.178.0.2") 
CHROMA_SERVER_PORT = 8000

# --- (B) 정적 우량주 목록 (안전망/Fallback) ---
BLUE_CHIP_STOCKS = [
    {"code": "0001", "name": "KOSPI", "is_tradable": False},
    {"code": "005930", "name": "삼성전자", "is_tradable": True},
    # ... (이하 생략, 기존 리스트 유지)
    {"code": "000660", "name": "SK하이닉스", "is_tradable": True},
    {"code": "035420", "name": "NAVER", "is_tradable": True},
    {"code": "035720", "name": "카카오", "is_tradable": True},
]

# =============================================================================
# [v1.1 Refactored] 캐시/상태 관리 함수들은 scout_cache.py로 분리됨
# =============================================================================
from scout_cache import (
    # 상수
    STATE_PREFIX, CANDIDATE_DIGEST_SUFFIX, CANDIDATE_HASHES_SUFFIX,
    LLM_CACHE_SUFFIX, LLM_LAST_RUN_SUFFIX, ISO_FORMAT_Z,
    REDIS_URL,
    # Redis 함수
    _get_redis, _utcnow, update_pipeline_status, save_pipeline_results,
    # CONFIG 테이블 함수
    _get_scope, _make_state_key, _load_json_config, _save_json_config,
    _get_last_llm_run_at, _save_last_llm_run_at,
    _load_candidate_state, _save_candidate_state,
    _load_llm_cache, _save_llm_cache,
    # LLM_EVAL_CACHE 테이블 함수
    _load_llm_cache_from_db, _save_llm_cache_to_db, _save_llm_cache_batch,
    # 캐시 유효성 검사 및 해시 계산
    _is_cache_valid_direct, _get_price_bucket, _get_volume_bucket, _get_foreign_direction,
    _hash_candidate_payload, _compute_candidate_hashes,
    _minutes_since, _parse_int_env, _is_cache_entry_valid,
    _record_to_watchlist_entry, _record_to_cache_payload, _cache_payload_to_record,
)

# =============================================================================
# [v1.1 Refactored] 종목 유니버스 관련 함수들은 scout_universe.py로 분리됨
# =============================================================================
from scout_universe import (
    SECTOR_MAPPING, BLUE_CHIP_STOCKS, FDR_AVAILABLE,
    analyze_sector_momentum, get_hot_sector_stocks,
    get_dynamic_blue_chips, get_momentum_stocks,
)

# =============================================================================
# [v1.1 Refactored] 자동 최적화 함수들은 scout_optimizer.py로 분리됨
# =============================================================================
from scout_optimizer import (
    run_auto_parameter_optimization,
    run_simple_backtest, generate_optimized_params, verify_params_with_llm,
)

# =============================================================================
# [v1.1 Refactored] 파이프라인 태스크 함수들은 scout_pipeline.py로 분리됨
# =============================================================================
from scout_pipeline import (
    is_hybrid_scoring_enabled,
    process_quant_scoring_task,
    process_phase1_hunter_v5_task, process_phase23_judge_v5_task,
    process_phase1_hunter_task, process_phase23_debate_judge_task,
    process_llm_decision_task, fetch_kis_data_task,
)

_redis_client = None  # scout_cache에서 관리하지만 호환성 유지




def prefetch_all_data(candidate_stocks: Dict[str, Dict], kis_api, vectorstore) -> Tuple[Dict[str, Dict], Dict[str, str]]:
    """
    [v4.2] Phase 1 시작 전에 모든 데이터를 일괄 조회하여 캐시
    
    Returns:
        (snapshot_cache, news_cache) - 종목코드를 키로 하는 dict
    
    효과: 병렬 스레드 안에서 API 호출 제거 → Rate Limit 회피 + 속도 향상
    """
    stock_codes = list(candidate_stocks.keys())
    logger.info(f"   (Prefetch) {len(stock_codes)}개 종목 데이터 사전 조회 시작...")
    
    snapshot_cache: Dict[str, Dict] = {}
    news_cache: Dict[str, str] = {}
    
    prefetch_start = time.time()
    
    # 1. KIS API 스냅샷 병렬 조회 (4개 워커)
    logger.info(f"   (Prefetch) KIS 스냅샷 조회 중...")
    snapshot_start = time.time()
    
    def fetch_snapshot(code):
        try:
            if hasattr(kis_api, 'API_CALL_DELAY'):
                time.sleep(kis_api.API_CALL_DELAY * 0.3)  # 약간의 딜레이
            return code, kis_api.get_stock_snapshot(code)
        except Exception as e:
            logger.debug(f"   ⚠️ [{code}] Snapshot 조회 실패: {e}")
            return code, None
    
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(fetch_snapshot, code) for code in stock_codes]
        for future in as_completed(futures):
            code, snapshot = future.result()
            if snapshot:
                snapshot_cache[code] = snapshot
    
    snapshot_time = time.time() - snapshot_start
    logger.info(f"   (Prefetch) ✅ KIS 스냅샷 {len(snapshot_cache)}/{len(stock_codes)}개 조회 완료 ({snapshot_time:.1f}초)")
    
    # 2. ChromaDB 뉴스 병렬 조회 (8개 워커)
    if vectorstore:
        logger.info(f"   (Prefetch) ChromaDB 뉴스 조회 중...")
        news_start = time.time()
        
        def fetch_news(code_name):
            code, name = code_name
            try:
                news = fetch_stock_news_from_chroma(vectorstore, code, name, k=3)
                return code, news
            except Exception as e:
                logger.debug(f"   ⚠️ [{code}] 뉴스 조회 실패: {e}")
                return code, "뉴스 조회 실패"
        
        code_name_pairs = [(code, info.get('name', '')) for code, info in candidate_stocks.items()]
        
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(fetch_news, pair) for pair in code_name_pairs]
            for future in as_completed(futures):
                code, news = future.result()
                news_cache[code] = news
        
        news_time = time.time() - news_start
        valid_news = sum(1 for n in news_cache.values() if n and n not in ["뉴스 DB 미연결", "최근 관련 뉴스 없음", "뉴스 검색 오류", "뉴스 조회 실패"])
        logger.info(f"   (Prefetch) ✅ ChromaDB 뉴스 {valid_news}/{len(stock_codes)}개 조회 완료 ({news_time:.1f}초)")
    
    total_time = time.time() - prefetch_start
    logger.info(f"   (Prefetch) ✅ 전체 사전 조회 완료 ({total_time:.1f}초)")
    
    return snapshot_cache, news_cache


def enrich_candidates_with_market_data(candidate_stocks: Dict[str, Dict], session, vectorstore) -> None:
    """
    [v4.1] 후보군에 시장 데이터 추가 (해시 계산용)
    
    해시에 포함될 데이터:
    - price: 최신 종가 (5% 버킷화됨)
    - volume: 최신 거래량 (10만주 버킷화됨)
    - foreign_net: 외국인 순매수 (방향만 - buy/sell/neutral)
    - news_date: 최신 뉴스 날짜 (YYYY-MM-DD)
    """
    if not candidate_stocks:
        return
    
    stock_codes = list(candidate_stocks.keys())
    logger.info(f"   (Hash) {len(stock_codes)}개 종목 시장 데이터 조회 중...")
    
    # 1. DB에서 최신 가격/거래량 데이터 일괄 조회
    try:
        from sqlalchemy import text
        
        placeholders = ','.join([f"'{code}'" for code in stock_codes])
        
        # 최신 날짜의 데이터만 조회 (가격, 거래량)
        query = text(f"""
            SELECT STOCK_CODE, CLOSE_PRICE, VOLUME, PRICE_DATE
            FROM STOCK_DAILY_PRICES_3Y
            WHERE STOCK_CODE IN ({placeholders})
            AND (STOCK_CODE, PRICE_DATE) IN (
                SELECT STOCK_CODE, MAX(PRICE_DATE) 
                FROM STOCK_DAILY_PRICES_3Y
                WHERE STOCK_CODE IN ({placeholders})
                GROUP BY STOCK_CODE
            )
        """)
        rows = session.execute(query).fetchall()
        
        for row in rows:
            code = row[0]
            price = row[1]
            volume = row[2]
            
            if code in candidate_stocks:
                candidate_stocks[code]['price'] = float(price) if price else 0
                candidate_stocks[code]['volume'] = int(volume) if volume else 0
        
        logger.info(f"   (Hash) ✅ DB에서 {len(rows)}개 종목 시장 데이터 로드")
    except Exception as e:
        logger.warning(f"   (Hash) ⚠️ DB 시장 데이터 조회 실패: {e}")
    
    # 2. ChromaDB 뉴스 조회 생략 (속도 최적화)
    # 이유: 해시에 오늘 날짜가 포함되어 있어서 매일 재평가 보장됨
    # 뉴스 데이터는 Phase 1 Hunter에서 개별 종목 평가 시 조회함
    logger.info(f"   (Hash) ✅ 뉴스 날짜 조회 생략 (날짜 기반 캐시 무효화로 대체)")


def _get_latest_news_date(vectorstore, stock_code: str, stock_name: str) -> Optional[str]:
    """ChromaDB에서 종목의 최신 뉴스 날짜 조회"""
    try:
        docs = vectorstore.similarity_search(
            query=f"{stock_name}",
            k=1,
            filter={"stock_code": stock_code}
        )
        if docs and docs[0].metadata:
            # 뉴스 날짜를 YYYY-MM-DD 형식으로 반환
            news_date = docs[0].metadata.get('date') or docs[0].metadata.get('published_at')
            if news_date:
                # 날짜 문자열에서 YYYY-MM-DD만 추출
                return str(news_date)[:10]
    except Exception:
        pass
    return None


def _record_to_cache_payload(record: Dict) -> Dict:
    metadata = record.get("llm_metadata", {})
    return {
        "code": record["code"],
        "name": record["name"],
        "llm_score": record.get("llm_score", 0),
        "llm_reason": record.get("llm_reason", ""),
        "llm_grade": metadata.get("llm_grade"),
        "decision_hash": metadata.get("decision_hash"),
        "llm_updated_at": metadata.get("llm_updated_at"),
        "is_tradable": record.get("is_tradable", True),
        "approved": record.get("approved", False),
    }


def _cache_payload_to_record(entry: Dict, decision_hash: str) -> Dict:
    updated_at = entry.get("llm_updated_at")
    metadata = {
        "llm_grade": entry.get("llm_grade"),
        "decision_hash": decision_hash,
        "llm_updated_at": updated_at,
        "source": "cache",
    }
    return {
        "code": entry["code"],
        "name": entry.get("name", entry["code"]),
        "llm_score": entry.get("llm_score", 0),
        "llm_reason": entry.get("llm_reason", ""),
        "is_tradable": entry.get("is_tradable", True),
        "approved": entry.get("approved", False),
        "llm_metadata": metadata,
    }

# 섹터/테마 분석 함수들은 scout_universe.py에서 import됨
# (analyze_sector_momentum, get_hot_sector_stocks, get_dynamic_blue_chips, get_momentum_stocks)

# 자동 파라미터 최적화 함수들은 scout_optimizer.py에서 import됨
# (run_auto_parameter_optimization, run_simple_backtest, generate_optimized_params, verify_params_with_llm)


def fetch_stock_news_from_chroma(vectorstore, stock_code: str, stock_name: str, k: int = 3) -> str:
    """
    [v3.9] ChromaDB에서 종목별 최신 뉴스 검색
    
    Args:
        vectorstore: ChromaDB vectorstore 인스턴스
        stock_code: 종목 코드
        stock_name: 종목명
        k: 가져올 뉴스 개수
        
    Returns:
        뉴스 요약 문자열 (없으면 "최근 관련 뉴스 없음")
    """
    if not vectorstore:
        return "뉴스 DB 미연결"
    
    try:
        from datetime import datetime, timedelta, timezone
        
        # 최신 7일 이내 뉴스 필터
        recency_timestamp = int((datetime.now(timezone.utc) - timedelta(days=7)).timestamp())
        
        # 종목 코드로 필터링된 뉴스 검색 시도
        try:
            docs = vectorstore.similarity_search(
                query=f"{stock_name} 실적 수주 호재",
                k=k,
                filter={"stock_code": stock_code}
            )
            # logger.debug(f"   (D) [{stock_code}] 필터 검색 결과: {len(docs)}건")
        except Exception:
            # 필터 실패시 종목명으로 검색
            docs = vectorstore.similarity_search(
                query=f"{stock_name} 주식 뉴스",
                k=k
            )
            logger.debug(f"   (D) [{stock_code}] 종목명 검색(Fallback): {len(docs)}건")
            # 종목 관련 뉴스만 필터링
            docs = [d for d in docs if stock_name in d.page_content or stock_code in str(d.metadata)]
        
        if docs:
            news_items = []
            for i, doc in enumerate(docs[:k], 1):
                content = doc.page_content[:100].strip()
                if content:
                    news_items.append(f"[뉴스{i}] {content}")
            
            if news_items:
                return " | ".join(news_items)
        
        return "최근 관련 뉴스 없음"
        
    except Exception as e:
        logger.debug(f"   ⚠️ [{stock_code}] ChromaDB 뉴스 검색 오류: {e}")
        return "뉴스 검색 오류"


# =============================================================================
# [v1.0 Refactored] 파이프라인 태스크 함수들은 scout_pipeline.py로 분리됨
# - is_hybrid_scoring_enabled, process_quant_scoring_task
# - process_phase1_hunter_v5_task, process_phase23_judge_v5_task
# - process_phase1_hunter_task, process_phase23_debate_judge_task
# - process_llm_decision_task, fetch_kis_data_task
# =============================================================================

def main():
    start_time = time.time()
    logger.info("--- 🤖 'Scout Job' [v3.0 Local] 실행 시작 ---")
    
    kis_api = None
    brain = None

    try:
        logger.info("--- [Init] 환경 변수 로드 및 KIS API 연결 시작 ---")
        load_dotenv(override=True)
        
        trading_mode = os.getenv("TRADING_MODE", "REAL")
        use_gateway = os.getenv("USE_KIS_GATEWAY", "true").lower() == "true"
        
        if use_gateway:
            kis_api = KISGatewayClient()
            logger.info("✅ KIS Gateway Client 초기화 완료")
        else:
            kis_api = KIS_API(
                app_key=auth.get_secret(os.getenv(f"{trading_mode}_SECRET_ID_APP_KEY")),
                app_secret=auth.get_secret(os.getenv(f"{trading_mode}_SECRET_ID_APP_SECRET")),
                base_url=os.getenv(f"KIS_BASE_URL_{trading_mode}"),
                account_prefix=auth.get_secret(os.getenv(f"{trading_mode}_SECRET_ID_ACCOUNT_PREFIX")),
                account_suffix=os.getenv("KIS_ACCOUNT_SUFFIX"),
                token_file_path="/app/tokens/kis_token_scout.json",
                trading_mode=trading_mode
            )
            if not kis_api.authenticate():
                raise Exception("KIS API 인증에 실패했습니다.")
        
        brain = JennieBrain(
            project_id=os.getenv("GCP_PROJECT_ID", "local"),
            gemini_api_key_secret=os.getenv("SECRET_ID_GEMINI_API_KEY")
        )
        
        # [v4.3] SQLAlchemy 세션 초기화 (session_scope 사용 전에 호출 필수)
        ensure_engine_initialized()
        
        # [v4.3] SQLAlchemy 세션 사용으로 변경
        with session_scope() as session:
            watchlist_snapshot = database.get_active_watchlist(session)
            
            vectorstore = None
            try:
                logger.info("   ... ChromaDB 클라이언트 연결 시도 (Gemini Embeddings) ...")
                api_key = ensure_gemini_api_key()
                embeddings = GoogleGenerativeAIEmbeddings(
                    model="models/gemini-embedding-001", 
                    google_api_key=api_key
                )
                
                chroma_client = chromadb.HttpClient( # noqa
                    host=CHROMA_SERVER_HOST, 
                    port=CHROMA_SERVER_PORT
                )
                vectorstore = Chroma(
                    client=chroma_client, 
                    collection_name="rag_stock_data", 
                    embedding_function=embeddings
                )
                logger.info("✅ [v3.0] LLM 및 ChromaDB 클라이언트 초기화 완료.")
            except Exception as e:
                logger.warning(f"⚠️ ChromaDB 초기화 실패 (RAG 기능 비활성화): {e}")
                vectorstore = None

            # Phase 1: 트리플 소스 후보 발굴 (v3.8: 섹터 분석 추가)
            logger.info("--- [Phase 1] 트리플 소스 후보 발굴 시작 ---")
            update_pipeline_status(phase=1, phase_name="Hunter Scout", status="running", progress=0)
            candidate_stocks = {}

            # A: 동적 우량주 (KOSPI 200 기준)
            universe_size = int(os.getenv("SCOUT_UNIVERSE_SIZE", "200"))
            for stock in get_dynamic_blue_chips(limit=universe_size):
                candidate_stocks[stock['code']] = {'name': stock['name'], 'reasons': ['KOSPI 시총 상위']}
            
            # E: 섹터 모멘텀 분석 (v3.8 신규)
            sector_analysis = analyze_sector_momentum(kis_api, session, watchlist_snapshot)
            hot_sector_stocks = get_hot_sector_stocks(sector_analysis, top_n=30)
            for stock in hot_sector_stocks:
                if stock['code'] not in candidate_stocks:
                    candidate_stocks[stock['code']] = {
                        'name': stock['name'], 
                        'reasons': [f"핫 섹터 ({stock['sector']}, +{stock['sector_momentum']:.1f}%)"]
                    }
                else:
                    candidate_stocks[stock['code']]['reasons'].append(
                        f"핫 섹터 ({stock['sector']}, +{stock['sector_momentum']:.1f}%)"
                    )

            # B: 정적 우량주
            for stock in BLUE_CHIP_STOCKS:
                if stock['code'] not in candidate_stocks:
                    candidate_stocks[stock['code']] = {'name': stock['name'], 'reasons': ['정적 우량주']}

            # C: RAG
            if vectorstore:
                try:
                    logger.info("   (C) RAG 기반 후보 발굴 중...")
                    rag_results = vectorstore.similarity_search(query="실적 호재 계약 수주", k=50)
                    for doc in rag_results:
                        stock_code = doc.metadata.get('stock_code')
                        stock_name = doc.metadata.get('stock_name')
                        if stock_code and stock_name:
                            if stock_code not in candidate_stocks:
                                candidate_stocks[stock_code] = {'name': stock_name, 'reasons': []}
                            candidate_stocks[stock_code]['reasons'].append(f"RAG 포착: {doc.page_content[:20]}...")
                except Exception as e:
                    logger.warning(f"   (C) RAG 검색 실패: {e}")

            # D: 모멘텀
            logger.info("   (D) 모멘텀 팩터 기반 종목 발굴 중...")
            momentum_stocks = get_momentum_stocks(
                    kis_api,
                    session,
                period_months=6,
                top_n=30,
                watchlist_snapshot=watchlist_snapshot
            )
            for stock in momentum_stocks:
                if stock['code'] not in candidate_stocks:
                    candidate_stocks[stock['code']] = {
                        'name': stock['name'], 
                        'reasons': [f'모멘텀 ({stock["momentum"]:.1f}%)']
                    }
            
            logger.info(f"   ✅ 후보군 {len(candidate_stocks)}개 발굴 완료.")

            # [v4.1] 해시 계산 전에 시장 데이터 추가 (가격, 거래량)
            logger.info("--- [Phase 1.5] 시장 데이터 기반 해시 계산 ---")
            enrich_candidates_with_market_data(candidate_stocks, session, vectorstore)
            
            # [v4.2] Phase 1 시작 전에 모든 데이터 일괄 조회 (병렬 스레드 안 API 호출 제거)
            logger.info("--- [Phase 1.6] 데이터 사전 조회 (스냅샷/뉴스) ---")
            snapshot_cache, news_cache = prefetch_all_data(candidate_stocks, kis_api, vectorstore)

            # [v4.3] 뉴스 해시를 candidate_stocks에 반영 (해시 계산에 포함)
            # 뉴스 내용이 바뀌면 해시가 달라져 LLM 재호출됨
            news_hash_count = 0
            for code, news in news_cache.items():
                if code in candidate_stocks and news and news not in [
                    "뉴스 DB 미연결", "최근 관련 뉴스 없음", "뉴스 검색 오류", 
                    "뉴스 조회 실패", "뉴스 캐시 없음"
                ]:
                    # 뉴스 내용의 MD5 해시 (시간 정보 포함되어 있음)
                    candidate_stocks[code]['news_hash'] = hashlib.md5(news.encode()).hexdigest()[:16]
                    news_hash_count += 1
            logger.info(f"   (Hash) ✅ 뉴스 해시 {news_hash_count}개 반영 완료")

            # [v4.0] Phase 1.8: 수급 데이터(Market Flow) 분석 및 기록
            logger.info("--- [Phase 1.8] 수급 데이터(Market Flow) 분석 (Foreign/Institution) ---")
            
            # [Optimization] 병렬로 투자자 동향 조회
            investor_flow_cache = {}
            
            # Archivist 초기화 (여기서도 사용)
            if 'archivist' not in locals():
                archivist = Archivist(session_scope)
                
            def process_flow_data(code):
                try:
                    # 최근 1일치(오늘/어제) 데이터만 조회하여 현재 수급 확인
                    # 장 중이면 오늘 잠정치/확정치, 장 마감 후면 오늘 확정치
                    trends = kis_api.get_market_data().get_investor_trend(code, start_date=None, end_date=None)
                    if not trends:
                        return code, None
                    
                    # 가장 최근 데이터 (오늘)
                    latest = trends[-1]
                    return code, latest
                except Exception as e:
                    return code, None

            with ThreadPoolExecutor(max_workers=8) as executor:
                futures = [executor.submit(process_flow_data, code) for code in candidate_stocks.keys()]
                for future in as_completed(futures):
                    code, flow_data = future.result()
                    if flow_data:
                        investor_flow_cache[code] = flow_data
                        
                        # 후보군 정보에 수급 데이터 추가 (LLM 프롬프트용)
                        candidate_stocks[code]['market_flow'] = {
                            'foreign_net_buy': flow_data['foreigner_net_buy'],
                            'institution_net_buy': flow_data['institution_net_buy'],
                            'individual_net_buy': flow_data['individual_net_buy']
                        }
                        
                        # Archivist에 기록 (Market Flow Snapshot)
                        try:
                            # flow_data는 dict 형태 (date, price, foreign..., institution...)
                            # Archivist.log_market_flow_snapshot은 stock_code를 포함한 dict를 기대함
                            log_payload = flow_data.copy()
                            log_payload['stock_code'] = code
                            # volume 필드가 get_investor_trend 결과에 없으므로 (필요시) 보완
                            # log_payload['volume'] = ... 
                            
                            archivist.log_market_flow_snapshot(log_payload)
                        except Exception as log_e:
                            logger.warning(f"Failed to log market flow for {code}: {log_e}")

            logger.info(f"   (Flow) ✅ 수급 데이터 {len(investor_flow_cache)}개 종목 분석 및 기록 완료")

            # Phase 2: LLM 최종 선정
            logger.info("--- [Phase 2] LLM 기반 최종 Watchlist 선정 시작 ---")
            update_pipeline_status(
                phase=1, phase_name="Hunter Scout", status="running", 
                total_candidates=len(candidate_stocks)
            )
            
            # =============================================================
            # [v1.0] 하이브리드 스코어링 모드 분기
            # =============================================================
            if is_hybrid_scoring_enabled():
                logger.info("=" * 60)
                logger.info("   🚀 Scout v5 Hybrid Scoring Mode 활성화!")
                logger.info("=" * 60)
                
                try:
                    from shared.hybrid_scoring import (
                        QuantScorer, HybridScorer, 
                        create_hybrid_scoring_tables,
                        format_quant_score_for_prompt,
                    )
                    from shared.market_regime import MarketRegimeDetector
                    
                    # DB 테이블 생성 확인
                    create_hybrid_scoring_tables(session)
                    
                    # 시장 국면 감지
                    kospi_prices = database.get_daily_prices(session, "0001", limit=60)
                    if not kospi_prices.empty:
                        detector = MarketRegimeDetector()
                        current_regime, _ = detector.detect_regime(kospi_prices, float(kospi_prices['CLOSE_PRICE'].iloc[-1]), quiet=True)
                    else:
                        current_regime = "SIDEWAYS"
                    
                    logger.info(f"   현재 시장 국면: {current_regime}")
                    
                    # QuantScorer 초기화
                    quant_scorer = QuantScorer(session, market_regime=current_regime)
                    
                    # Step 1: 정량 점수 계산 (LLM 호출 없음, 비용 0원)
                    logger.info(f"\n   [v5 Step 1] 정량 점수 계산 ({len(candidate_stocks)}개 종목) - 비용 0원")
                    quant_results = {}
                    
                    for code, info in candidate_stocks.items():
                        if code == '0001':
                            continue
                        stock_info = {
                            'code': code,
                            'info': info,
                            'snapshot': snapshot_cache.get(code),
                        }
                        quant_results[code] = process_quant_scoring_task(
                            stock_info, quant_scorer, session, kospi_prices
                        )
                    
                    # Step 2: 정량 기반 1차 필터링 (하위 20% 탈락) - [v1.1] 필터링 완화
                    logger.info(f"\n   [v5 Step 2] 정량 기반 1차 필터링 (하위 20% 탈락)")
                    quant_result_list = list(quant_results.values())
                    filtered_results = quant_scorer.filter_candidates(quant_result_list, cutoff_ratio=0.2)
                    
                    filtered_codes = {r.stock_code for r in filtered_results}
                    logger.info(f"   ✅ 정량 필터 통과: {len(filtered_codes)}개 (평균 점수: {sum(r.total_score for r in filtered_results)/len(filtered_results):.1f}점)")
                    
                    # Step 3: LLM 정성 분석 (통과 종목만)
                    logger.info(f"\n   [v5 Step 3] LLM 정성 분석 (통계 컨텍스트 포함)")
                    
                    final_approved_list: List[Dict] = []
                    if '0001' in candidate_stocks:
                        final_approved_list.append({'code': '0001', 'name': 'KOSPI', 'is_tradable': False})
                    
                    llm_decision_records: Dict[str, Dict] = {}
                    llm_max_workers = max(1, _parse_int_env(os.getenv("SCOUT_LLM_MAX_WORKERS"), 4))
                    
                    # Phase 1: Hunter (통계 컨텍스트 포함)
                    phase1_results = []
                    # [v6.0] Archivist 초기화 (Phase 1/2 공용)
                    archivist = Archivist(session_scope)

                    with ThreadPoolExecutor(max_workers=llm_max_workers) as executor:
                        future_to_code = {}
                        for code in filtered_codes:
                            info = candidate_stocks[code]
                            quant_result = quant_results[code]
                            payload = {'code': code, 'info': info}
                            future = executor.submit(
                                process_phase1_hunter_v5_task, 
                                payload, brain, quant_result, snapshot_cache, news_cache, archivist
                            )
                            future_to_code[future] = code
                        
                        for future in as_completed(future_to_code):
                            result = future.result()
                            if result:
                                phase1_results.append(result)
                                if not result['passed']:
                                    llm_decision_records[result['code']] = {
                                        'code': result['code'],
                                        'name': result['name'],
                                        'llm_score': result['hunter_score'],
                                        'llm_reason': result['hunter_reason'],
                                        'is_tradable': False,
                                        'approved': False,
                                        'hunter_score': result['hunter_score'],
                                        'llm_metadata': {'llm_grade': 'D', 'source': 'v5_hunter_reject'}
                                    }
                    
                    phase1_passed = [r for r in phase1_results if r['passed']]
                    logger.info(f"   ✅ v5 Hunter 통과: {len(phase1_passed)}/{len(filtered_codes)}개")
                    
                    # Phase 2-3: Debate + Judge (상위 종목만)
                    PHASE2_MAX = int(os.getenv("SCOUT_PHASE2_MAX_ENTRIES", "50"))
                    if len(phase1_passed) > PHASE2_MAX:
                        phase1_passed_sorted = sorted(phase1_passed, key=lambda x: x['hunter_score'], reverse=True)
                        phase1_passed = phase1_passed_sorted[:PHASE2_MAX]
                    
                    if phase1_passed:
                        logger.info(f"\n   [v5 Step 4] Debate + Judge (하이브리드 점수 결합)")
                        
                        with ThreadPoolExecutor(max_workers=llm_max_workers) as executor:
                            future_to_code = {}
                            
                            # [v6.0] Archivist 사용 (위에서 초기화됨)

                            for p1_result in phase1_passed:
                                future = executor.submit(
                                    process_phase23_judge_v5_task, 
                                    p1_result, brain, archivist, current_regime
                                )
                                future_to_code[future] = p1_result['code']
                            
                            for future in as_completed(future_to_code):
                                record = future.result()
                                if record:
                                    llm_decision_records[record['code']] = record
                                    if record.get('approved'):
                                        final_approved_list.append(_record_to_watchlist_entry(record))
                    
                    logger.info(f"   ✅ v5 최종 승인: {len([r for r in llm_decision_records.values() if r.get('approved')])}개")
                    
                    # 쿼터제 적용
                    MAX_WATCHLIST_SIZE = 15
                    if len(final_approved_list) > MAX_WATCHLIST_SIZE:
                        final_approved_list_sorted = sorted(
                            final_approved_list,
                            key=lambda x: x.get('llm_score', 0),
                            reverse=True
                        )
                        final_approved_list = final_approved_list_sorted[:MAX_WATCHLIST_SIZE]
                    
                    logger.info(f"\n   🏁 Scout v1.0 완료: {len(final_approved_list)}개 종목 선정")
                    _v5_completed = True
                    
                except Exception as e:
                    logger.error(f"❌ Scout v1.0 실행 오류, v4 모드로 폴백: {e}", exc_info=True)
                    _v5_completed = False
            else:
                _v5_completed = False
            
            # =============================================================
            # [v4.x] 기존 LLM 기반 선정 로직 (v5 미활성화 또는 실패 시)
            # =============================================================
            if not _v5_completed:
                logger.info("   (Mode) v4.x 기존 LLM 기반 로직 실행")
                
                # [v4.3] 새로운 캐시 시스템 - LLM_EVAL_CACHE 테이블 기반 직접 비교 (db_conn 사용)
                llm_cache_snapshot = _load_llm_cache_from_db(session)
                llm_max_workers = max(1, _parse_int_env(os.getenv("SCOUT_LLM_MAX_WORKERS"), 4))
    
                # 오늘 날짜 (KST 기준)
                kst = timezone(timedelta(hours=9))
                today_str = datetime.now(kst).strftime("%Y-%m-%d")
    
                final_approved_list: List[Dict] = []
                if '0001' in candidate_stocks:
                    final_approved_list.append({'code': '0001', 'name': 'KOSPI', 'is_tradable': False})
                    del candidate_stocks['0001']
    
                llm_decision_records: Dict[str, Dict] = {}
                cache_hits = 0
                pending_codes: List[str] = []
                cache_miss_reasons: Dict[str, str] = {}  # 디버깅용
    
                for code, info in candidate_stocks.items():
                    cached = llm_cache_snapshot.get(code)
                    
                    # [v4.3] 직접 비교로 캐시 유효성 검증
                    current_data = {
                        'price_bucket': _get_price_bucket(info.get('price', 0)),
                        'volume_bucket': _get_volume_bucket(info.get('volume', 0)),
                        'news_hash': info.get('news_hash'),
                    }
                    
                    if _is_cache_valid_direct(cached, current_data, today_str):
                        # 캐시 적중 - 이전 LLM 결과 재사용
                        llm_decision_records[code] = {
                            'code': code,
                            'name': info['name'],
                            'llm_score': cached.get('judge_score') or cached.get('hunter_score', 0),
                            'llm_reason': cached.get('llm_reason', ''),
                            'is_tradable': cached.get('is_tradable', False),
                            'approved': cached.get('is_approved', False),
                            'llm_metadata': {
                                'llm_grade': cached.get('llm_grade'),
                                'source': 'cache',
                            }
                        }
                        cache_hits += 1
                        if cached.get('is_approved'):
                            final_approved_list.append(_record_to_watchlist_entry(llm_decision_records[code]))
                    else:
                        # 캐시 미스 - LLM 재호출 필요
                        pending_codes.append(code)
                        # 미스 원인 기록 (디버깅용)
                        if not cached:
                            cache_miss_reasons[code] = "no_cache"
                        elif cached.get('eval_date') != today_str:
                            cache_miss_reasons[code] = f"date({cached.get('eval_date')}!={today_str})"
                        elif cached.get('price_bucket') != current_data['price_bucket']:
                            cache_miss_reasons[code] = f"price({cached.get('price_bucket')}!={current_data['price_bucket']})"
                        elif (cached.get('news_hash') or '') != (current_data.get('news_hash') or ''):
                            cache_miss_reasons[code] = "news_changed"
    
                if cache_hits:
                    logger.info(f"   (LLM) ✅ 캐시 적중 {cache_hits}건 (오늘 날짜 + 동일 가격/뉴스)")
                
                if pending_codes:
                    # 캐시 미스 원인 분석
                    reason_counts = {}
                    for reason in cache_miss_reasons.values():
                        reason_type = reason.split("(")[0]
                        reason_counts[reason_type] = reason_counts.get(reason_type, 0) + 1
                    logger.info(f"   (LLM) ⚠️ 캐시 미스 {len(pending_codes)}건 - 원인: {reason_counts}")
    
                need_llm_calls = len(pending_codes) > 0
    
                llm_invocation_count = 0
                if need_llm_calls:
                    if brain is None:
                        logger.error("   (LLM) JennieBrain 초기화 실패로 신규 호출을 수행할 수 없습니다.")
                    else:
                        # [v3.8] 2-Pass 병렬 처리 최적화
                        # Pass 1: Phase 1 Hunter (Gemini-Flash) - 병렬로 빠르게 필터링
                        logger.info(f"   (LLM) [Pass 1] Phase 1 Hunter 병렬 실행 시작 ({len(pending_codes)}개 종목)")
                        update_pipeline_status(
                            phase=1, phase_name="Hunter Scout", status="running",
                            total_candidates=len(candidate_stocks)
                        )
                        phase1_start = time.time()
                        
                        phase1_results = []
                        # [v4.1] Claude Rate Limit 대응: 워커 수 제한 (기존 *2 제거)
                        phase1_worker_count = min(llm_max_workers, max(1, len(pending_codes)))
                        logger.info(f"   (LLM) Phase 1 워커 수: {phase1_worker_count}개 (Rate Limit 대응)")
                        
                        with ThreadPoolExecutor(max_workers=phase1_worker_count) as executor:
                            future_to_code = {}
                            for code in pending_codes:
                                payload = {
                                    'code': code,
                                    'info': candidate_stocks[code],
                                }
                                # [v4.2] 캐시에서 데이터 조회하도록 변경 (API 호출 X)
                                future = executor.submit(process_phase1_hunter_task, payload, brain, snapshot_cache, news_cache)
                                future_to_code[future] = code
                            
                            for future in as_completed(future_to_code):
                                result = future.result()
                                if result:
                                    phase1_results.append(result)
                                    # Phase 1 탈락 종목도 기록 (캐시용)
                                    if not result['passed']:
                                        llm_decision_records[result['code']] = {
                                            'code': result['code'],
                                            'name': result['name'],
                                            'is_tradable': False,
                                            'llm_score': result['hunter_score'],
                                            'llm_reason': result['hunter_reason'] or 'Phase 1 필터링 탈락',
                                            'approved': False,
                                            'hunter_score': result['hunter_score'],  # [v4.3] 캐시 저장용
                                            'llm_metadata': {
                                                'llm_grade': 'D',
                                                'llm_updated_at': _utcnow().isoformat(),
                                                'source': 'llm_hunter_reject',
                                            }
                                        }
                        
                        phase1_passed_all = [r for r in phase1_results if r['passed']]
                        phase1_time = time.time() - phase1_start
                        logger.info(f"   (LLM) [Pass 1] Phase 1 완료: {len(phase1_passed_all)}/{len(pending_codes)}개 통과 ({phase1_time:.1f}초)")
                        
                        # [v4.1] Phase 2 진입 제한: 상위 50개만 (속도 최적화)
                        PHASE2_MAX_ENTRIES = int(os.getenv("SCOUT_PHASE2_MAX_ENTRIES", "50"))
                        if len(phase1_passed_all) > PHASE2_MAX_ENTRIES:
                            phase1_passed_sorted = sorted(phase1_passed_all, key=lambda x: x['hunter_score'], reverse=True)
                            phase1_passed = phase1_passed_sorted[:PHASE2_MAX_ENTRIES]
                            logger.info(f"   (LLM) [속도 최적화] Phase 2 진입 제한: 상위 {PHASE2_MAX_ENTRIES}개만 선택 (전체 {len(phase1_passed_all)}개 중)")
                        else:
                            phase1_passed = phase1_passed_all
                        
                        # [v1.0] Redis 상태 업데이트 - Phase 1 완료
                        update_pipeline_status(
                            phase=2, phase_name="Bull vs Bear Debate", status="running",
                            total_candidates=len(candidate_stocks),
                            passed_phase1=len(phase1_passed_all)  # 전체 통과 수 표시
                        )
                        
                        # Pass 2: Phase 2-3 Debate+Judge (GPT-5-mini) - 상위 종목만
                        if phase1_passed:
                            logger.info(f"   (LLM) [Pass 2] Phase 2-3 Debate-Judge 실행 ({len(phase1_passed)}개 종목)")
                            phase23_start = time.time()
                            
                            phase23_worker_count = min(llm_max_workers, max(1, len(phase1_passed)))
                            
                            with ThreadPoolExecutor(max_workers=phase23_worker_count) as executor:
                                future_to_code = {}
                                for phase1_result in phase1_passed:
                                    future = executor.submit(process_phase23_debate_judge_task, phase1_result, brain)
                                    future_to_code[future] = phase1_result['code']
                                
                                for future in as_completed(future_to_code):
                                    record = future.result()
                                    if not record:
                                        continue
                                    llm_invocation_count += 1
                                    llm_decision_records[record['code']] = record
                                    if record.get('approved'):
                                        final_approved_list.append(_record_to_watchlist_entry(record))
                            
                            phase23_time = time.time() - phase23_start
                            logger.info(f"   (LLM) [Pass 2] Phase 2-3 완료 ({phase23_time:.1f}초)")
                            
                            # [v1.0] Redis 상태 업데이트 - Phase 2-3 완료
                            update_pipeline_status(
                                phase=3, phase_name="Final Judge", status="running",
                                total_candidates=len(candidate_stocks),
                                passed_phase1=len(phase1_passed),
                                passed_phase2=len(phase1_passed),  # Debate은 전원 참여
                                final_selected=len(final_approved_list)
                            )
                        else:
                            logger.info("   (LLM) [Pass 2] Phase 1 통과 종목 없음, Phase 2-3 건너뜀")
                else:
                    logger.info("   (LLM) 모든 후보가 캐시로 충족되어 신규 호출이 없습니다.")
    
                logger.info("   (LLM) 신규 호출 수: %d", llm_invocation_count)
    
                # [v4.3] 새로운 캐시 테이블에 결과 저장
                if llm_invocation_count > 0:
                    new_cache_entries = {}
                    for code, record in llm_decision_records.items():
                        info = candidate_stocks.get(code, {})
                        new_cache_entries[code] = {
                            'stock_name': record.get('name', ''),
                            'price_bucket': _get_price_bucket(info.get('price', 0)),
                            'volume_bucket': _get_volume_bucket(info.get('volume', 0)),
                            'news_hash': info.get('news_hash'),
                            'eval_date': today_str,
                            'hunter_score': record.get('hunter_score', record.get('llm_score', 0)),
                            'judge_score': record.get('llm_score', 0),
                            'llm_grade': record.get('llm_metadata', {}).get('llm_grade'),
                            'llm_reason': record.get('llm_reason', '')[:60000] if record.get('llm_reason') else None,
                            'news_used': news_cache.get(code, '')[:60000] if news_cache.get(code) else None,
                            'is_approved': record.get('approved', False),
                            'is_tradable': record.get('is_tradable', False),
                        }
                    _save_llm_cache_batch(session, new_cache_entries)
                    _save_last_llm_run_at(session, _utcnow())
    
                # [v1.0] Phase 3: 쿼터제 적용 (Top 15개만 저장) - 제니 피드백 반영
                MAX_WATCHLIST_SIZE = 15
                
                # 점수 기준 내림차순 정렬 후 상위 N개만 선택
                if len(final_approved_list) > MAX_WATCHLIST_SIZE:
                    final_approved_list_sorted = sorted(
                        final_approved_list, 
                        key=lambda x: x.get('llm_score', 0), 
                        reverse=True
                    )
                    final_approved_list = final_approved_list_sorted[:MAX_WATCHLIST_SIZE]
                    logger.info(f"   (쿼터제) 상위 {MAX_WATCHLIST_SIZE}개만 선정 (총 {len(final_approved_list_sorted)}개 중)")
            
            # =============================================================
            # [공통] Phase 3: 최종 Watchlist 저장
            # =============================================================
            logger.info(f"--- [Phase 3] 최종 Watchlist {len(final_approved_list)}개 저장 ---")
            database.save_to_watchlist(session, final_approved_list)
            
            with ThreadPoolExecutor(max_workers=10) as executor:
                if hasattr(kis_api, 'API_CALL_DELAY'):
                    future_to_data = {
                        executor.submit(fetch_kis_data_task, s, kis_api): (time.sleep(kis_api.API_CALL_DELAY), s)[1]
                        for s in final_approved_list 
                    }
                else:
                    future_to_data = {
                        executor.submit(fetch_kis_data_task, s, kis_api): s
                        for s in final_approved_list 
                    }
                
                all_daily = []
                all_fund = []
                for future in as_completed(future_to_data):
                    d, f = future.result()
                    if d: all_daily.extend(d)
                    if f: all_fund.append(f)
            
            if all_daily: database.save_all_daily_prices(session, all_daily)
            if all_fund: database.update_all_stock_fundamentals(session, all_fund)
            
            # Phase 3-A: 재무 데이터 (네이버 크롤링)
            tradable_codes = [s['code'] for s in final_approved_list if s.get('is_tradable', True)]
            if tradable_codes:
                batch_update_watchlist_financial_data(session, tradable_codes)
            
            # [v1.0] Redis 최종 상태 업데이트 - 완료
            update_pipeline_status(
                phase=3, phase_name="Final Judge", status="completed",
                progress=100,
                total_candidates=len(candidate_stocks) if 'candidate_stocks' in locals() else 0,
                passed_phase1=len(phase1_passed) if 'phase1_passed' in locals() else 0,
                passed_phase2=len(phase1_passed) if 'phase1_passed' in locals() else 0,
                final_selected=len(final_approved_list)
            )
            
            # [v1.0] Redis 결과 저장 (Dashboard에서 조회용)
            pipeline_results = [
                {
                    "stock_code": s.get('code'),
                    "stock_name": s.get('name'),
                    "grade": s.get('llm_metadata', {}).get('llm_grade', 'C'),
                    "final_score": s.get('llm_score', 0),
                    "selected": s.get('approved', False),
                    "judge_reason": s.get('llm_reason', ''),
                }
                for s in final_approved_list
            ]
            save_pipeline_results(pipeline_results)
            logger.info(f"   (Redis) Dashboard용 결과 저장 완료 ({len(pipeline_results)}개)")

    except Exception as e:
        logger.critical(f"❌ 'Scout Job' 실행 중 오류: {e}", exc_info=True)
        # [v1.0] 오류 시 Redis 상태 업데이트
        update_pipeline_status(phase=0, phase_name="Error", status="error")
            
    logger.info(f"--- 🤖 'Scout Job' 종료 (소요: {time.time() - start_time:.2f}초) ---")

if __name__ == "__main__":
    main()
