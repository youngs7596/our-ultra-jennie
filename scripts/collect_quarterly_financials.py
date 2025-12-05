#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
[v1.0] 분기별 재무 데이터 수집기
- 네이버 금융에서 분기별 EPS, BPS, 순이익, 자기자본 수집
- 분기말 주가와 조합하여 PER/PBR/ROE 계산
- FINANCIAL_METRICS_QUARTERLY 테이블에 저장

작업 LLM: Claude Opus 4.5
"""

import os
import sys
import requests
from bs4 import BeautifulSoup
import time
import logging
import argparse
from datetime import datetime, timedelta
from dotenv import load_dotenv
import re
import pandas as pd

# 프로젝트 루트 경로 설정
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

os.environ["SECRETS_FILE"] = os.path.join(PROJECT_ROOT, "secrets.json")

import shared.database as database

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# MariaDB용 DDL
DDL_FINANCIAL_METRICS_QUARTERLY = """
CREATE TABLE IF NOT EXISTS FINANCIAL_METRICS_QUARTERLY (
    STOCK_CODE VARCHAR(20) NOT NULL,
    QUARTER_DATE DATE NOT NULL,
    QUARTER_NAME VARCHAR(20),
    EPS DECIMAL(15,2),
    BPS DECIMAL(15,2),
    NET_INCOME BIGINT,
    TOTAL_EQUITY BIGINT,
    CLOSE_PRICE DECIMAL(15,2),
    PER DECIMAL(10,2),
    PBR DECIMAL(10,2),
    ROE DECIMAL(10,4),
    CREATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UPDATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (STOCK_CODE, QUARTER_DATE)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def _is_mariadb() -> bool:
    return os.getenv("DB_TYPE", "ORACLE").upper() == "MARIADB"


def ensure_table_exists(conn):
    """테이블 생성"""
    cursor = conn.cursor()
    try:
        if _is_mariadb():
            cursor.execute("SHOW TABLES LIKE 'financial_metrics_quarterly'")
            exists = cursor.fetchone() is not None
        else:
            cursor.execute("SELECT COUNT(*) FROM user_tables WHERE table_name = 'FINANCIAL_METRICS_QUARTERLY'")
            row = cursor.fetchone()
            exists = (list(row.values())[0] if isinstance(row, dict) else row[0]) > 0
        
        if not exists:
            logger.info("테이블 'FINANCIAL_METRICS_QUARTERLY' 생성 중...")
            cursor.execute(DDL_FINANCIAL_METRICS_QUARTERLY)
            conn.commit()
            logger.info("✅ 테이블 생성 완료")
        else:
            logger.info("✅ 테이블 이미 존재")
    finally:
        cursor.close()


def parse_number(text: str) -> float:
    """숫자 문자열 파싱 (콤마, 단위 처리)"""
    if not text or text.strip() in ['', '-', 'N/A', 'nan']:
        return None
    
    text = text.strip().replace(',', '').replace(' ', '')
    
    # 음수 처리
    is_negative = False
    if text.startswith('-') or text.startswith('△') or text.startswith('▲'):
        is_negative = True
        text = text.lstrip('-△▲')
    
    try:
        value = float(text)
        return -value if is_negative else value
    except:
        return None


def scrape_naver_financial_summary(stock_code: str) -> list:
    """
    네이버 금융 메인 페이지에서 분기별/연별 재무 데이터 수집
    
    수집 항목: EPS, BPS, PER, PBR, ROE, 당기순이익
    """
    url = f"https://finance.naver.com/item/main.naver?code={stock_code}"
    
    results = []
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        response = requests.get(url, headers=headers, timeout=15)
        
        if response.status_code != 200:
            logger.warning(f"⚠️ {stock_code} 페이지 접근 실패: {response.status_code}")
            return results
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # EPS/BPS가 포함된 테이블 찾기
        target_table = None
        for table in soup.find_all('table'):
            table_text = table.get_text()
            if 'EPS' in table_text and 'BPS' in table_text and 'PER' in table_text:
                target_table = table
                break
        
        if not target_table:
            logger.warning(f"⚠️ {stock_code} 재무 테이블 없음")
            return results
        
        rows = target_table.find_all('tr')
        
        # 날짜 행 찾기 (2022.12, 2023.12, ... 형식)
        date_row = None
        for row in rows:
            cells = row.find_all(['th', 'td'])
            row_text = ' '.join([c.get_text(strip=True) for c in cells])
            if re.search(r'\d{4}\.\d{2}', row_text) and len(cells) > 5:
                date_row = cells
                break
        
        if not date_row:
            logger.warning(f"⚠️ {stock_code} 날짜 행 없음")
            return results
        
        # 날짜 파싱 (추정치 '(E)' 제외)
        quarters = []
        for cell in date_row:
            text = cell.get_text(strip=True)
            # 추정치 제외
            if '(E)' in text:
                quarters.append(None)
                continue
            # 날짜 형식 확인 (2024.12, 2024.09 등)
            match = re.match(r'(\d{4})\.(\d{2})', text)
            if match:
                quarters.append(text)
            else:
                quarters.append(None)
        
        # 데이터 저장용 딕셔너리
        quarter_data = {q: {} for q in quarters if q}
        
        # 각 행에서 데이터 추출
        target_fields = {
            'EPS(원)': 'eps',
            'BPS(원)': 'bps',
            'PER(배)': 'per',
            'PBR(배)': 'pbr',
            'ROE(지배주주)': 'roe',
            '당기순이익': 'net_income',
            '자본총계': 'total_equity',
        }
        
        for row in rows:
            cells = row.find_all(['th', 'td'])
            if len(cells) < 2:
                continue
            
            field_name = cells[0].get_text(strip=True)
            
            # 필드 매칭
            matched_key = None
            for target, key in target_fields.items():
                if target in field_name:
                    matched_key = key
                    break
            
            if not matched_key:
                continue
            
            # 각 분기별 값 추출
            for i, cell in enumerate(cells[1:]):
                if i >= len(quarters) or quarters[i] is None:
                    continue
                
                q = quarters[i]
                value = parse_number(cell.get_text(strip=True))
                
                if value is not None:
                    quarter_data[q][matched_key] = value
        
        # 결과 변환 (유효한 데이터만)
        for quarter_name, data in quarter_data.items():
            if not data or not quarter_name:
                continue
            
            # 최소한 EPS 또는 PER이 있어야 함
            if not data.get('eps') and not data.get('per'):
                continue
            
            quarter_date = parse_quarter_date(quarter_name)
            if not quarter_date:
                continue
            
            results.append({
                'stock_code': stock_code,
                'quarter_date': quarter_date,
                'quarter_name': quarter_name,
                'eps': data.get('eps'),
                'bps': data.get('bps'),
                'per': data.get('per'),
                'pbr': data.get('pbr'),
                'roe': data.get('roe'),
                'net_income': data.get('net_income'),
                'total_equity': data.get('total_equity'),
            })
        
        # 날짜순 정렬
        results.sort(key=lambda x: x['quarter_date'])
        
        logger.info(f"   ✅ {stock_code}: {len(results)}개 분기 데이터 수집")
        
    except Exception as e:
        logger.error(f"❌ {stock_code} 크롤링 오류: {e}")
    
    return results


def parse_quarter_date(quarter_str: str) -> str:
    """분기 문자열을 날짜로 변환 (YYYY-MM-DD)"""
    try:
        # "2024.09" 형식
        match = re.search(r'(\d{4})[./](\d{1,2})', quarter_str)
        if match:
            year = int(match.group(1))
            month = int(match.group(2))
            # 분기말 날짜로 변환
            if month <= 3:
                return f"{year}-03-31"
            elif month <= 6:
                return f"{year}-06-30"
            elif month <= 9:
                return f"{year}-09-30"
            else:
                return f"{year}-12-31"
        
        # "24.3Q" 또는 "2024년 3분기" 형식
        match = re.search(r'(\d{2,4}).*?(\d)[QE분기]', quarter_str)
        if match:
            year = int(match.group(1))
            if year < 100:
                year += 2000
            quarter = int(match.group(2))
            month = quarter * 3
            day = 31 if month in [3, 12] else 30
            return f"{year}-{month:02d}-{day}"
        
        return None
    except:
        return None


def get_quarter_end_prices(conn, stock_code: str, quarter_dates: list) -> dict:
    """분기말 주가 조회"""
    if not quarter_dates:
        return {}
    
    cursor = conn.cursor()
    prices = {}
    
    try:
        for q_date in quarter_dates:
            # 분기말 날짜 또는 가장 가까운 거래일 주가 조회
            if _is_mariadb():
                cursor.execute("""
                    SELECT CLOSE_PRICE, PRICE_DATE
                    FROM STOCK_DAILY_PRICES_3Y
                    WHERE STOCK_CODE = %s 
                      AND PRICE_DATE <= %s
                    ORDER BY PRICE_DATE DESC
                    LIMIT 1
                """, (stock_code, q_date))
            else:
                cursor.execute("""
                    SELECT CLOSE_PRICE, PRICE_DATE
                    FROM STOCK_DAILY_PRICES_3Y
                    WHERE STOCK_CODE = :code 
                      AND PRICE_DATE <= TO_DATE(:qdate, 'YYYY-MM-DD')
                    ORDER BY PRICE_DATE DESC
                    FETCH FIRST 1 ROW ONLY
                """, {'code': stock_code, 'qdate': q_date})
            
            row = cursor.fetchone()
            if row:
                vals = list(row.values()) if isinstance(row, dict) else list(row)
                prices[q_date] = float(vals[0]) if vals[0] else None
    finally:
        cursor.close()
    
    return prices


def calculate_metrics(data: dict, close_price: float) -> dict:
    """PER/PBR/ROE 계산 (주어진 데이터가 없으면 직접 계산)"""
    result = data.copy()
    result['close_price'] = close_price
    
    eps = data.get('eps')
    bps = data.get('bps')
    net_income = data.get('net_income')
    total_equity = data.get('total_equity')
    
    # PER 계산 (주가 / EPS)
    if not result.get('per') and eps and eps != 0 and close_price:
        result['per'] = round(close_price / eps, 2)
    
    # PBR 계산 (주가 / BPS)
    if not result.get('pbr') and bps and bps != 0 and close_price:
        result['pbr'] = round(close_price / bps, 2)
    
    # ROE 계산 (순이익 / 자기자본 * 100)
    if not result.get('roe') and net_income and total_equity and total_equity != 0:
        # 순이익과 자본총계가 억 단위일 수 있음
        result['roe'] = round((net_income / total_equity) * 100, 4)
    
    return result


def save_quarterly_metrics(conn, metrics_list: list) -> int:
    """분기별 지표 저장"""
    if not metrics_list:
        return 0
    
    cursor = conn.cursor()
    saved = 0
    
    try:
        for m in metrics_list:
            if _is_mariadb():
                sql = """
                INSERT INTO FINANCIAL_METRICS_QUARTERLY 
                    (STOCK_CODE, QUARTER_DATE, QUARTER_NAME, EPS, BPS, NET_INCOME, 
                     TOTAL_EQUITY, CLOSE_PRICE, PER, PBR, ROE)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    QUARTER_NAME = VALUES(QUARTER_NAME),
                    EPS = VALUES(EPS),
                    BPS = VALUES(BPS),
                    NET_INCOME = VALUES(NET_INCOME),
                    TOTAL_EQUITY = VALUES(TOTAL_EQUITY),
                    CLOSE_PRICE = VALUES(CLOSE_PRICE),
                    PER = VALUES(PER),
                    PBR = VALUES(PBR),
                    ROE = VALUES(ROE),
                    UPDATED_AT = CURRENT_TIMESTAMP
                """
                cursor.execute(sql, (
                    m.get('stock_code'),
                    m.get('quarter_date'),
                    m.get('quarter_name'),
                    m.get('eps'),
                    m.get('bps'),
                    m.get('net_income'),
                    m.get('total_equity'),
                    m.get('close_price'),
                    m.get('per'),
                    m.get('pbr'),
                    m.get('roe'),
                ))
            else:
                # Oracle MERGE
                sql = """
                MERGE INTO FINANCIAL_METRICS_QUARTERLY t
                USING (SELECT :code AS STOCK_CODE, TO_DATE(:qdate, 'YYYY-MM-DD') AS QUARTER_DATE FROM DUAL) s
                ON (t.STOCK_CODE = s.STOCK_CODE AND t.QUARTER_DATE = s.QUARTER_DATE)
                WHEN MATCHED THEN UPDATE SET
                    QUARTER_NAME = :qname, EPS = :eps, BPS = :bps, NET_INCOME = :net_income,
                    TOTAL_EQUITY = :total_equity, CLOSE_PRICE = :close_price,
                    PER = :per, PBR = :pbr, ROE = :roe
                WHEN NOT MATCHED THEN INSERT
                    (STOCK_CODE, QUARTER_DATE, QUARTER_NAME, EPS, BPS, NET_INCOME, TOTAL_EQUITY, CLOSE_PRICE, PER, PBR, ROE)
                VALUES (:code, TO_DATE(:qdate, 'YYYY-MM-DD'), :qname, :eps, :bps, :net_income, :total_equity, :close_price, :per, :pbr, :roe)
                """
                cursor.execute(sql, {
                    'code': m.get('stock_code'),
                    'qdate': m.get('quarter_date'),
                    'qname': m.get('quarter_name'),
                    'eps': m.get('eps'),
                    'bps': m.get('bps'),
                    'net_income': m.get('net_income'),
                    'total_equity': m.get('total_equity'),
                    'close_price': m.get('close_price'),
                    'per': m.get('per'),
                    'pbr': m.get('pbr'),
                    'roe': m.get('roe'),
                })
            saved += 1
        
        conn.commit()
    except Exception as e:
        logger.error(f"❌ 저장 실패: {e}")
        conn.rollback()
    finally:
        cursor.close()
    
    return saved


def get_kospi_codes(conn, limit: int = None) -> list:
    """KOSPI 종목 코드 조회"""
    cursor = conn.cursor()
    
    try:
        if _is_mariadb():
            sql = "SELECT DISTINCT STOCK_CODE FROM STOCK_DAILY_PRICES_3Y ORDER BY STOCK_CODE"
            if limit:
                sql += f" LIMIT {limit}"
        else:
            sql = "SELECT DISTINCT STOCK_CODE FROM STOCK_DAILY_PRICES_3Y ORDER BY STOCK_CODE"
            if limit:
                sql = f"SELECT * FROM ({sql}) WHERE ROWNUM <= {limit}"
        
        cursor.execute(sql)
        rows = cursor.fetchall()
        codes = []
        for row in rows:
            val = list(row.values())[0] if isinstance(row, dict) else row[0]
            codes.append(val)
        return codes
    finally:
        cursor.close()


def main():
    load_dotenv()
    
    parser = argparse.ArgumentParser(description='분기별 재무 지표 수집기')
    parser.add_argument('--codes', type=int, default=200, help='수집할 종목 수')
    parser.add_argument('--sleep', type=float, default=1.5, help='요청 간격 (초)')
    parser.add_argument('--stock', type=str, help='특정 종목 코드만 수집')
    args = parser.parse_args()
    
    logger.info("=" * 60)
    logger.info("📊 분기별 재무 지표 수집 시작")
    logger.info("=" * 60)
    
    # DB 연결
    conn = database.get_db_connection(
        db_user='dummy', db_password='dummy',
        db_service_name='dummy', wallet_path='dummy'
    )
    
    if not conn:
        logger.error("❌ DB 연결 실패")
        return
    
    try:
        # 테이블 생성
        ensure_table_exists(conn)
        
        # 종목 코드 조회
        if args.stock:
            stock_codes = [args.stock]
        else:
            stock_codes = get_kospi_codes(conn, args.codes)
        
        logger.info(f"📋 수집 대상: {len(stock_codes)}개 종목")
        
        total_saved = 0
        
        for i, code in enumerate(stock_codes, 1):
            logger.info(f"[{i}/{len(stock_codes)}] {code} 수집 중...")
            
            # 1. 네이버에서 분기별 재무 데이터 수집
            quarterly_data = scrape_naver_financial_summary(code)
            
            if not quarterly_data:
                continue
            
            # 2. 분기말 주가 조회
            quarter_dates = [d['quarter_date'] for d in quarterly_data]
            prices = get_quarter_end_prices(conn, code, quarter_dates)
            
            # 3. PER/PBR/ROE 계산
            metrics_to_save = []
            for data in quarterly_data:
                q_date = data['quarter_date']
                close_price = prices.get(q_date)
                
                if close_price:
                    metrics = calculate_metrics(data, close_price)
                    metrics_to_save.append(metrics)
            
            # 4. 저장
            saved = save_quarterly_metrics(conn, metrics_to_save)
            total_saved += saved
            
            logger.info(f"   ↳ {saved}건 저장 (누적 {total_saved})")
            
            # 요청 간격
            if i < len(stock_codes):
                time.sleep(args.sleep)
        
        logger.info("=" * 60)
        logger.info(f"✅ 분기별 재무 지표 수집 완료 (총 {total_saved}건)")
        logger.info("=" * 60)
        
    finally:
        conn.close()


if __name__ == "__main__":
    main()

