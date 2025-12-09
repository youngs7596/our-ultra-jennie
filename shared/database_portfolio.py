"""
shared/database_portfolio.py

Watchlist/Portfolio/TradeLog 등 포트폴리오 관련 CRUD를 분리한 모듈입니다.
기존 shared/database.py에서 얇은 래퍼를 통해 하위 호환을 유지합니다.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

import pandas as pd

from .database_base import _get_table_name

logger = logging.getLogger(__name__)


# ============================================================================
# Watchlist
# ============================================================================
def get_active_watchlist(connection) -> Dict[str, Dict]:
    cursor = connection.cursor()
    cursor.execute("SELECT STOCK_CODE, STOCK_NAME, IS_TRADABLE, LLM_SCORE, LLM_REASON FROM WatchList WHERE IS_TRADABLE = 1")
    rows = cursor.fetchall()
    cursor.close()
    
    watchlist = {}
    for row in rows:
        if isinstance(row, dict):
            code = row.get('STOCK_CODE') or row.get('stock_code')
            name = row.get('STOCK_NAME') or row.get('stock_name')
            is_tradable = row.get('IS_TRADABLE', True)
            llm_score = row.get('LLM_SCORE', None)
            llm_reason = row.get('LLM_REASON', None)
        else:
            code, name, is_tradable, llm_score, llm_reason = row
        watchlist[code] = {
            "code": code,
            "name": name,
            "is_tradable": is_tradable,
            "llm_score": llm_score,
            "llm_reason": llm_reason,
        }
    return watchlist


def save_to_watchlist(connection, candidates: List[Dict]):
    if not candidates:
        return
    
    cursor = connection.cursor()
    table_name = "WatchList"
    
    for c in candidates:
        code = c.get('code')
        name = c.get('name')
        is_tradable = c.get('is_tradable', True)
        llm_score = c.get('llm_score', 50)
        llm_reason = c.get('llm_reason', '')
        
        cursor.execute(f"""
            INSERT INTO {table_name} (STOCK_CODE, STOCK_NAME, IS_TRADABLE, LLM_SCORE, LLM_REASON)
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                STOCK_NAME = VALUES(STOCK_NAME),
                IS_TRADABLE = VALUES(IS_TRADABLE),
                LLM_SCORE = VALUES(LLM_SCORE),
                LLM_REASON = VALUES(LLM_REASON)
        """, [code, name, is_tradable, llm_score, llm_reason])
    
    connection.commit()
    cursor.close()


# ============================================================================
# Portfolio
# ============================================================================
def get_active_portfolio(connection) -> List[Dict]:
    cursor = connection.cursor()
    table_name = _get_table_name("Portfolio")
    cursor.execute(f"""
        SELECT ID, STOCK_CODE, STOCK_NAME, QUANTITY, BUY_PRICE, AVG_PRICE, CURRENT_PRICE,
               BUY_DATE, STOP_LOSS_PRICE, HIGH_PRICE
        FROM {table_name}
        WHERE QUANTITY > 0
    """)
    rows = cursor.fetchall()
    cursor.close()
    
    portfolio = []
    for row in rows:
        if isinstance(row, dict):
            portfolio.append({
                "id": row.get('ID'),
                "code": row.get('STOCK_CODE'),
                "stock_code": row.get('STOCK_CODE'),
                "name": row.get('STOCK_NAME'),
                "stock_name": row.get('STOCK_NAME'),
                "quantity": row.get('QUANTITY', 0),
                "buy_price": row.get('BUY_PRICE', 0),
                "avg_price": row.get('AVG_PRICE', 0),
                "current_price": row.get('CURRENT_PRICE', 0),
                "buy_date": row.get('BUY_DATE'),
                "stop_loss_price": row.get('STOP_LOSS_PRICE'),
                "high_price": row.get('HIGH_PRICE'),
            })
        else:
            portfolio.append({
                "id": row[0],
                "code": row[1],
                "stock_code": row[1],
                "name": row[2],
                "stock_name": row[2],
                "quantity": row[3],
                "buy_price": row[4],
                "avg_price": row[5],
                "current_price": row[6],
                "buy_date": row[7],
                "stop_loss_price": row[8],
                "high_price": row[9],
            })
    return portfolio


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


# ============================================================================
# 가격/일봉 조회
# ============================================================================
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
    df = df.iloc[::-1]  # 날짜 오름차순으로 뒤집기
    return df
