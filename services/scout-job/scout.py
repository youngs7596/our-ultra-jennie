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
    level=logging.INFO,
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
from shared.kis import KISClient as KIS_API
from shared.kis.gateway_client import KISGatewayClient
from shared.llm import JennieBrain
from shared.financial_data_collector import batch_update_watchlist_financial_data
from shared.gemini import ensure_gemini_api_key  # [v3.0] Local Gemini Auth 추가

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


def enrich_candidates_with_market_data(candidate_stocks: Dict[str, Dict], db_conn, vectorstore) -> None:
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
        cursor = db_conn.cursor()
        placeholders = ','.join(['%s'] * len(stock_codes))
        
        # 최신 날짜의 데이터만 조회 (가격, 거래량)
        # Note: foreign_net_buy는 아직 테이블에 없으므로 제외
        query = f"""
            SELECT STOCK_CODE, CLOSE_PRICE, VOLUME, PRICE_DATE
            FROM STOCK_DAILY_PRICES_3Y
            WHERE STOCK_CODE IN ({placeholders})
            AND PRICE_DATE = (
                SELECT MAX(PRICE_DATE) FROM STOCK_DAILY_PRICES_3Y AS sub 
                WHERE sub.STOCK_CODE = STOCK_DAILY_PRICES_3Y.STOCK_CODE
            )
        """
        cursor.execute(query, stock_codes)
        rows = cursor.fetchall()
        
        for row in rows:
            code = row['STOCK_CODE'] if isinstance(row, dict) else row[0]
            price = row['CLOSE_PRICE'] if isinstance(row, dict) else row[1]
            volume = row['VOLUME'] if isinstance(row, dict) else row[2]
            
            if code in candidate_stocks:
                candidate_stocks[code]['price'] = float(price) if price else 0
                candidate_stocks[code]['volume'] = int(volume) if volume else 0
        
        cursor.close()
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

# =============================================================================
# 섹터/테마 분석 기능 (v3.8)
# =============================================================================

# 섹터 분류 (KOSPI 주요 섹터)
SECTOR_MAPPING = {
    # 반도체/IT
    '005930': '반도체', '000660': '반도체', '009150': '반도체', '034220': '반도체',
    '066570': 'IT/전자', '018260': 'IT/전자', '017670': 'IT/통신', '030200': 'IT/통신',
    # 자동차
    '005380': '자동차', '000270': '자동차', '012330': '자동차', '086280': '자동차', '018880': '자동차',
    # 배터리/에너지
    '373220': '배터리', '006400': '배터리', '051910': '화학', '096770': '에너지', '010950': '에너지',
    '003670': '배터리', '361610': '배터리',
    # 바이오/헬스케어
    '207940': '바이오', '068270': '바이오', '302440': '바이오', '326030': '바이오',
    # 인터넷/플랫폼
    '035420': '인터넷', '035720': '인터넷', '323410': '인터넷', '377300': '인터넷',
    # 금융
    '105560': '금융', '055550': '금융', '086790': '금융', '316140': '금융', '032830': '금융', '024110': '금융', '000810': '금융',
    # 철강/소재
    '005490': '철강', '010130': '철강', '011170': '화학',
    # 게임/엔터
    '259960': '게임', '036570': '게임', '251270': '게임', '352820': '엔터',
    # 유통/소비재
    '051900': '소비재', '090430': '소비재', '033780': '소비재',
    # 건설/인프라
    '028260': '건설', '015760': '인프라', '009540': '조선',
    # 지주회사
    '034730': '지주', '003550': '지주',
}

def analyze_sector_momentum(kis_api, db_conn, watchlist_snapshot=None):
    """
    [v3.8] 섹터별 모멘텀 분석
    각 섹터의 평균 수익률을 계산하여 핫 섹터를 식별합니다.
    
    Returns:
        dict: {섹터명: {'momentum': float, 'stocks': list, 'avg_return': float}}
    """
    logger.info("   (E) 섹터별 모멘텀 분석 시작...")
    
    sector_data = {}
    
    try:
        # KOSPI 200 종목 가져오기
        if FDR_AVAILABLE:
            df_kospi = fdr.StockListing('KOSPI')
            top_200 = df_kospi.head(200) if len(df_kospi) > 200 else df_kospi
            
            for _, row in top_200.iterrows():
                code = str(row.get('Code', row.get('Symbol', ''))).zfill(6)
                name = row.get('Name', row.get('종목명', ''))
                
                # 섹터 분류
                sector = SECTOR_MAPPING.get(code, '기타')
                
                if sector not in sector_data:
                    sector_data[sector] = {'stocks': [], 'returns': []}
                
                # 최근 수익률 계산 (변동률 % 사용)
                try:
                    # [v1.0 Fix] Changes는 금액, ChagesRatio/ChangesRatio가 %
                    # FinanceDataReader 버전에 따라 컬럼명이 다를 수 있음
                    change_pct = row.get('ChagesRatio') or row.get('ChangesRatio') or row.get('ChangeRatio')
                    
                    if change_pct is None:
                        # Changes(금액)를 Close(종가)로 나눠서 % 계산
                        changes = float(row.get('Changes', 0))
                        close = float(row.get('Close', row.get('Price', 1)))
                        if close > 0:
                            change_pct = (changes / close) * 100
                        else:
                            change_pct = 0
                    else:
                        change_pct = float(change_pct)
                    
                    # 비정상적인 값 필터링 (±50% 초과는 무시)
                    if abs(change_pct) > 50:
                        continue
                    
                    sector_data[sector]['stocks'].append({'code': code, 'name': name})
                    sector_data[sector]['returns'].append(change_pct)
                except (ValueError, TypeError):
                    continue
        
        # 섹터별 평균 수익률 계산
        hot_sectors = {}
        for sector, data in sector_data.items():
            if data['returns']:
                avg_return = sum(data['returns']) / len(data['returns'])
                hot_sectors[sector] = {
                    'avg_return': avg_return,
                    'stock_count': len(data['stocks']),
                    'stocks': data['stocks'][:5],  # 상위 5개 종목만
                }
        
        # 수익률 기준 정렬
        sorted_sectors = sorted(hot_sectors.items(), key=lambda x: x[1]['avg_return'], reverse=True)
        
        logger.info(f"   (E) ✅ 섹터 분석 완료. 핫 섹터 TOP 3:")
        for i, (sector, info) in enumerate(sorted_sectors[:3]):
            logger.info(f"       {i+1}. {sector}: 평균 수익률 {info['avg_return']:.2f}%")
        
        return dict(sorted_sectors)
        
    except Exception as e:
        logger.warning(f"   (E) ⚠️ 섹터 분석 실패: {e}")
        return {}


