#!/usr/bin/env python3
import os
import sys
import logging
from datetime import datetime

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(PROJECT_ROOT)

from dotenv import load_dotenv
import shared.database as database

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

def main():
    load_dotenv()
    
    # Force DB_TYPE if not set (assuming MariaDB based on context)
    if not os.getenv("DB_TYPE"):
        os.environ["DB_TYPE"] = "MARIADB"

    conn = database.get_db_connection()
    if not conn:
        logger.error("Failed to connect to DB")
        return

    cursor = conn.cursor()
    
    logger.info("=== Data Collection Status ===")
    
    # 1. News Data
    try:
        cursor.execute("SELECT MIN(NEWS_DATE), MAX(NEWS_DATE), COUNT(*) FROM STOCK_NEWS_SENTIMENT")
        row = cursor.fetchone()
        if row:
            min_date, max_date, count = row
            logger.info(f"[News Data] Count: {count:,}, Range: {min_date} ~ {max_date}")
        else:
            logger.info("[News Data] No data found.")
    except Exception as e:
        logger.error(f"[News Data] Error: {e}")

    # 2. Investor Trading Data
    try:
        cursor.execute("SELECT MIN(TRADE_DATE), MAX(TRADE_DATE), COUNT(*) FROM STOCK_INVESTOR_TRADING")
        row = cursor.fetchone()
        if row:
            min_date, max_date, count = row
            logger.info(f"[Investor Trading] Count: {count:,}, Range: {min_date} ~ {max_date}")
        else:
            logger.info("[Investor Trading] No data found.")
    except Exception as e:
        logger.error(f"[Investor Trading] Error: {e}")
        
    conn.close()

if __name__ == "__main__":
    main()
