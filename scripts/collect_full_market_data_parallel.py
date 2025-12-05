#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Version: v3.8
# 작업 LLM: Claude Opus 4.5
"""
[v3.8] scripts/collect_full_market_data_parallel.py
KOSPI 전 종목의 700일치 데이터를 병렬로 수집합니다.
- MariaDB/Oracle 하이브리드 지원 (Claude Opus 4.5)
"""

import os
import sys
import logging
import time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import FinanceDataReader as fdr
from dotenv import load_dotenv

# 프로젝트 루트 경로 설정
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(PROJECT_ROOT)

import shared.auth as auth
import shared.database as database
from shared.kis.client import KISClient
from shared.kis.market_data import MarketData

# 로깅 설정
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 전역 설정
MAX_WORKERS = 5  # 동시 실행 스레드 수 (API 제한 고려)
DAYS_TO_COLLECT = 711

def _is_mariadb() -> bool:
    """현재 DB 타입이 MariaDB인지 확인"""
    return os.getenv("DB_TYPE", "ORACLE").upper() == "MARIADB"

def collect_stock_data(code, kis_client, db_config):
    """단일 종목 데이터 수집 및 저장 (스레드에서 실행)"""
    conn = None
    try:
        # DB 연결 (스레드별 독립 연결)
        conn = database.get_db_connection(**db_config)
        if not conn:
            logger.error(f"❌ [{code}] DB 연결 실패")
            return False
            
        cur = conn.cursor()
        
        # Oracle 전용: 병렬 DML 비활성화 (ORA-12838 방지)
        if not _is_mariadb():
            try:
                cur.execute("ALTER SESSION DISABLE PARALLEL DML")
            except:
                pass
            
        market_data = MarketData(kis_client)
        
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=DAYS_TO_COLLECT)).strftime("%Y%m%d")
        
        # 데이터 조회 (페이지네이션 적용됨)
        rows = market_data.get_stock_history_by_chart(code, start_date=start_date, end_date=end_date)
        
        if not rows:
            logger.warning(f"⚠️ [{code}] 데이터 없음")
            return False
            
        # DB 저장
        if _is_mariadb():
            # MariaDB: INSERT ... ON DUPLICATE KEY UPDATE
            sql = """
            INSERT INTO STOCK_DAILY_PRICES_3Y 
                (STOCK_CODE, PRICE_DATE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, CLOSE_PRICE, VOLUME)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                OPEN_PRICE = VALUES(OPEN_PRICE),
                HIGH_PRICE = VALUES(HIGH_PRICE),
                LOW_PRICE = VALUES(LOW_PRICE),
                CLOSE_PRICE = VALUES(CLOSE_PRICE),
                VOLUME = VALUES(VOLUME)
            """
            for row in rows:
                cur.execute(sql, (
                    code, row['date'], row['open'], row['high'], 
                    row['low'], row['close'], row['volume']
                ))
        else:
            # Oracle: MERGE INTO
            for row in rows:
                params = {
                    'code': code,
                    'pdate': row['date'],
                    'open': row['open'],
                    'high': row['high'],
                    'low': row['low'],
                    'close': row['close'],
                    'vol': row['volume']
                }
                
                merge_sql = """
                MERGE INTO STOCK_DAILY_PRICES_3Y target
                USING (SELECT :code AS STOCK_CODE, TO_DATE(:pdate, 'YYYY-MM-DD') AS PRICE_DATE FROM DUAL) source
                ON (target.STOCK_CODE = source.STOCK_CODE AND target.PRICE_DATE = source.PRICE_DATE)
                WHEN MATCHED THEN
                    UPDATE SET 
                        OPEN_PRICE = :open,
                        HIGH_PRICE = :high,
                        LOW_PRICE = :low,
                        CLOSE_PRICE = :close,
                        VOLUME = :vol
                WHEN NOT MATCHED THEN
                    INSERT (STOCK_CODE, PRICE_DATE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, CLOSE_PRICE, VOLUME)
                    VALUES (:code, TO_DATE(:pdate, 'YYYY-MM-DD'), :open, :high, :low, :close, :vol)
                """
                cur.execute(merge_sql, params)
        
        conn.commit()
        cur.close()
        return True
        
    except Exception as e:
        logger.error(f"❌ [{code}] 처리 실패: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()

def main():
    load_dotenv()
    
    # DB 타입 확인
    db_type = os.getenv("DB_TYPE", "ORACLE").upper()
    logger.info(f"DB 타입: {db_type}")
    
    # KIS Client 초기화 (공유)
    project_id = os.getenv("GCP_PROJECT_ID")
    trading_mode = os.getenv("TRADING_MODE", "MOCK")
    
    if trading_mode == "REAL":
        app_key = auth.get_secret(os.getenv("REAL_SECRET_ID_APP_KEY"), project_id)
        app_secret = auth.get_secret(os.getenv("REAL_SECRET_ID_APP_SECRET"), project_id)
        account_prefix = auth.get_secret(os.getenv("REAL_SECRET_ID_ACCOUNT_PREFIX"), project_id)
        base_url = os.getenv("KIS_BASE_URL_REAL")
    else:
        app_key = auth.get_secret(os.getenv("MOCK_SECRET_ID_APP_KEY"), project_id)
        app_secret = auth.get_secret(os.getenv("MOCK_SECRET_ID_APP_SECRET"), project_id)
        account_prefix = auth.get_secret(os.getenv("MOCK_SECRET_ID_ACCOUNT_PREFIX"), project_id)
        base_url = os.getenv("KIS_BASE_URL_MOCK")
        
    account_suffix = os.getenv("KIS_ACCOUNT_SUFFIX")
    
    kis_client = KISClient(
        app_key=app_key,
        app_secret=app_secret,
        base_url=base_url,
        account_prefix=account_prefix,
        account_suffix=account_suffix,
        trading_mode=trading_mode
    )
    
    if not kis_client.authenticate():
        logger.error("KIS API 인증 실패")
        return

    # DB 설정 (스레드에 전달용)
    # MariaDB는 환경변수에서 직접 읽으므로 Oracle 형식의 더미 값 전달
    if _is_mariadb():
        db_config = {
            "db_user": "dummy",  # MariaDB는 환경변수에서 읽음
            "db_password": "dummy",
            "db_service_name": "dummy",
            "wallet_path": "dummy"
        }
    else:
        db_user = auth.get_secret(os.getenv("SECRET_ID_ORACLE_DB_USER"), project_id)
        db_password = auth.get_secret(os.getenv("SECRET_ID_ORACLE_DB_PASSWORD"), project_id)
        wallet_path = os.path.join(PROJECT_ROOT, os.getenv("OCI_WALLET_DIR_NAME", "wallet"))
        
        db_config = {
            "db_user": db_user,
            "db_password": db_password,
            "db_service_name": os.getenv("OCI_DB_SERVICE_NAME"),
            "wallet_path": wallet_path
        }

    # KOSPI 종목 리스트 가져오기
    logger.info("FinanceDataReader를 사용하여 KOSPI 종목 리스트를 가져옵니다...")
    df_krx = fdr.StockListing('KOSPI')
    codes = df_krx['Code'].tolist()
    logger.info(f"✅ KOSPI 종목 {len(codes)}개 확보 완료.")
    
    logger.info(f"=== KOSPI 전 종목({len(codes)}개) 병렬 수집 시작 (Workers: {MAX_WORKERS}) ===")
    
    success_count = 0
    fail_count = 0
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_code = {executor.submit(collect_stock_data, code, kis_client, db_config): code for code in codes}
        
        for i, future in enumerate(as_completed(future_to_code)):
            code = future_to_code[future]
            try:
                result = future.result()
                if result:
                    success_count += 1
                    if success_count % 10 == 0:
                        logger.info(f"[{i+1}/{len(codes)}] 진행 중... 성공: {success_count}, 실패: {fail_count}")
                else:
                    fail_count += 1
            except Exception as e:
                logger.error(f"❌ [{code}] 예외 발생: {e}")
                fail_count += 1
                
    logger.info(f"=== 수집 완료: 성공 {success_count}, 실패 {fail_count} ===")

if __name__ == "__main__":
    main()