def get_hot_sector_stocks(sector_analysis, top_n=30):
    """
    [v3.8] 핫 섹터의 종목들을 우선 후보로 반환
    상위 3개 섹터의 종목들을 반환합니다.
    """
    if not sector_analysis:
        return []
    
    hot_stocks = []
    sorted_sectors = list(sector_analysis.items())[:3]  # 상위 3개 섹터
    
    for sector, info in sorted_sectors:
        for stock in info.get('stocks', []):
            hot_stocks.append({
                'code': stock['code'],
                'name': stock['name'],
                'sector': sector,
                'sector_momentum': info['avg_return'],
            })
    
    return hot_stocks[:top_n]


def get_dynamic_blue_chips(limit=200):
    """
    KOSPI 시가총액 상위 종목을 수집합니다. (KOSPI 200 기준)
    
    1차: FinanceDataReader 사용 (안정적, 시가총액 순 정렬)
    2차: 네이버 금융 스크래핑 (폴백)
    
    Args:
        limit: 수집할 종목 수 (기본값: 200, KOSPI 200 기준)
    """
    # 1차 시도: FinanceDataReader (권장)
    if FDR_AVAILABLE:
        try:
            logger.info(f"   (A) FinanceDataReader로 KOSPI 시총 상위 {limit}개 조회 중...")
            
            # KOSPI 전체 종목 조회
            df_kospi = fdr.StockListing('KOSPI')
            
            # 시가총액 기준 정렬 (Marcap 컬럼)
            if 'Marcap' in df_kospi.columns:
                df_sorted = df_kospi.sort_values('Marcap', ascending=False).head(limit)
            elif 'Market' in df_kospi.columns:
                # Marcap이 없으면 그냥 상위 N개 (이미 시총순일 수 있음)
                df_sorted = df_kospi.head(limit)
            else:
                df_sorted = df_kospi.head(limit)
            
            dynamic_list = []
            for _, row in df_sorted.iterrows():
                code = str(row.get('Code', row.get('Symbol', ''))).zfill(6)
                name = row.get('Name', row.get('종목명', ''))
                if code and name:
                    dynamic_list.append({'code': code, 'name': name})
            
            logger.info(f"   (A) ✅ FinanceDataReader로 {len(dynamic_list)}개 종목 로드 완료. (KOSPI 시총 상위)")
            return dynamic_list
            
        except Exception as e:
            logger.warning(f"   (A) ⚠️ FinanceDataReader 실패, 네이버 금융으로 폴백: {e}")
    
    # 2차 시도: 네이버 금융 스크래핑 (폴백)
    logger.info(f"   (A) 네이버 금융에서 KOSPI 시가총액 상위 {limit}개 스크래핑 시도...")
    dynamic_list = []
    seen_codes = set()
    
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        base_url = "https://finance.naver.com/sise/sise_market_sum.naver?sosok=0"
        
        # 네이버 금융은 페이지당 50개씩 표시
        pages_needed = (limit + 49) // 50
        
        for page in range(1, pages_needed + 1):
            if len(dynamic_list) >= limit:
                break
                
            url = f"{base_url}&page={page}"
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            table = soup.find('table', class_='type_2')
            if not table:
                logger.warning(f"   (A) ⚠️ 페이지 {page} 테이블을 찾지 못했습니다.")
                continue
                
            rows = table.find_all('tr')
            page_count = 0
            for row in rows:
                if len(dynamic_list) >= limit:
                    break
                
                cols = row.find_all('td')
                if len(cols) > 1 and cols[0].text.strip().isdigit():
                    a_tag = cols[1].find('a')
                    if a_tag and 'href' in a_tag.attrs and 'code=' in a_tag['href']:
                        code = a_tag['href'].split('code=')[1]
                        name = a_tag.text.strip()
                        
                        if code not in seen_codes:
                            seen_codes.add(code)
                            dynamic_list.append({'code': code, 'name': name})
                            page_count += 1
            
            logger.debug(f"   (A) 페이지 {page}: {page_count}개 추가 (누적: {len(dynamic_list)}개)")
            
            if page < pages_needed:
                time.sleep(0.3)
        
        logger.info(f"   (A) ✅ 네이버 금융에서 {len(dynamic_list)}개 스크래핑 완료.")
    except Exception as e:
        logger.error(f"   (A) ❌ 동적 우량주 스크래핑 중 오류 발생: {e}")
    
    return dynamic_list

def get_momentum_stocks(kis_api, db_conn, period_months=6, top_n=30, watchlist_snapshot=None):
    """
    모멘텀 팩터 기반 종목 선별
    """
    logger.info(f"   (D) 모멘텀 팩터 계산 중 (기간: {period_months}개월, 상위 {top_n}개)...")
    momentum_scores = []
    
    try:
        # 1. KOSPI 수익률 계산
        kospi_code = "0001"
        period_days = period_months * 30
        kospi_prices = database.get_daily_prices(db_conn, kospi_code, limit=period_days)
        
        if kospi_prices.empty or len(kospi_prices) < period_days * 0.8:
            logger.warning(f"   (D) ⚠️ KOSPI 데이터 부족 ({len(kospi_prices)}일). 모멘텀 계산 건너뜀.")
            return []
        
        kospi_start_price = float(kospi_prices['CLOSE_PRICE'].iloc[0])
        kospi_end_price = float(kospi_prices['CLOSE_PRICE'].iloc[-1])
        kospi_return = (kospi_end_price / kospi_start_price - 1) * 100
        
        # 2. Watchlist 또는 BLUE_CHIP_STOCKS에서 종목 가져오기
        # [v4.5] 사용자 요청으로 전체 종목 대상으로 변경 (Watchlist 한정 X)
        all_codes = database.get_all_stock_codes(db_conn)
        
        if not all_codes:
            # Fallback: Watchlist or Bluechips
            watchlist = watchlist_snapshot or database.get_active_watchlist(db_conn)
            if not watchlist:
                stocks_to_check = [s for s in BLUE_CHIP_STOCKS if s.get('is_tradable', True)]
            else:
                stocks_to_check = [{'code': code, 'name': info.get('name', code)} for code, info in watchlist.items() if info.get('is_tradable', True)]
        else:
             # 전체 종목 (이름은 일단 코드로 대체하거나 추후 DB에서 가져올 수 있음)
             stocks_to_check = [{'code': code, 'name': code} for code in all_codes]

        logger.info(f"   (D) {len(stocks_to_check)}개 종목의 모멘텀 계산 중... (전체 대상)")
        
        # 3. 각 종목의 모멘텀 계산
        for stock in stocks_to_check:
            try:
                code = stock['code']
                name = stock.get('name', code)
                
                stock_prices = database.get_daily_prices(db_conn, code, limit=period_days)
                
                if stock_prices.empty or len(stock_prices) < period_days * 0.8:
                    continue
                
                stock_start_price = float(stock_prices['CLOSE_PRICE'].iloc[0])
                stock_end_price = float(stock_prices['CLOSE_PRICE'].iloc[-1])
                stock_return = (stock_end_price / stock_start_price - 1) * 100
                
                relative_momentum = stock_return - kospi_return
                
                momentum_scores.append({
                    'code': code,
                    'name': name,
                    'momentum': relative_momentum,
                    'absolute_return': stock_return,
                    'kospi_return': kospi_return
                })
                
                if hasattr(kis_api, 'API_CALL_DELAY'):
                    time.sleep(kis_api.API_CALL_DELAY * 0.1)
                
            except Exception as e:
                logger.debug(f"   (D) {stock.get('name', stock.get('code'))} 모멘텀 계산 오류: {e}")
                continue
        
        momentum_scores.sort(key=lambda x: x['momentum'], reverse=True)
        
        logger.info(f"   (D) ✅ 모멘텀 계산 완료. 상위 {min(top_n, len(momentum_scores))}개 반환")
        return momentum_scores[:top_n]
        
    except Exception as e:
        logger.error(f"   (D) ❌ 모멘텀 팩터 계산 중 오류 발생: {e}", exc_info=True)
        return []

