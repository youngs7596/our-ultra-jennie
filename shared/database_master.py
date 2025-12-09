"""
shared/database_master.py

종목 마스터/메타/섹터 관련 조회 로직을 분리한 모듈입니다.
"""

import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


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
