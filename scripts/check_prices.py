#!/usr/bin/env python3
import os
import sys
import logging
from datetime import datetime

from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(PROJECT_ROOT)

import shared.database as database

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def main():
    load_dotenv()
    conn = database.get_db_connection()
    cursor = conn.cursor()
    
    stock_code = '005930'
    logger.info(f"Checking STOCK_DAILY_PRICES_3Y for {stock_code}")
    
    cursor.execute("""
        SELECT PRICE_DATE, CLOSE_PRICE, HIGH_PRICE, LOW_PRICE, VOLUME
        FROM STOCK_DAILY_PRICES_3Y
        WHERE STOCK_CODE = %s AND PRICE_DATE >= '2025-11-20'
        ORDER BY PRICE_DATE ASC
    """, (stock_code,))
    
    for row in cursor.fetchall():
        logger.info(f"Date: {row[0]}, Close: {row[1]}, High: {row[2]}, Low: {row[3]}, Vol: {row[4]}")

    conn.close()

if __name__ == "__main__":
    main()