# [v2.0] 자동 파라미터 최적화 함수들 (생략 없이 전체 포함)
def run_auto_parameter_optimization(db_conn, brain):
    """
    [v2.2] 자동 파라미터 최적화 파이프라인
    """
    logger.info("=" * 80)
    logger.info("   [v2.2 AUTO-OPTIMIZATION] 자동 파라미터 최적화 파이프라인 시작")
    logger.info("=" * 80)
    
    try:
        logger.info("   [Step 1/5] 현재 파라미터 조회 중...")
        current_params = database.get_all_config(db_conn)
        backtest_period = int(current_params.get('AUTO_OPTIMIZATION_PERIOD_DAYS', '90'))
        
        if not current_params:
            logger.warning("   ⚠️ CONFIG 테이블이 비어있습니다. 최적화를 건너뜁니다.")
            return False
        
        logger.info(f"   ✅ 현재 파라미터 {len(current_params)}개 조회 완료")
        
        logger.info("   [Step 2/5] 현재 파라미터로 백테스트 실행 중...")
        current_performance = run_simple_backtest(db_conn, current_params)
        
        if not current_performance:
            logger.warning("   ⚠️ 현재 파라미터 백테스트 실패. 최적화를 건너뜁니다.")
            return False
        
        logger.info(f"   ✅ 현재 성과: MDD {current_performance['mdd']:.2f}%, 연환산수익률 {current_performance['return']:.2f}%")
        
        logger.info("   [Step 3/5] 최적화 후보 파라미터 생성 중...")
        new_params = generate_optimized_params(current_params)
        logger.info(f"   ✅ 최적화 후보 파라미터 생성 완료 (변경: {len(new_params)}개)")
        
        logger.info("   [Step 4/5] 최적화 후보로 백테스트 실행 중...")
        new_performance = run_simple_backtest(db_conn, {**current_params, **new_params})
        
        if not new_performance:
            logger.warning("   ⚠️ 최적화 후보 백테스트 실패. 최적화를 건너뜁니다.")
            return False
        
        logger.info(f"   ✅ 최적화 성과: MDD {new_performance['mdd']:.2f}%, 연환산수익률 {new_performance['return']:.2f}%")
        
        logger.info("   [Step 5/5] AI 검증 (LLM) 시작...")
        market_summary = f"최근 {backtest_period}일 시장 요약"
        
        verification_result = verify_params_with_llm(
            brain, current_params, current_performance,
            new_params, new_performance, market_summary
        )
        
        if not verification_result:
            logger.warning("   ⚠️ AI 검증 실패. 최적화를 중단합니다.")
            return False
        
        is_approved = verification_result.get('is_approved', False)
        confidence = verification_result.get('confidence_score', 0.0)
        reasoning = verification_result.get('reasoning', 'N/A')
        
        logger.info(f"   ✅ AI 검증 완료: {is_approved}, 신뢰도: {confidence:.2f}")
        
        logger.info("   [v2.2] 최적화 이력 DB 저장 중...")
        ai_decision = 'APPROVED' if is_approved else 'REJECTED'
        
        optimization_id = database.save_optimization_history(
            connection=db_conn,
            current_params=current_params,
            new_params=new_params,
            current_performance=current_performance,
            new_performance=new_performance,
            ai_decision=ai_decision,
            ai_reasoning=reasoning,
            ai_confidence=confidence,
            market_summary=market_summary,
            backtest_period=backtest_period
        )
        
        if is_approved and confidence > 0.7:
            logger.info("   [Auto-Update] CONFIG 테이블 업데이트 시작...")
            update_count = 0
            for key, value in new_params.items():
                try:
                    database.set_config(db_conn, key, value)
                    update_count += 1
                    logger.info(f"   - {key}: {current_params.get(key)} → {value}")
                except Exception as e:
                    logger.error(f"   ❌ {key} 업데이트 실패: {e}")
            
            logger.info(f"   ✅ [Auto-Update] {update_count}/{len(new_params)}개 파라미터 업데이트 완료!")
            
            if optimization_id:
                database.mark_optimization_applied(db_conn, optimization_id)
            return True
        else:
            logger.warning(f"   ⚠️ [Auto-Update] 승인 거부 또는 신뢰도 부족 (신뢰도: {confidence:.2f} < 0.7)")
            return False
        
    except Exception as e:
        logger.error(f"   ❌ [AUTO-OPTIMIZATION] 오류 발생: {e}", exc_info=True)
        return False
    finally:
        logger.info("=" * 80)

def _get_param(params: Dict, key: str, default, cast_type=float):
    try:
        if key not in params or params[key] is None:
            return default
        return cast_type(params[key])
    except (ValueError, TypeError):
        return default

