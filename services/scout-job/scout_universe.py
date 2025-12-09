# services/scout-job/scout_universe.py
# Version: v1.0
# Scout Job Universe Selection - 섹터 분석 및 종목 선별 함수
#
# scout.py에서 분리된 종목 유니버스 관리 함수들

import logging
import time
import requests
from bs4 import BeautifulSoup
from typing import Dict, List, Optional

import shared.database as database

logger = logging.getLogger(__name__)

# FinanceDataReader (optional)
try:
    import FinanceDataReader as fdr
    FDR_AVAILABLE = True
except ImportError:
    FDR_AVAILABLE = False
    logger.warning("⚠️ FinanceDataReader 미설치 - 네이버 금융 스크래핑으로 폴백")


# 정적 우량주 목록 (Fallback용)
BLUE_CHIP_STOCKS = [
    {"code": "0001", "name": "KOSPI", "is_tradable": False},
    {"code": "005930", "name": "삼성전자", "is_tradable": True},
    {"code": "000660", "name": "SK하이닉스", "is_tradable": True},
    {"code": "035420", "name": "NAVER", "is_tradable": True},
    {"code": "035720", "name": "카카오", "is_tradable": True},
]


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
                    change_pct = row.get('ChagesRatio') or row.get('ChangesRatio') or row.get('ChangeRatio')
                    
                    if change_pct is None:
                        changes = float(row.get('Changes', 0))
                        close = float(row.get('Close', row.get('Price', 1)))
                        if close > 0:
                            change_pct = (changes / close) * 100
                        else:
                            change_pct = 0
                    else:
                        change_pct = float(change_pct)
                    
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
                    'stocks': data['stocks'][:5],
                }
        
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
    sorted_sectors = list(sector_analysis.items())[:3]
    
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
            
            df_kospi = fdr.StockListing('KOSPI')
            
            if 'Marcap' in df_kospi.columns:
                df_sorted = df_kospi.sort_values('Marcap', ascending=False).head(limit)
            elif 'Market' in df_kospi.columns:
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
        
        # 2. 전체 종목 또는 Watchlist에서 가져오기
        all_codes = database.get_all_stock_codes(db_conn)
        
        if not all_codes:
            watchlist = watchlist_snapshot or database.get_active_watchlist(db_conn)
            if not watchlist:
                stocks_to_check = [s for s in BLUE_CHIP_STOCKS if s.get('is_tradable', True)]
            else:
                stocks_to_check = [{'code': code, 'name': info.get('name', code)} for code, info in watchlist.items() if info.get('is_tradable', True)]
        else:
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
