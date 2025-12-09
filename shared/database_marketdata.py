"""
shared/database_marketdata.py

종목 마스터, 가격 데이터, 거래/뉴스 통계 등 조회성 유틸을 분리한 모듈입니다.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

import pandas as pd

from .database_base import _get_table_name

logger = logging.getLogger(__name__)


# 종목 마스터 조회
def get_stock_by_code(connection, stock_code: str) -> Optional[Dict]:
    cursor = connection.cursor()
    cursor.execute("""
        SELECT STOCK_CODE, STOCK_NAME, SECTOR
        FROM STOCK_MASTER
        WHERE STOCK_CODE = %s
        LIMIT 1
    """, [stock_code])
    row = cursor.fetchone()
    cursor.close()
    
    if not row:
        return None
    
    if isinstance(row, dict):
        return row
    return {
        "stock_code": row[0],
        "stock_name": row[1],
        "sector": row[2],
    }


def search_stock_by_name(connection, name: str) -> Optional[Dict]:
    cursor = connection.cursor()
    cursor.execute("""
        SELECT STOCK_CODE, STOCK_NAME, SECTOR
        FROM STOCK_MASTER
        WHERE STOCK_NAME = %s
        LIMIT 1
    """, [name])
    row = cursor.fetchone()
    cursor.close()
    
    if not row:
        return None
    
    if isinstance(row, dict):
        return row
    return {
        "stock_code": row[0],
        "stock_name": row[1],
        "sector": row[2],
    }


# 최근 거래/포트폴리오/뉴스 통계 조회
def get_today_trades(connection) -> List[Dict]:
    cursor = connection.cursor()
    table_name = _get_table_name("TradeLog")
    
    today = datetime.now().strftime('%Y%m%d')
    cursor.execute(f"""
        SELECT STOCK_CODE, TRADE_TYPE, QUANTITY, PRICE, PROFIT_AMOUNT, TRADE_TIME_UTC
        FROM {table_name}
        WHERE DATE(TRADE_TIME_UTC) = %s
        ORDER BY TRADE_TIME_UTC DESC
    """, [today])
    rows = cursor.fetchall()
    cursor.close()
    
    trades = []
    for row in rows:
        if isinstance(row, dict):
            trades.append(row)
        else:
            trades.append({
                "stock_code": row[0],
                "trade_type": row[1],
                "quantity": row[2],
                "price": row[3],
                "profit_amount": row[4],
                "trade_time": row[5],
            })
    return trades


def get_daily_prices(connection, stock_code: str, limit: int = 30, table_name: str = "STOCK_DAILY_PRICES_3Y") -> pd.DataFrame:
    cursor = connection.cursor()
    cursor.execute(f"""
        SELECT PRICE_DATE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, CLOSE_PRICE, VOLUME
        FROM {table_name}
        WHERE STOCK_CODE = %s
        ORDER BY PRICE_DATE DESC
        LIMIT %s
    """, [stock_code, limit])
    rows = cursor.fetchall()
    cursor.close()
    
    if not rows:
        return pd.DataFrame()
    
    if isinstance(rows[0], dict):
        df = pd.DataFrame(rows)
    else:
        df = pd.DataFrame(rows, columns=[
            "PRICE_DATE", "OPEN_PRICE", "HIGH_PRICE", "LOW_PRICE", "CLOSE_PRICE", "VOLUME"
        ])
    df = df.iloc[::-1]  # 날짜 오름차순
    return df