def run_simple_backtest(db_conn, params):
    try:
        if Backtester is None:
            logger.error("   (Backtest) ❌ Backtester 모듈을 사용할 수 없습니다. 최적화를 건너뜁니다.")
            return None
        
        logger.info("   (Backtest) Backtester 기반 검증 실행 중...")
        
        backtester = Backtester(
            db_conn,
            max_buys_per_day=_get_param(params, 'MAX_BUYS_PER_DAY', 100, int),
            profit_target_full=_get_param(params, 'PROFIT_TARGET_FULL', 10.0, float),
            profit_target_partial=_get_param(params, 'PROFIT_TARGET_PARTIAL', 5.0, float),
            rsi_threshold_1=_get_param(params, 'RSI_THRESHOLD_1', 70.0, float),
            rsi_threshold_2=_get_param(params, 'RSI_THRESHOLD_2', 75.0, float),
            rsi_threshold_3=_get_param(params, 'RSI_THRESHOLD_3', 80.0, float),
            time_based_bull=_get_param(params, 'TIME_BASED_BULL', 30, int),
            time_based_sideways=_get_param(params, 'TIME_BASED_SIDEWAYS', 30, int),
            max_position_pct=_get_param(params, 'MAX_POSITION_PCT', 5, int),
            cash_keep_pct=_get_param(params, 'CASH_KEEP_PCT', 5, int),
            hybrid_mode=True,
        )
        
        metrics = backtester.run()
        if not metrics:
            logger.warning("   (Backtest) 백테스트 결과가 없습니다.")
            return None
        
        monthly_return = metrics.get('monthly_return_pct')
        total_return = metrics.get('total_return_pct')
        
        return {
            'mdd': float(metrics.get('mdd_pct', 0.0)),
            'return': float(monthly_return if monthly_return is not None else (total_return or 0.0))
        }
        
    except Exception as e:
        logger.error(f"   (Backtest) 백테스트 실행 오류: {e}", exc_info=True)
        return None

def generate_optimized_params(current_params):
    new_params = {}
    if 'SELL_RSI_OVERBOUGHT_THRESHOLD' in current_params:
        current_value = float(current_params['SELL_RSI_OVERBOUGHT_THRESHOLD'])
        adjustment = current_value * 0.05
        new_value = min(80, current_value + adjustment)
        new_params['SELL_RSI_OVERBOUGHT_THRESHOLD'] = new_value
    
    if 'ATR_MULTIPLIER' in current_params:
        current_value = float(current_params['ATR_MULTIPLIER'])
        adjustment = 0.1
        new_value = min(3.0, current_value + adjustment)
        new_params['ATR_MULTIPLIER'] = new_value
    
    return new_params

def verify_params_with_llm(brain, current_params, current_performance, 
                           new_params, new_performance, market_summary):
    try:
        logger.info("   (LLM) [v2.2] JennieBrain을 통한 AI 검증 시작...")
        result = brain.verify_parameter_change(
            current_params=current_params,
            new_params=new_params,
            current_performance=current_performance,
            new_performance=new_performance,
            market_summary=market_summary
        )
        if result:
            logger.info(f"   (LLM) ✅ AI 검증 완료: {result.get('is_approved')}")
        return result
    except Exception as e:
        logger.error(f"   (LLM) ❌ AI 검증 오류: {e}", exc_info=True)
        return None

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
# [v1.0] Scout Hybrid Scoring Pipeline - 정량 기반 필터링
# =============================================================================

def is_hybrid_scoring_enabled() -> bool:
    """Scout v1.0 하이브리드 스코어링 활성화 여부 확인 (SCOUT_V5_ENABLED 환경변수 - 하위호환)"""
    return os.getenv("SCOUT_V5_ENABLED", "false").lower() == "true"


def process_quant_scoring_task(stock_info, quant_scorer, db_conn, kospi_prices_df=None):
    """
    [v1.0] Step 1: 정량 점수 계산 (LLM 호출 없음, 비용 0원)
    
    세 설계의 핵심 아이디어 구현:
    - Claude: 정량 점수를 LLM과 독립적으로 계산
    - Gemini: 비용 0원으로 1차 필터링
    - GPT: 조건부 승률 기반 점수 산출
    
    [v1.0] Gemini 피드백 반영:
    - 데이터 부족 시 is_valid=False 설정하여 "묻어가기" 합격 방지
    
    Args:
        stock_info: {'code': str, 'info': dict, 'snapshot': dict}
        quant_scorer: QuantScorer 인스턴스
        db_conn: DB 연결 (일봉 데이터 조회용)
        kospi_prices_df: KOSPI 일봉 데이터
    
    Returns:
        QuantScoreResult 객체
    """
    code = stock_info['code']
    info = stock_info['info']
    snapshot = stock_info.get('snapshot', {}) or {}
    
    try:
        # 일봉 데이터 조회
        daily_prices_df = database.get_daily_prices(db_conn, code, limit=150)
        
        # [v1.0] 데이터 부족 시 is_valid=False 설정 (묻어가기 방지)
        if daily_prices_df.empty or len(daily_prices_df) < 30:
            data_len = len(daily_prices_df) if not daily_prices_df.empty else 0
            logger.debug(f"   ⚠️ [Quant] {info['name']}({code}) 일봉 데이터 부족 ({data_len}일) → is_valid=False")
            from shared.hybrid_scoring import QuantScoreResult
            return QuantScoreResult(
                stock_code=code,
                stock_name=info['name'],
                total_score=0.0,  # [v1.0] 0점 (중립 50점 아님!)
                momentum_score=0.0,
                quality_score=0.0,
                value_score=0.0,
                technical_score=0.0,
                news_stat_score=0.0,
                supply_demand_score=0.0,
                matched_conditions=[],
                condition_win_rate=None,
                condition_sample_count=0,
                condition_confidence='LOW',
                is_valid=False,  # [v1.0] 묻어가기 방지
                invalid_reason=f'데이터 부족 ({data_len}일)',
                details={'note': f'데이터 부족 ({data_len}일)'},
            )
        
        # 정량 점수 계산
        result = quant_scorer.calculate_total_quant_score(
            stock_code=code,
            stock_name=info['name'],
            daily_prices_df=daily_prices_df,
            kospi_prices_df=kospi_prices_df,
            pbr=snapshot.get('pbr'),
            per=snapshot.get('per'),
            current_sentiment_score=info.get('sentiment_score', 50),
            foreign_net_buy=snapshot.get('foreign_net_buy'),
        )
        
        # [v1.0] 역신호 카테고리 체크
        # 팩터 분석 결과: 수주(43.7%), 배당(37.6%) 뉴스는 역신호!
        REVERSE_SIGNAL_CATEGORIES = {'수주', '배당', '자사주', '주주환원', '배당락'}
        news_category = info.get('news_category') or snapshot.get('news_category')
        
        if news_category and news_category in REVERSE_SIGNAL_CATEGORIES:
            sentiment_score = info.get('sentiment_score', 50)
            if sentiment_score >= 70:  # 호재로 분류된 경우
                logger.warning(f"   ⚠️ [v1.0] {info['name']}({code}) 역신호 카테고리({news_category}) 감지 - "
                              f"통계상 승률 50% 미만, 점수 패널티 적용")
                # 결과에 역신호 정보 추가
                if result.details is None:
                    result.details = {}
                result.details['reverse_signal_category'] = news_category
                result.details['reverse_signal_warning'] = True
        
        logger.debug(f"   ✅ [Quant] {info['name']}({code}) - {result.total_score:.1f}점")
        return result
        
    except Exception as e:
        logger.error(f"   ❌ [Quant] {code} 정량 점수 계산 오류: {e}")
        from shared.hybrid_scoring import QuantScoreResult
        # [v1.0] 예외 발생 시에도 is_valid=False 설정 (묻어가기 방지)
        return QuantScoreResult(
            stock_code=code,
            stock_name=info['name'],
            total_score=0.0,  # [v1.0] 0점 (중립 50점 아님!)
            momentum_score=0.0,
            quality_score=0.0,
            value_score=0.0,
            technical_score=0.0,
            news_stat_score=0.0,
            supply_demand_score=0.0,
            matched_conditions=[],
            condition_win_rate=None,
            condition_sample_count=0,
            condition_confidence='LOW',
            is_valid=False,  # [v1.0] 묻어가기 방지
            invalid_reason=f'계산 오류: {str(e)[:30]}',
            details={'error': str(e)},
        )


