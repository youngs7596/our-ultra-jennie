#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
네이버 증권 재무제표 크롤링 및 DB 저장
성장성 팩터 계산을 위한 매출액, EPS 데이터 수집
"""

import os
import sys
import requests
from bs4 import BeautifulSoup
import time
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv
import re

# 프로젝트 루트 경로 설정 (utilities/ 상위 폴더)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import shared.auth as auth
import shared.database as database

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [%(funcName)s] - %(message)s'
)
logger = logging.getLogger(__name__)

# 재무제표 테이블 DDL (financial_data_collector.py와 동일)
DDL_FINANCIAL_DATA = """
CREATE TABLE FINANCIAL_DATA (
  STOCK_CODE        VARCHAR2(16) NOT NULL,
  REPORT_DATE       DATE NOT NULL,
  REPORT_TYPE       VARCHAR2(16) NOT NULL, -- 'QUARTERLY' / 'ANNUAL'
  SALES            NUMBER,  -- 매출액
  OPERATING_PROFIT  NUMBER,  -- 영업이익
  NET_INCOME       NUMBER,  -- 당기순이익
  TOTAL_ASSETS     NUMBER,  -- 자산총계
  TOTAL_LIABILITIES NUMBER, -- 부채총계
  TOTAL_EQUITY     NUMBER,  -- 자본총계
  SHARES_OUTSTANDING NUMBER, -- 발행주식수 (EPS 계산용)
  EPS              NUMBER,  -- 주당순이익 (계산값)
  SALES_GROWTH     NUMBER,  -- 매출액 성장률 (%)
  EPS_GROWTH       NUMBER,  -- EPS 성장률 (%)
  CREATED_AT       TIMESTAMP DEFAULT SYSTIMESTAMP,
  CONSTRAINT PK_FINANCIAL_DATA PRIMARY KEY (STOCK_CODE, REPORT_DATE, REPORT_TYPE)
)
"""

def ensure_financial_table(connection):
    """FINANCIAL_DATA 테이블 생성"""
    try:
        cur = connection.cursor()
        cur.execute("""
            SELECT COUNT(*) 
            FROM user_tables 
            WHERE table_name = 'FINANCIAL_DATA'
        """)
        exists = cur.fetchone()[0] > 0
        if not exists:
            logger.info("테이블 'FINANCIAL_DATA' 미존재. 생성 시도...")
            cur.execute(DDL_FINANCIAL_DATA)
            connection.commit()
            logger.info("✅ 'FINANCIAL_DATA' 생성 완료.")
        else:
            logger.info("✅ 'FINANCIAL_DATA' 이미 존재.")
    except Exception as e:
        logger.error(f"❌ 테이블 생성 확인 중 오류: {e}", exc_info=True)
        raise
    finally:
        if cur:
            cur.close()

def scrape_naver_finance_financials(stock_code: str):
    """
    네이버 증권에서 재무제표 데이터 크롤링
    
    Args:
        stock_code: 종목 코드 (6자리)
    
    Returns:
        재무제표 데이터 딕셔너리 리스트
    """
    url = f"https://finance.naver.com/item/main.naver?code={stock_code}"
    financial_data = []
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code != 200:
            logger.warning(f"⚠️ {stock_code} 페이지 접근 실패 (Status: {response.status_code})")
            return financial_data
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 재무제표 테이블 찾기
        # 네이버 증권의 재무제표는 보통 특정 클래스나 ID를 가진 테이블에 있음
        tables = soup.find_all('table', class_='tb_type1')
        
        for table in tables:
            rows = table.find_all('tr')
            
            # 테이블 헤더 찾기 (분기/연도 정보)
            header_row = None
            for row in rows:
                cells = row.find_all(['th', 'td'])
                if len(cells) > 5:  # 헤더 행일 가능성
                    header_text = ' '.join([cell.get_text(strip=True) for cell in cells])
                    if any(keyword in header_text for keyword in ['2024', '2023', '2022', '분기']):
                        header_row = cells
                        break
            
            if not header_row:
                continue
            
            # 헤더에서 날짜 정보 추출
            dates = []
            for cell in header_row[1:]:  # 첫 번째 열은 항목명
                date_text = cell.get_text(strip=True)
                # 날짜 형식 파싱 (예: "2024.12", "2024/12", "2024년 4분기" 등)
                date_match = re.search(r'(\d{4})[.\/년\s]+(\d{1,2})', date_text)
                if date_match:
                    year = int(date_match.group(1))
                    quarter_or_month = int(date_match.group(2))
                    # 분기별 데이터로 가정
                    dates.append({
                        'year': year,
                        'quarter': quarter_or_month if quarter_or_month <= 4 else None,
                        'raw': date_text
                    })
            
            # 데이터 행 파싱
            for row in rows:
                cells = row.find_all(['td', 'th'])
                if len(cells) < 2:
                    continue
                
                row_label = cells[0].get_text(strip=True)
                
                # 매출액, 영업이익, 당기순이익 행 찾기
                if '매출액' in row_label or '영업이익' in row_label or '당기순이익' in row_label:
                    values = []
                    for cell in cells[1:]:
                        value_text = cell.get_text(strip=True).replace(',', '')
                        try:
                            value = float(value_text) if value_text else None
                            values.append(value)
                        except:
                            values.append(None)
                    
                    # 날짜와 값 매칭
                    for i, date_info in enumerate(dates):
                        if i < len(values) and values[i] is not None:
                            financial_data.append({
                                'stock_code': stock_code,
                                'year': date_info['year'],
                                'quarter': date_info.get('quarter'),
                                'item': row_label,
                                'value': values[i],
                                'date_raw': date_info['raw']
                            })
        
        logger.info(f"✅ {stock_code} 재무제표 데이터 추출 완료: {len(financial_data)}건")
        
    except Exception as e:
        logger.error(f"❌ {stock_code} 재무제표 크롤링 중 오류: {e}", exc_info=True)
    
    return financial_data

def calculate_growth_from_scraped_data(financial_data: list):
    """
    크롤링한 데이터로부터 성장률 계산
    """
    # 항목별로 그룹화
    by_item = {}
    for data in financial_data:
        item = data['item']
        if item not in by_item:
            by_item[item] = []
        by_item[item].append(data)
    
        # 각 항목별로 시간순 정렬 및 성장률 계산
        growth_data = []
        for item, data_list in by_item.items():
            # 연도/분기순 정렬 (None 처리)
            sorted_data = sorted(data_list, key=lambda x: (x['year'], x.get('quarter') or 0))
        
        for i in range(1, len(sorted_data)):
            current = sorted_data[i]
            previous = sorted_data[i-1]
            
            if previous['value'] and previous['value'] > 0:
                growth_rate = ((current['value'] - previous['value']) / previous['value']) * 100
                growth_data.append({
                    'stock_code': current['stock_code'],
                    'item': item,
                    'year': current['year'],
                    'quarter': current.get('quarter'),
                    'value': current['value'],
                    'growth_rate': growth_rate,
                    'previous_value': previous['value']
                })
    
    return growth_data

def convert_scraped_to_db_format(scraped_data: list):
    """
    크롤링한 데이터를 DB 저장 형식으로 변환
    
    Args:
        scraped_data: scrape_naver_finance_financials() 결과
    
    Returns:
        DB 저장용 데이터 리스트
    """
    # 항목별로 그룹화
    by_stock_date = {}
    
    for data in scraped_data:
        stock_code = data['stock_code']
        year = data['year']
        quarter = data.get('quarter')
        
        # 리포트 날짜 생성 (분기 마지막 날)
        if quarter:
            # 분기별: 3월, 6월, 9월, 12월 말일
            month = quarter * 3
            report_date = datetime(year, month, 1) + timedelta(days=32)
            report_date = report_date.replace(day=1) - timedelta(days=1)
            report_type = 'QUARTERLY'
        else:
            # 연간: 12월 말일
            report_date = datetime(year, 12, 31)
            report_type = 'ANNUAL'
        
        key = (stock_code, report_date, report_type)
        if key not in by_stock_date:
            by_stock_date[key] = {
                'stock_code': stock_code,
                'report_date': report_date,
                'report_type': report_type,
                'sales': None,
                'operating_profit': None,
                'net_income': None,
                'total_assets': None,
                'total_liabilities': None,
                'total_equity': None,
                'shares_outstanding': None,
                'eps': None
            }
        
        # 항목별로 값 할당
        item = data['item']
        value = data['value']
        
        if '매출액' in item:
            by_stock_date[key]['sales'] = value
        elif '영업이익' in item:
            by_stock_date[key]['operating_profit'] = value
        elif '당기순이익' in item:
            by_stock_date[key]['net_income'] = value
        elif '자산총계' in item or '총자산' in item:
            by_stock_date[key]['total_assets'] = value
        elif '부채총계' in item or '총부채' in item:
            by_stock_date[key]['total_liabilities'] = value
        elif '자본총계' in item or '총자본' in item:
            by_stock_date[key]['total_equity'] = value
    
    # 성장률 계산
    db_data = list(by_stock_date.values())
    
    # 종목별, 리포트 타입별로 정렬
    db_data.sort(key=lambda x: (x['stock_code'], x['report_type'], x['report_date']))
    
    # 성장률 계산
    for i in range(1, len(db_data)):
        current = db_data[i]
        previous = db_data[i-1]
        
        # 같은 종목, 같은 리포트 타입인 경우만
        if (current['stock_code'] == previous['stock_code'] and 
            current['report_type'] == previous['report_type']):
            
            # 매출액 성장률
            if previous.get('sales') and previous['sales'] > 0 and current.get('sales'):
                current['sales_growth'] = ((current['sales'] - previous['sales']) / previous['sales']) * 100
            else:
                current['sales_growth'] = None
            
            # EPS 성장률 (EPS가 있는 경우)
            if previous.get('eps') and previous['eps'] > 0 and current.get('eps'):
                current['eps_growth'] = ((current['eps'] - previous['eps']) / previous['eps']) * 100
            else:
                current['eps_growth'] = None
    
    return db_data

def upsert_financial_data(connection, financial_data: list):
    """재무제표 데이터를 DB에 저장"""
    if not financial_data:
        return
    
    sql_merge = """
    MERGE INTO FINANCIAL_DATA t
    USING (
      SELECT TO_DATE(:p_date, 'YYYY-MM-DD') AS report_date,
             :p_code AS stock_code,
             :p_type AS report_type,
             :p_sales AS sales,
             :p_operating_profit AS operating_profit,
             :p_net_income AS net_income,
             :p_total_assets AS total_assets,
             :p_total_liabilities AS total_liabilities,
             :p_total_equity AS total_equity,
             :p_shares AS shares_outstanding,
             :p_eps AS eps,
             :p_sales_growth AS sales_growth,
             :p_eps_growth AS eps_growth
      FROM DUAL
    ) s
    ON (t.STOCK_CODE = s.stock_code AND t.REPORT_DATE = s.report_date AND t.REPORT_TYPE = s.report_type)
    WHEN MATCHED THEN
      UPDATE SET t.SALES = s.sales,
                 t.OPERATING_PROFIT = s.operating_profit,
                 t.NET_INCOME = s.net_income,
                 t.TOTAL_ASSETS = s.total_assets,
                 t.TOTAL_LIABILITIES = s.total_liabilities,
                 t.TOTAL_EQUITY = s.total_equity,
                 t.SHARES_OUTSTANDING = s.shares_outstanding,
                 t.EPS = s.eps,
                 t.SALES_GROWTH = s.sales_growth,
                 t.EPS_GROWTH = s.eps_growth
    WHEN NOT MATCHED THEN
      INSERT (STOCK_CODE, REPORT_DATE, REPORT_TYPE, SALES, OPERATING_PROFIT, NET_INCOME,
              TOTAL_ASSETS, TOTAL_LIABILITIES, TOTAL_EQUITY, SHARES_OUTSTANDING,
              EPS, SALES_GROWTH, EPS_GROWTH)
      VALUES (s.stock_code, s.report_date, s.report_type, s.sales, s.operating_profit, s.net_income,
              s.total_assets, s.total_liabilities, s.total_equity, s.shares_outstanding,
              s.eps, s.sales_growth, s.eps_growth)
    """
    
    params = []
    for data in financial_data:
        params.append({
            "p_date": data['report_date'].strftime('%Y-%m-%d'),
            "p_code": data['stock_code'],
            "p_type": data['report_type'],
            "p_sales": data.get('sales'),
            "p_operating_profit": data.get('operating_profit'),
            "p_net_income": data.get('net_income'),
            "p_total_assets": data.get('total_assets'),
            "p_total_liabilities": data.get('total_liabilities'),
            "p_total_equity": data.get('total_equity'),
            "p_shares": data.get('shares_outstanding'),
            "p_eps": data.get('eps'),
            "p_sales_growth": data.get('sales_growth'),
            "p_eps_growth": data.get('eps_growth')
        })
    
    cur = None
    try:
        cur = connection.cursor()
        try:
            cur.execute("ALTER SESSION DISABLE PARALLEL DML")
        except Exception:
            pass
        cur.executemany(sql_merge, params)
        connection.commit()
        logger.info(f"✅ 재무제표 데이터 저장 완료: {len(params)}건")
    except Exception as e:
        logger.error(f"❌ 재무제표 데이터 저장 실패: {e}", exc_info=True)
        if connection:
            connection.rollback()
        raise
    finally:
        if cur:
            cur.close()

def main():
    """재무제표 데이터 수집 메인 함수"""
    load_dotenv()
    logger.info("--- 🤖 네이버 증권 재무제표 데이터 수집기 시작 ---")
    
    db_conn = None
    try:
        # DB 연결
        db_user = auth.get_secret(os.getenv("SECRET_ID_ORACLE_DB_USER"), os.getenv("GCP_PROJECT_ID"))
        db_password = auth.get_secret(os.getenv("SECRET_ID_ORACLE_DB_PASSWORD"), os.getenv("GCP_PROJECT_ID"))
        wallet_path = os.path.join(PROJECT_ROOT, os.getenv("OCI_WALLET_DIR_NAME", "wallet"))
        db_conn = database.get_db_connection(
            db_user=db_user,
            db_password=db_password,
            db_service_name=os.getenv("OCI_DB_SERVICE_NAME"),
            wallet_path=wallet_path
        )
        if not db_conn:
            raise RuntimeError("OCI DB 연결 실패")
        
        ensure_financial_table(db_conn)
        
        # 수집할 종목 목록: Watchlist + Portfolio (현재 관심 종목)
        watchlist = database.get_active_watchlist(db_conn)
        portfolio_items = database.get_active_portfolio(db_conn)
        
        # Watchlist와 Portfolio 종목 코드 수집
        target_codes = set(watchlist.keys())
        for item in portfolio_items:
            target_codes.add(item['code'])
        
        # KOSPI 제외
        target_codes.discard('0001')
        
        logger.info(f"--- 재무제표 데이터 수집 시작 (대상: {len(target_codes)}개 종목) ---")
        logger.info(f"   - Watchlist: {len(watchlist)}개")
        logger.info(f"   - Portfolio: {len(portfolio_items)}개")
        
        total_saved = 0
        success_count = 0
        fail_count = 0
        
        for code in sorted(target_codes):
            # 종목명 조회 (Watchlist 우선, 없으면 Portfolio에서)
            name = watchlist.get(code, {}).get('name') or next(
                (item.get('name') for item in portfolio_items if item.get('code') == code), 
                code
            )
            
            try:
                logger.info(f"   - 수집 중: {name}({code})")
                
                # 크롤링
                scraped_data = scrape_naver_finance_financials(code)
                
                if not scraped_data:
                    logger.warning(f"   ⚠️ {name}({code}): 데이터 추출 실패")
                    fail_count += 1
                    time.sleep(2)  # 과도한 요청 방지
                    continue
                
                # DB 형식으로 변환
                db_data = convert_scraped_to_db_format(scraped_data)
                
                if db_data:
                    # DB 저장
                    upsert_financial_data(db_conn, db_data)
                    total_saved += len(db_data)
                    success_count += 1
                    logger.info(f"   ✅ {name}({code}): {len(db_data)}건 저장 완료")
                else:
                    logger.warning(f"   ⚠️ {name}({code}): 변환된 데이터 없음")
                    fail_count += 1
                
                # 과도한 요청 방지 (2초 딜레이)
                time.sleep(2)
                
            except Exception as e:
                logger.error(f"   ❌ {name}({code}) 처리 중 오류: {e}", exc_info=True)
                fail_count += 1
                time.sleep(2)
                continue
        
        logger.info("--- ✅ 재무제표 데이터 수집 완료 ---")
        logger.info(f"   성공: {success_count}개 종목, 실패: {fail_count}개 종목")
        logger.info(f"   총 저장: {total_saved}건")
        
    except Exception as e:
        logger.critical(f"❌ 수집기 실행 중 치명적 오류: {e}", exc_info=True)
        sys.exit(1)
    finally:
        if db_conn:
            db_conn.close()
            logger.info("--- DB 연결 종료 ---")

if __name__ == "__main__":
    # 테스트 모드
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        test_code = "005930"  # 삼성전자
        print(f"테스트: {test_code} 재무제표 크롤링")
        
        data = scrape_naver_finance_financials(test_code)
        print(f"추출된 데이터: {len(data)}건")
        
        if data:
            growth = calculate_growth_from_scraped_data(data)
            print(f"계산된 성장률: {len(growth)}건")
            for g in growth[:5]:  # 처음 5개만 출력
                print(f"  {g['item']} ({g['year']}): {g.get('growth_rate', 0):.2f}%")
            
            # DB 형식 변환 테스트
            db_data = convert_scraped_to_db_format(data)
            print(f"\nDB 형식 변환: {len(db_data)}건")
            for d in db_data[:3]:  # 처음 3개만 출력
                print(f"  {d['stock_code']} {d['report_date']} {d['report_type']}: "
                      f"매출액={d.get('sales')}, 영업이익={d.get('operating_profit')}, "
                      f"성장률={d.get('sales_growth', 0):.2f}%")
    else:
        # 배치 수집 모드
        main()

