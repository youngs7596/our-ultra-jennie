#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
[v1.0] scripts/collect_investor_trading.py

KRX(pykrx 라이브러리)를 통해 외국인/기관 순매수 데이터를 수집하여
`STOCK_INVESTOR_TRADING` 테이블에 저장합니다.

데이터 소스: KRX 정보데이터시스템 (pykrx 래퍼)

Usage:
    DB_TYPE=MARIADB python3 scripts/collect_investor_trading.py --days 365 --codes 100
    DB_TYPE=MARIADB python3 scripts/collect_investor_trading.py --days 730 --codes 200
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from typing import Dict, List

from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(PROJECT_ROOT)

import shared.auth as auth
import shared.database as database
from shared.hybrid_scoring.schema import execute_upsert

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

TABLE_NAME = "STOCK_INVESTOR_TRADING"


def _is_mariadb() -> bool:
    return os.getenv("DB_TYPE", "ORACLE").upper() == "MARIADB"


def ensure_table_exists(connection):
    """테이블이 없으면 생성"""
    cursor = connection.cursor()
    try:
        if _is_mariadb():
            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
                    ID INT AUTO_INCREMENT PRIMARY KEY,
                    TRADE_DATE DATE NOT NULL,
                    STOCK_CODE VARCHAR(20) NOT NULL,
                    STOCK_NAME VARCHAR(100),
                    FOREIGN_BUY BIGINT DEFAULT 0 COMMENT '외국인 매수량',
                    FOREIGN_SELL BIGINT DEFAULT 0 COMMENT '외국인 매도량',
                    FOREIGN_NET_BUY BIGINT DEFAULT 0 COMMENT '외국인 순매수량',
                    INSTITUTION_BUY BIGINT DEFAULT 0 COMMENT '기관 매수량',
                    INSTITUTION_SELL BIGINT DEFAULT 0 COMMENT '기관 매도량',
                    INSTITUTION_NET_BUY BIGINT DEFAULT 0 COMMENT '기관 순매수량',
                    INDIVIDUAL_BUY BIGINT DEFAULT 0 COMMENT '개인 매수량',
                    INDIVIDUAL_SELL BIGINT DEFAULT 0 COMMENT '개인 매도량',
                    INDIVIDUAL_NET_BUY BIGINT DEFAULT 0 COMMENT '개인 순매수량',
                    CLOSE_PRICE INT DEFAULT 0 COMMENT '종가',
                    VOLUME BIGINT DEFAULT 0 COMMENT '거래량',
                    SCRAPED_AT DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY UK_DATE_CODE (TRADE_DATE, STOCK_CODE)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='외국인/기관 투자자별 매매 데이터'
            """)
        else:
            try:
                cursor.execute(f"SELECT 1 FROM {TABLE_NAME} WHERE ROWNUM=1")
            except Exception:
                cursor.execute(f"""
                    CREATE TABLE {TABLE_NAME} (
                        ID NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                        TRADE_DATE DATE NOT NULL,
                        STOCK_CODE VARCHAR2(20) NOT NULL,
                        STOCK_NAME VARCHAR2(100),
                        FOREIGN_BUY NUMBER DEFAULT 0,
                        FOREIGN_SELL NUMBER DEFAULT 0,
                        FOREIGN_NET_BUY NUMBER DEFAULT 0,
                        INSTITUTION_BUY NUMBER DEFAULT 0,
                        INSTITUTION_SELL NUMBER DEFAULT 0,
                        INSTITUTION_NET_BUY NUMBER DEFAULT 0,
                        INDIVIDUAL_BUY NUMBER DEFAULT 0,
                        INDIVIDUAL_SELL NUMBER DEFAULT 0,
                        INDIVIDUAL_NET_BUY NUMBER DEFAULT 0,
                        CLOSE_PRICE NUMBER DEFAULT 0,
                        VOLUME NUMBER DEFAULT 0,
                        SCRAPED_AT TIMESTAMP DEFAULT SYSTIMESTAMP,
                        CONSTRAINT UK_DATE_CODE UNIQUE (TRADE_DATE, STOCK_CODE)
                    )
                """)
        connection.commit()
        logger.info(f"✅ 테이블 확인 완료: {TABLE_NAME}")
    except Exception as e:
        logger.error(f"❌ 테이블 생성 실패: {e}")
        connection.rollback()
        raise
    finally:
        cursor.close()


def get_db_config():
    if _is_mariadb():
        return {
            "db_user": "dummy",
            "db_password": "dummy",
            "db_service_name": "dummy",
            "wallet_path": "dummy",
        }
    project_id = os.getenv("GCP_PROJECT_ID")
    db_user = auth.get_secret(os.getenv("SECRET_ID_ORACLE_DB_USER"), project_id)
    db_password = auth.get_secret(os.getenv("SECRET_ID_ORACLE_DB_PASSWORD"), project_id)
    wallet_path = os.path.join(PROJECT_ROOT, os.getenv("OCI_WALLET_DIR_NAME", "wallet"))
    return {
        "db_user": db_user,
        "db_password": db_password,
        "db_service_name": os.getenv("OCI_DB_SERVICE_NAME"),
        "wallet_path": wallet_path,
    }


def load_stock_codes(limit: int = None) -> List[str]:
    """KOSPI 종목 코드 로드"""
    import FinanceDataReader as fdr
    codes = fdr.StockListing("KOSPI")["Code"].tolist()
    if limit:
        return codes[:limit]
    return codes


def fetch_investor_trading_by_date(date_str: str, stock_codes: List[str]) -> List[Dict]:
    """
    특정 날짜의 투자자별 매매 데이터 조회 (pykrx 사용)
    
    Args:
        date_str: 날짜 (YYYYMMDD 형식)
        stock_codes: 종목 코드 리스트
    
    Returns:
        [{'stock_code': ..., 'foreign_buy': ..., ...}]
    """
    from pykrx import stock as pykrx_stock
    
    results = []
    
    try:
        # 전체 종목의 투자자별 순매수 데이터
        df = pykrx_stock.get_market_net_purchases_of_equities_by_ticker(
            date_str, date_str, market="KOSPI"
        )
        
        if df.empty:
            return results
        
        for code in stock_codes:
            if code not in df.index:
                continue
            
            row = df.loc[code]
            
            # 컬럼명이 버전마다 다를 수 있어 try-except
            try:
                results.append({
                    'trade_date': datetime.strptime(date_str, "%Y%m%d").date(),
                    'stock_code': code,
                    'stock_name': row.get('종목명', ''),
                    'foreign_buy': int(row.get('외국인_매수', row.get('외국인합계_매수', 0)) or 0),
                    'foreign_sell': int(row.get('외국인_매도', row.get('외국인합계_매도', 0)) or 0),
                    'foreign_net_buy': int(row.get('외국인_순매수', row.get('외국인합계_순매수', 0)) or 0),
                    'institution_buy': int(row.get('기관_매수', row.get('기관합계_매수', 0)) or 0),
                    'institution_sell': int(row.get('기관_매도', row.get('기관합계_매도', 0)) or 0),
                    'institution_net_buy': int(row.get('기관_순매수', row.get('기관합계_순매수', 0)) or 0),
                    'individual_buy': int(row.get('개인_매수', 0) or 0),
                    'individual_sell': int(row.get('개인_매도', 0) or 0),
                    'individual_net_buy': int(row.get('개인_순매수', 0) or 0),
                    'close_price': 0,  # 별도 조회 필요
                    'volume': 0,
                })
            except Exception as e:
                logger.debug(f"   ⚠️ {code} 파싱 실패: {e}")
                continue
    
    except Exception as e:
        logger.warning(f"   ⚠️ {date_str} 데이터 조회 실패: {e}")
    
    return results


def fetch_investor_trading_by_stock(stock_code: str, start_date: str, end_date: str) -> List[Dict]:
    """
    특정 종목의 기간별 투자자 매매 데이터 조회
    
    Args:
        stock_code: 종목 코드
        start_date: 시작일 (YYYYMMDD)
        end_date: 종료일 (YYYYMMDD)
    
    Returns:
        [{'trade_date': ..., 'foreign_net_buy': ..., ...}]
    """
    from pykrx import stock as pykrx_stock
    
    results = []
    
    try:
        # 종목별 투자자 매매 동향
        df = pykrx_stock.get_market_trading_value_by_date(
            start_date, end_date, stock_code, detail=True
        )
        
        if df.empty:
            return results
        
        for date_idx, row in df.iterrows():
            try:
                trade_date = date_idx.date() if hasattr(date_idx, 'date') else date_idx
                
                results.append({
                    'trade_date': trade_date,
                    'stock_code': stock_code,
                    'stock_name': '',
                    'foreign_buy': int(row.get('외국인', {}).get('매수', 0) or 0) if isinstance(row.get('외국인'), dict) else 0,
                    'foreign_sell': int(row.get('외국인', {}).get('매도', 0) or 0) if isinstance(row.get('외국인'), dict) else 0,
                    'foreign_net_buy': int(row.get('외국인', 0) or 0) if not isinstance(row.get('외국인'), dict) else 0,
                    'institution_buy': 0,
                    'institution_sell': 0,
                    'institution_net_buy': int(row.get('기관합계', row.get('기관', 0)) or 0),
                    'individual_buy': 0,
                    'individual_sell': 0,
                    'individual_net_buy': int(row.get('개인', 0) or 0),
                    'close_price': 0,
                    'volume': 0,
                })
            except Exception as e:
                logger.debug(f"   ⚠️ {stock_code} {date_idx} 파싱 실패: {e}")
                continue
    
    except Exception as e:
        logger.warning(f"   ⚠️ {stock_code} 데이터 조회 실패: {e}")
    
    return results


def save_trading_data(connection, data_list: List[Dict]) -> int:
    """투자자 매매 데이터 저장"""
    if not data_list:
        return 0
    
    cursor = connection.cursor()
    saved = 0
    
    for data in data_list:
        try:
            columns = [
                "TRADE_DATE", "STOCK_CODE", "STOCK_NAME",
                "FOREIGN_BUY", "FOREIGN_SELL", "FOREIGN_NET_BUY",
                "INSTITUTION_BUY", "INSTITUTION_SELL", "INSTITUTION_NET_BUY",
                "INDIVIDUAL_BUY", "INDIVIDUAL_SELL", "INDIVIDUAL_NET_BUY",
                "CLOSE_PRICE", "VOLUME", "SCRAPED_AT"
            ]
            values = (
                data['trade_date'],
                data['stock_code'],
                data.get('stock_name', ''),
                data.get('foreign_buy', 0),
                data.get('foreign_sell', 0),
                data.get('foreign_net_buy', 0),
                data.get('institution_buy', 0),
                data.get('institution_sell', 0),
                data.get('institution_net_buy', 0),
                data.get('individual_buy', 0),
                data.get('individual_sell', 0),
                data.get('individual_net_buy', 0),
                data.get('close_price', 0),
                data.get('volume', 0),
                datetime.now(),
            )
            
            execute_upsert(
                cursor,
                TABLE_NAME,
                columns,
                values,
                unique_keys=["TRADE_DATE", "STOCK_CODE"],
                update_columns=[
                    "STOCK_NAME", "FOREIGN_BUY", "FOREIGN_SELL", "FOREIGN_NET_BUY",
                    "INSTITUTION_BUY", "INSTITUTION_SELL", "INSTITUTION_NET_BUY",
                    "INDIVIDUAL_BUY", "INDIVIDUAL_SELL", "INDIVIDUAL_NET_BUY",
                    "CLOSE_PRICE", "VOLUME", "SCRAPED_AT"
                ]
            )
            saved += 1
        except Exception as e:
            logger.debug(f"   ⚠️ 저장 실패 ({data.get('stock_code')}): {e}")
    
    connection.commit()
    cursor.close()
    return saved


def parse_args():
    parser = argparse.ArgumentParser(description="외국인/기관 투자자 매매 데이터 수집기")
    parser.add_argument("--days", type=int, default=365, help="수집 기간(일)")
    parser.add_argument("--codes", type=int, default=100, help="수집할 종목 수 (KOSPI 상위)")
    parser.add_argument("--mode", type=str, default="by_stock", 
                        choices=["by_stock", "by_date"],
                        help="수집 모드: by_stock(종목별), by_date(날짜별)")
    parser.add_argument("--sleep", type=float, default=0.5, help="요청 간 대기 시간(초)")
    return parser.parse_args()


def main():
    load_dotenv()
    args = parse_args()
    
    # secrets.json 경로 설정 (프로젝트 루트)
    if not os.getenv("SECRETS_FILE"):
        os.environ["SECRETS_FILE"] = os.path.join(PROJECT_ROOT, "secrets.json")
    
    logger.info("=" * 60)
    logger.info(f"📈 외국인/기관 매매 데이터 수집 시작")
    logger.info(f"   - 기간: {args.days}일")
    logger.info(f"   - 종목 수: {args.codes}개")
    logger.info(f"   - 모드: {args.mode}")
    logger.info("=" * 60)
    
    # pykrx 설치 확인
    try:
        from pykrx import stock as pykrx_stock
    except ImportError:
        logger.error("❌ pykrx 라이브러리가 필요합니다. (pip install pykrx)")
        return
    
    # DB 연결
    db_config = get_db_config()
    conn = database.get_db_connection(**db_config)
    if not conn:
        logger.error("❌ DB 연결 실패")
        return
    
    ensure_table_exists(conn)
    
    # 종목 코드 로드
    stock_codes = load_stock_codes(args.codes)
    logger.info(f"   📊 대상 종목: {len(stock_codes)}개")
    
    # 날짜 범위
    end_date = datetime.now()
    start_date = end_date - timedelta(days=args.days)
    start_str = start_date.strftime("%Y%m%d")
    end_str = end_date.strftime("%Y%m%d")
    
    total_saved = 0
    
    if args.mode == "by_stock":
        # 종목별로 수집 (더 안정적)
        for idx, code in enumerate(stock_codes, start=1):
            try:
                logger.info(f"[{idx}/{len(stock_codes)}] {code} 수급 데이터 수집 ({start_str} ~ {end_str})")
                
                data_list = fetch_investor_trading_by_stock(code, start_str, end_str)
                saved = save_trading_data(conn, data_list)
                total_saved += saved
                
                logger.info(f"   ↳ {len(data_list)}건 조회, {saved}건 저장 (누적: {total_saved})")
                
                time.sleep(args.sleep)
            except Exception as e:
                logger.error(f"   ❌ {code} 수집 실패: {e}")
    else:
        # 날짜별로 수집
        current_date = start_date
        while current_date <= end_date:
            date_str = current_date.strftime("%Y%m%d")
            
            # 주말 건너뛰기
            if current_date.weekday() >= 5:
                current_date += timedelta(days=1)
                continue
            
            try:
                logger.info(f"📅 {date_str} 수급 데이터 수집")
                
                data_list = fetch_investor_trading_by_date(date_str, stock_codes)
                saved = save_trading_data(conn, data_list)
                total_saved += saved
                
                logger.info(f"   ↳ {len(data_list)}건 조회, {saved}건 저장 (누적: {total_saved})")
                
                time.sleep(args.sleep)
            except Exception as e:
                logger.error(f"   ❌ {date_str} 수집 실패: {e}")
            
            current_date += timedelta(days=1)
    
    conn.close()
    
    logger.info("=" * 60)
    logger.info(f"✅ 외국인/기관 매매 데이터 수집 완료 (총 {total_saved}건)")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()