def process_phase1_hunter_v5_task(stock_info, brain, quant_result, snapshot_cache=None, news_cache=None):
    """
    [v1.0] Phase 1 Hunter - 정량 컨텍스트 포함 LLM 분석
    [v1.0] 경쟁사 수혜 점수 반영 추가
    
    기존 Hunter와 달리, QuantScorer의 결과를 프롬프트에 포함하여
    LLM이 데이터 기반 판단을 하도록 유도합니다.
    """
    from shared.hybrid_scoring import format_quant_score_for_prompt
    
    code = stock_info['code']
    info = stock_info['info']
    
    # 정량 컨텍스트 생성
    quant_context = format_quant_score_for_prompt(quant_result)
    
    # [v1.0] 경쟁사 수혜 점수 조회
    competitor_benefit = database.get_competitor_benefit_score(code)
    competitor_bonus = competitor_benefit.get('score', 0)
    competitor_reason = competitor_benefit.get('reason', '')
    
    snapshot = snapshot_cache.get(code) if snapshot_cache else None
    if not snapshot:
        return {
            'code': code,
            'name': info['name'],
            'info': info,
            'snapshot': None,
            'quant_result': quant_result,
            'hunter_score': 0,
            'hunter_reason': '스냅샷 조회 실패',
            'passed': False,
            'competitor_bonus': competitor_bonus,
        }
    
    news_from_chroma = news_cache.get(code, "최근 관련 뉴스 없음") if news_cache else "뉴스 캐시 없음"
    
    # [v1.0] 경쟁사 수혜 정보를 뉴스에 추가
    if competitor_bonus > 0:
        news_from_chroma += f"\n\n⚡ [경쟁사 수혜 기회] {competitor_reason} (+{competitor_bonus}점)"
    
    decision_info = {
        'code': code,
        'name': info['name'],
        'technical_reason': 'N/A',
        'news_reason': news_from_chroma if news_from_chroma not in ["뉴스 DB 미연결", "뉴스 검색 오류"] else ', '.join(info.get('reasons', [])),
        'per': snapshot.get('per'),
        'pbr': snapshot.get('pbr'),
        'market_cap': snapshot.get('market_cap'),
    }
    
    # [v1.0] 정량 컨텍스트 포함 Hunter 호출
    hunter_result = brain.get_jennies_analysis_score_v5(decision_info, quant_context)
    hunter_score = hunter_result.get('score', 0)
    
    # [v1.0] 경쟁사 수혜 가산점 적용 (최대 +10점)
    if competitor_bonus > 0:
        hunter_score = min(100, hunter_score + competitor_bonus)
        logger.info(f"   🎯 [경쟁사 수혜] {info['name']}({code}) +{competitor_bonus}점 가산 ({competitor_reason})")
    
    # [v1.0] 정량+정성 하이브리드 점수
    # [v4.1] 기준 상향 75점
    passed = hunter_score >= 75
    if hunter_score == 0: passed = False
    
    if passed:
        logger.info(f"   ✅ [v5 Hunter 통과] {info['name']}({code}) - Quant:{quant_result.total_score:.0f} → Hunter:{hunter_score}점")
    else:
        logger.debug(f"   ❌ [v5 Hunter 탈락] {info['name']}({code}) - Quant:{quant_result.total_score:.0f} → Hunter:{hunter_score}점")
    
    return {
        'code': code,
        'name': info['name'],
        'info': info,
        'snapshot': snapshot,
        'decision_info': decision_info,
        'quant_result': quant_result,
        'hunter_score': hunter_score,
        'hunter_reason': hunter_result.get('reason', ''),
        'passed': passed,
        'competitor_bonus': competitor_bonus,  # [v1.0] 경쟁사 수혜 점수
        'competitor_reason': competitor_reason,
    }


def process_phase23_judge_v5_task(phase1_result, brain):
    """
    [v1.0] Phase 2-3: Debate + Judge (정량 컨텍스트 포함)
    
    정량 분석 결과를 Judge 프롬프트에 포함하여
    하이브리드 점수를 산출합니다.
    """
    from shared.hybrid_scoring import format_quant_score_for_prompt
    
    code = phase1_result['code']
    info = phase1_result['info']
    decision_info = phase1_result['decision_info']
    quant_result = phase1_result['quant_result']
    hunter_score = phase1_result['hunter_score']
    
    logger.info(f"   🔄 [v5 Phase 2-3] {info['name']}({code}) Debate-Judge 시작...")
    
    # 정량 컨텍스트 생성
    quant_context = format_quant_score_for_prompt(quant_result)
    
    # Phase 2: Debate (Bull vs Bear)
    debate_log = brain.run_debate_session(decision_info)
    
    # Phase 3: Judge (정량 컨텍스트 포함)
    judge_result = brain.run_judge_scoring_v5(decision_info, debate_log, quant_context)
    score = judge_result.get('score', 0)
    grade = judge_result.get('grade', 'D')
    reason = judge_result.get('reason', '분석 실패')
    
    # [v1.0] 하이브리드 점수 계산 (정량 60% + 정성 40%)
    # Gemini 설계의 안전장치: 차이 30점 이상시 보수적 가중치
    quant_score = quant_result.total_score
    llm_score = score
    
    score_diff = abs(quant_score - llm_score)
    if score_diff >= 30:
        # 안전장치 발동: 낮은 쪽으로 가중치 이동
        if quant_score < llm_score:
            hybrid_score = quant_score * 0.75 + llm_score * 0.25
            logger.warning(f"   ⚠️ [Safety Lock] {info['name']} - 정량({quant_score:.0f}) << 정성({llm_score}) → 보수적 판단")
        else:
            hybrid_score = quant_score * 0.45 + llm_score * 0.55
            logger.warning(f"   ⚠️ [Safety Lock] {info['name']} - 정성({llm_score}) << 정량({quant_score:.0f}) → 보수적 판단")
    else:
        # 기본 비율: 정량 60% + 정성 40%
        hybrid_score = quant_score * 0.60 + llm_score * 0.40
    
    # 최종 판단
    is_tradable = hybrid_score >= 75  # A등급 이상
    approved = hybrid_score >= 50     # C등급 이상
    
    # 등급 재결정
    if hybrid_score >= 80:
        final_grade = 'S'
    elif hybrid_score >= 70:
        final_grade = 'A'
    elif hybrid_score >= 60:
        final_grade = 'B'
    elif hybrid_score >= 50:
        final_grade = 'C'
    else:
        final_grade = 'D'
    
    if approved:
        logger.info(f"   ✅ [v5 Judge 승인] {info['name']}({code}) - Hybrid:{hybrid_score:.1f}점 ({final_grade})")
    else:
        logger.info(f"   ❌ [v5 Judge 거절] {info['name']}({code}) - Hybrid:{hybrid_score:.1f}점 ({final_grade})")
    
    metadata = {
        'llm_grade': final_grade,
        'llm_updated_at': _utcnow().isoformat(),
        'source': 'hybrid_scorer_v5',
        'quant_score': quant_score,
        'llm_raw_score': llm_score,
        'hybrid_score': hybrid_score,
        'hunter_score': hunter_score,
        'condition_win_rate': quant_result.condition_win_rate,
    }
    
    return {
        'code': code,
        'name': info['name'],
        'is_tradable': is_tradable,
        'llm_score': hybrid_score,  # 하이브리드 점수 저장
        'llm_reason': reason,
        'approved': approved,
        'llm_metadata': metadata,
    }


