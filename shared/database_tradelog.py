"""
shared/database_tradelog.py

트레이드 로그/거래 관련 CRUD 및 조회를 분리한 모듈입니다.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Dict, List

from .database_base import _get_table_name

logger = logging.getLogger(__name__)


def record_trade(connection, stock_code: str, trade_type: str, quantity: int,
                 price: float, reason: str = "", extra: Dict = None):
    cursor = connection.cursor()
    table_name = _get_table_name("TradeLog")
    
    extra_json = json.dumps(extra, default=str) if extra else None
    now_ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    
    cursor.execute(f"""
        INSERT INTO {table_name}
        (STOCK_CODE, TRADE_TYPE, QUANTITY, PRICE, REASON, EXTRA, TRADE_TIME_UTC)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, [stock_code, trade_type, quantity, price, reason, extra_json, now_ts])
    
    connection.commit()
    cursor.close()


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
