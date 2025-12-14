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
    logger.info(f"Checking STOCK_NEWS_SENTIMENT for {stock_code}")
    
    cursor.execute("""
        SELECT * FROM STOCK_NEWS_SENTIMENT 
        WHERE STOCK_CODE = %s 
        ORDER BY NEWS_DATE DESC 
        LIMIT 5
    """, (stock_code,))
    
    rows = cursor.fetchall()
    columns = [desc[0] for desc in cursor.description]
    
    for row in rows:
        row_dict = dict(zip(columns, row))
        logger.info(row_dict)
        
    cursor.execute("""
        SELECT NEWS_DATE, HEADLINE, SENTIMENT_SCORE 
        FROM STOCK_NEWS_SENTIMENT 
        WHERE STOCK_CODE = %s AND CATEGORY = '수주'
        ORDER BY NEWS_DATE DESC
    """, (stock_code,))
    
    logger.info("Category '수주' Items:")
    for row in cursor.fetchall():
        logger.info(f" - Date: {row[0]}, Score: {row[2]}, Title: {row[1]}")

    conn.close()

if __name__ == "__main__":
    main()