def process_phase1_hunter_task(stock_info, brain, snapshot_cache=None, news_cache=None):
    """
    [v4.2] Phase 1 Hunter만 실행하는 태스크 (병렬 처리용)
    
    변경사항:
    - KIS API 스냅샷: 사전 캐시에서 조회 (API 호출 X)
    - ChromaDB 뉴스: 사전 캐시에서 조회 (HTTP 요청 X)
    - LLM 호출만 수행 → Rate Limit 대응 용이
    """
    code = stock_info['code']
    info = stock_info['info']
    
    # [v4.2] 캐시에서 스냅샷 조회 (API 호출 X)
    snapshot = snapshot_cache.get(code) if snapshot_cache else None
    if not snapshot:
        logger.debug(f"   ⚠️ [Phase 1] {info['name']}({code}) Snapshot 캐시 미스")
        return {
            'code': code,
            'name': info['name'],
            'info': info,
            'snapshot': None,
            'hunter_score': 0,
            'hunter_reason': '스냅샷 조회 실패',
            'passed': False,
        }

    factor_info = ""
    momentum_value = None
    for reason in info.get('reasons', []):
        if '모멘텀' in reason:
            factor_info = reason
            try:
                match = re.search(r'([\d.-]+)%', reason)
                if match:
                    momentum_value = float(match.group(1))
            except Exception:
                pass
            break
    
    # [v4.2] 캐시에서 뉴스 조회 (HTTP 요청 X)
    news_from_chroma = news_cache.get(code, "최근 관련 뉴스 없음") if news_cache else "뉴스 캐시 없음"
    
    # 기존 reasons + ChromaDB 뉴스 결합
    all_reasons = info.get('reasons', []).copy()
    if news_from_chroma and news_from_chroma not in ["뉴스 DB 미연결", "최근 관련 뉴스 없음", "뉴스 검색 오류", "뉴스 조회 실패", "뉴스 캐시 없음"]:
        all_reasons.append(news_from_chroma)
    
    decision_info = {
        'code': code,
        'name': info['name'],
        'technical_reason': 'N/A (전략 변경)',
        'news_reason': news_from_chroma if news_from_chroma not in ["뉴스 DB 미연결", "뉴스 검색 오류"] else ', '.join(info['reasons']),
        'per': snapshot.get('per'),
        'pbr': snapshot.get('pbr'),
        'market_cap': snapshot.get('market_cap'),
        'factor_info': factor_info,
        'momentum_score': momentum_value
    }

    # Phase 1: Hunter (Gemini-Flash로 빠른 필터링)
    hunter_result = brain.get_jennies_analysis_score(decision_info)
    hunter_score = hunter_result.get('score', 0)
    
    # [v1.0] Phase 1 통과 기준: 60점 이상 (상위 40~50개, 약 20~25% 목표)
    passed = hunter_score >= 60
    if passed:
        logger.info(f"   ✅ [Phase 1 통과] {info['name']}({code}) - Hunter: {hunter_score}점")
    else:
        logger.debug(f"   ❌ [Phase 1 탈락] {info['name']}({code}) - Hunter: {hunter_score}점")
    
    return {
        'code': code,
        'name': info['name'],
        'info': info,
        'snapshot': snapshot,
        'decision_info': decision_info,
        'hunter_score': hunter_score,
        'hunter_reason': hunter_result.get('reason', ''),
        'passed': passed,
    }


def process_phase23_debate_judge_task(phase1_result, brain):
    """
    [v3.8] Phase 2-3 (Debate + Judge) 실행하는 태스크 (Phase 1 통과 종목만)
    GPT-5-mini로 심층 분석
    """
    code = phase1_result['code']
    info = phase1_result['info']
    decision_info = phase1_result['decision_info']
    hunter_score = phase1_result['hunter_score']
    
    logger.info(f"   🔄 [Phase 2-3] {info['name']}({code}) Debate-Judge 시작...")
    
    # Phase 2: Debate (Bull vs Bear)
    debate_log = brain.run_debate_session(decision_info)
    
    # Phase 3: Judge (Supreme Jennie)
    judge_result = brain.run_judge_scoring(decision_info, debate_log)
    score = judge_result.get('score', 0)
    grade = judge_result.get('grade', 'D')
    reason = judge_result.get('reason', '분석 실패')
    
    # [v1.0] 최종 판단 - Judge 승인 기준 완화 (60→50점)
    is_tradable = score >= 75  # 강력 매수: 75점 이상 (A등급)
    approved = score >= 50     # Watchlist 등록: 50점 이상 (C등급 이상)
    
    if approved:
        logger.info(f"   ✅ [Judge 승인] {info['name']}({code}) - 최종: {score}점 ({grade})")
    else:
        logger.info(f"   ❌ [Judge 거절] {info['name']}({code}) - 최종: {score}점 ({grade})")
    
    # 메타데이터에 토론 요약 일부 포함
    metadata = {
        'llm_grade': grade,
        'llm_updated_at': _utcnow().isoformat(),
        'source': 'llm_judge',
        'hunter_score': hunter_score,
    }
    
    return {
        'code': code,
        'name': info['name'],
        'is_tradable': is_tradable,
        'llm_score': score,
        'llm_reason': reason,
        'approved': approved,
        'llm_metadata': metadata,
    }


