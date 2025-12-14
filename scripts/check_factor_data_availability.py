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
    
    tables = [
        "STOCK_INVESTOR_TRADING", 
        "FINANCIAL_METRICS_QUARTERLY"
    ]
    
    logger.info("Checking Factor Data Availability...")
    
    for table in tables:
        date_col = "TRADE_DATE" if table == "STOCK_INVESTOR_TRADING" else "QUARTER_DATE"
        
        cursor.execute(f"SELECT MIN({date_col}), MAX({date_col}), COUNT(*) FROM {table}")
        stats = cursor.fetchone()
        
        if stats:
            logger.info(f"[{table}]")
            logger.info(f"   - Range: {stats[0]} ~ {stats[1]}")
            logger.info(f"   - Count: {stats[2]}")
        else:
            logger.info(f"[{table}] No data found.")
            
    conn.close()

if __name__ == "__main__":
    main()