def process_llm_decision_task(stock_info, kis_api, brain):
    """
    [Deprecated in v3.8] 기존 단일 패스 처리 (호환성 유지용)
    """
    code = stock_info['code']
    info = stock_info['info']
    decision_hash = stock_info['decision_hash']
    
    if hasattr(kis_api, 'API_CALL_DELAY'):
        time.sleep(kis_api.API_CALL_DELAY)
    
    snapshot = kis_api.get_stock_snapshot(code)
    if not snapshot:
        logger.warning(f"   ⚠️ [LLM 분석] {info['name']}({code}) Snapshot 조회 실패")
        return {
            'code': code,
            'name': info['name'],
            'is_tradable': False,
            'llm_score': 0,
            'llm_reason': '스냅샷 조회 실패',
            'approved': False,
            'llm_metadata': {
                'llm_grade': 'D',
                'decision_hash': decision_hash,
                'llm_updated_at': _utcnow().isoformat(),
                'source': 'llm',
            }
        }

    factor_info = ""
    momentum_value = None
    for reason in info.get('reasons', []):
        if '모멘텀 팩터' in reason:
            factor_info = reason
            try:
                match = re.search(r'상대 모멘텀: ([\d.-]+)%', reason)
                if match:
                    momentum_value = float(match.group(1))
            except Exception:
                pass
            break
    
    decision_info = {
        'code': code,
        'name': info['name'],
        'technical_reason': 'N/A (전략 변경)',
        'news_reason': ', '.join(info['reasons']),
        'per': snapshot.get('per'),
        'pbr': snapshot.get('pbr'),
        'market_cap': snapshot.get('market_cap'),
        'factor_info': factor_info,
        'momentum_score': momentum_value
    }

    # [v1.0] Scout 3단계 파이프라인 적용
    
    # 1. Phase 1: Hunter (High Recall Filtering)
    # - 기존 분석 로직을 활용하되, 기준을 대폭 낮춰서(40점) 잠재력 있는 종목을 넓게 잡음
    hunter_result = brain.get_jennies_analysis_score(decision_info)
    hunter_score = hunter_result.get('score', 0)
    
    if hunter_score < 40:
        logger.info(f"   ❌ [Phase 1 탈락] {info['name']}({code}) - Hunter점수: {hunter_score}점 (미달)")
        return {
            'code': code,
            'name': info['name'],
            'is_tradable': False,
            'llm_score': hunter_score,
            'llm_reason': hunter_result.get('reason', 'Phase 1 필터링 탈락'),
            'approved': False,
            'llm_metadata': {
                'llm_grade': 'D',
                'decision_hash': decision_hash,
                'llm_updated_at': _utcnow().isoformat(),
                'source': 'llm_hunter_reject',
            }
        }
    
    logger.info(f"   ✅ [Phase 1 통과] {info['name']}({code}) - Hunter점수: {hunter_score}점 -> Debate 진출")

    # 2. Phase 2: Debate (Bull vs Bear)
    # - 통과된 종목에 대해 찬반 토론 시뮬레이션
    debate_log = brain.run_debate_session(decision_info)
    
    # 3. Phase 3: Judge (Supreme Jennie)
    # - 토론 내용을 바탕으로 최종 판결
    judge_result = brain.run_judge_scoring(decision_info, debate_log)
    score = judge_result.get('score', 0)
    grade = judge_result.get('grade', 'D')
    reason = judge_result.get('reason', '분석 실패')
    
    # [v1.0] 최종 판단 - Judge 승인 기준 완화 (60→50점)
    is_tradable = score >= 75  # 강력 매수: 75점 이상 (A등급)
    approved = score >= 50     # Watchlist 등록: 50점 이상 (C등급 이상)
    
    # 메타데이터에 토론 요약 일부 포함 (선택 사항)
    metadata = {
        'llm_grade': grade,
        'decision_hash': decision_hash,
        'llm_updated_at': _utcnow().isoformat(),
        'source': 'llm_judge',
        'debate_summary': debate_log[:200] + "..." if len(debate_log) > 200 else debate_log
    }
    
    if approved:
        logger.info(f"   🎉 [Judge 승인] {info['name']}({code}) - 최종: {score}점 ({grade})")
    else:
        logger.info(f"   ❌ [Judge 거절] {info['name']}({code}) - 최종: {score}점 ({grade})")
    
    return {
        'code': code,
        'name': info['name'],
        'is_tradable': is_tradable,
        'llm_score': score,
        'llm_reason': reason,
        'approved': approved,
        'llm_metadata': metadata,
    }

def fetch_kis_data_task(stock, kis_api):
    try:
        stock_code = stock['code']
        
        if hasattr(kis_api, 'API_CALL_DELAY'):
            time.sleep(kis_api.API_CALL_DELAY)
        
        price_data = kis_api.get_stock_daily_prices(stock_code, num_days_to_fetch=30)
        
        daily_prices = []
        if price_data is not None:
            if hasattr(price_data, 'empty') and not price_data.empty:
                for _, dp in price_data.iterrows():
                    # DataFrame 컬럼 접근 시 안전하게 처리 (close_price 또는 price)
                    close_price = dp.get('close_price') if 'close_price' in dp.index else dp.get('price')
                    high_price = dp.get('high_price') if 'high_price' in dp.index else dp.get('high')
                    low_price = dp.get('low_price') if 'low_price' in dp.index else dp.get('low')
                    date_val = dp.get('price_date') if 'price_date' in dp.index else dp.get('date')
                    
                    if close_price is not None:
                        daily_prices.append({
                            'p_date': date_val, 'p_code': stock_code,
                            'p_price': close_price, 'p_high': high_price, 'p_low': low_price
                        })
            elif isinstance(price_data, list) and len(price_data) > 0:
                for dp in price_data:
                    if isinstance(dp, dict):
                        # dict 접근 시 안전하게 처리 (close_price 또는 price)
                        close_price = dp.get('close_price') or dp.get('price')
                        high_price = dp.get('high_price') or dp.get('high')
                        low_price = dp.get('low_price') or dp.get('low')
                        date_val = dp.get('price_date') or dp.get('date')
                        
                        if close_price is not None:
                            daily_prices.append({
                                'p_date': date_val, 'p_code': stock_code,
                                'p_price': close_price, 'p_high': high_price, 'p_low': low_price
                            })
        
        fundamentals = None
        if stock.get("is_tradable", False):
            snapshot = kis_api.get_stock_snapshot(stock_code)
            if hasattr(kis_api, 'API_CALL_DELAY'):
                time.sleep(kis_api.API_CALL_DELAY)
            if snapshot:
                fundamentals = {
                    'code': stock_code,
                    'per': snapshot.get('per'),
                    'pbr': snapshot.get('pbr'),
                    'market_cap': snapshot.get('market_cap')
                }
        
        return daily_prices, fundamentals
    except Exception as e:
        logger.error(f"   (DW) ❌ {stock.get('name', 'N/A')} 처리 중 오류 발생: {e}")
        return [], None

def main():
    start_time = time.time()
    logger.info("--- 🤖 'Scout Job' [v3.0 Local] 실행 시작 ---")
    
    db_conn = None
    kis_api = None
    brain = None
    chroma_client = None

    try:
        logger.info("--- [Init] 환경 변수 로드 및 MariaDB/KIS API 연결 시작 ---")
        load_dotenv()
        
        logger.info("🔧 DB 연결 중... (SQLAlchemy 사용)")
        db_conn = database.get_db_connection()
        if db_conn is None:
            raise Exception("MariaDB 연결에 실패했습니다.")
        
        logger.info("✅ DB 연결 완료")
        
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
        watchlist_snapshot = database.get_active_watchlist(db_conn)
        
        # ChromaDB 초기화
        vectorstore = None
        try:
            logger.info("   ... ChromaDB 클라이언트 연결 시도 (Gemini Embeddings) ...")
            api_key = ensure_gemini_api_key()
            embeddings = GoogleGenerativeAIEmbeddings(
                model="models/gemini-embedding-001", 
                google_api_key=api_key
            )
            
            chroma_client = chromadb.HttpClient(
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

        # [Phase 0] 자동 파라미터 최적화 (비활성화)
        # logger.info("--- [Phase 0] 자동 파라미터 최적화 시작 ---")
        # try:
        #     if run_auto_parameter_optimization(db_conn, brain):
        #         logger.info("   ✅ 자동 파라미터 최적화 완료!")
        #     else:
        #         logger.info("   ⏭️ 자동 파라미터 최적화 건너뜀")
        # except Exception as e:
        #     logger.error(f"   ❌ 자동 파라미터 최적화 중 오류: {e}")

        # Phase 1: 트리플 소스 후보 발굴 (v3.8: 섹터 분석 추가)
        logger.info("--- [Phase 1] 트리플 소스 후보 발굴 시작 ---")
        update_pipeline_status(phase=1, phase_name="Hunter Scout", status="running", progress=0)
        candidate_stocks = {}

        # A: 동적 우량주 (KOSPI 200 기준)
        universe_size = int(os.getenv("SCOUT_UNIVERSE_SIZE", "200"))
        for stock in get_dynamic_blue_chips(limit=universe_size):
            candidate_stocks[stock['code']] = {'name': stock['name'], 'reasons': ['KOSPI 시총 상위']}
        
        # E: 섹터 모멘텀 분석 (v3.8 신규)
        sector_analysis = analyze_sector_momentum(kis_api, db_conn, watchlist_snapshot)
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
            db_conn,
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
        enrich_candidates_with_market_data(candidate_stocks, db_conn, vectorstore)
        
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
            logger.info("   🚀 Scout v1.0 Hybrid Scoring Mode 활성화!")
            logger.info("=" * 60)
            
            try:
                from shared.hybrid_scoring import (
                    QuantScorer, HybridScorer, 
                    create_hybrid_scoring_tables,
                    format_quant_score_for_prompt,
                )
                from shared.market_regime import MarketRegimeDetector
                
                # DB 테이블 생성 확인
                create_hybrid_scoring_tables(db_conn)
                
                # 시장 국면 감지
                kospi_prices = database.get_daily_prices(db_conn, "0001", limit=60)
                if not kospi_prices.empty:
                    detector = MarketRegimeDetector()
                    current_regime, _ = detector.detect_regime(kospi_prices, float(kospi_prices['CLOSE_PRICE'].iloc[-1]), quiet=True)
                else:
                    current_regime = "SIDEWAYS"
                
                logger.info(f"   현재 시장 국면: {current_regime}")
                
                # QuantScorer 초기화
                quant_scorer = QuantScorer(db_conn, market_regime=current_regime)
                
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
                        stock_info, quant_scorer, db_conn, kospi_prices
                    )
                
                # Step 2: 정량 기반 1차 필터링 (하위 50% 탈락)
                logger.info(f"\n   [v5 Step 2] 정량 기반 1차 필터링 (하위 50% 탈락)")
                quant_result_list = list(quant_results.values())
                filtered_results = quant_scorer.filter_candidates(quant_result_list, cutoff_ratio=0.5)
                
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
                with ThreadPoolExecutor(max_workers=llm_max_workers) as executor:
                    future_to_code = {}
                    for code in filtered_codes:
                        info = candidate_stocks[code]
                        quant_result = quant_results[code]
                        payload = {'code': code, 'info': info}
                        future = executor.submit(
                            process_phase1_hunter_v5_task, 
                            payload, brain, quant_result, snapshot_cache, news_cache
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
                        for p1_result in phase1_passed:
                            future = executor.submit(process_phase23_judge_v5_task, p1_result, brain)
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
            
            # [v4.3] 새로운 캐시 시스템 - LLM_EVAL_CACHE 테이블 기반 직접 비교
            llm_cache_snapshot = _load_llm_cache_from_db(db_conn)
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
                        'llm_reason': record.get('llm_reason', ''),
                        'news_used': news_cache.get(code, ''),
                        'is_approved': record.get('approved', False),
                        'is_tradable': record.get('is_tradable', False),
                    }
                _save_llm_cache_batch(db_conn, new_cache_entries)
                _save_last_llm_run_at(db_conn, _utcnow())

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
        database.save_to_watchlist(db_conn, final_approved_list)
        
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
        
        if all_daily: database.save_all_daily_prices(db_conn, all_daily)
        if all_fund: database.update_all_stock_fundamentals(db_conn, all_fund)
        
        # Phase 3-A: 재무 데이터 (네이버 크롤링)
        tradable_codes = [s['code'] for s in final_approved_list if s.get('is_tradable', True)]
        if tradable_codes:
            batch_update_watchlist_financial_data(db_conn, tradable_codes)
        
        # [v1.0] Redis 최종 상태 업데이트 - 완료
        update_pipeline_status(
            phase=3, phase_name="Final Judge", status="completed",
            progress=100,
            total_candidates=len(candidate_stocks) if 'candidate_stocks' in dir() else 0,
            passed_phase1=len(phase1_passed) if 'phase1_passed' in dir() else 0,
            passed_phase2=len(phase1_passed) if 'phase1_passed' in dir() else 0,
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
    
    finally:
        if db_conn:
            db_conn.close()
            logger.info("--- [DB] 연결 종료 ---")
            
    logger.info(f"--- 🤖 'Scout Job' 종료 (소요: {time.time() - start_time:.2f}초) ---")

if __name__ == "__main__":
    main()
