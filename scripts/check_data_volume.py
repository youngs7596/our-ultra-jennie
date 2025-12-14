#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/check_data_volume.py

DB에 저장된 데이터의 양(행 수)과 기간(Min/Max Date)을 확인하는 스크립트.
"""

import os
import sys
import logging
from datetime import datetime

# 프로젝트 루트 경로 설정
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(PROJECT_ROOT)

import shared.database as database
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

def get_db_config():
    # 로컬 개발 환경(MariaDB) 가정
    return {
        "db_user": "dummy",
        "db_password": "dummy",
        "db_service_name": "dummy",
        "wallet_path": "dummy",
    }

def main():
    load_dotenv()
    
    # DB 연결 (Shared 모듈 활용)
    # DB_TYPE=MARIADB 환경변수가 설정되어 있어야 로컬 로직을 탐
    os.environ["DB_TYPE"] = "MARIADB"
    
    logger.info("=" * 60)
    logger.info("📊 데이터 볼륨 점검")
    logger.info("=" * 60)
    
    conn = database.get_db_connection()
    if not conn:
        logger.error("❌ DB 연결 실패")
        return

    try:
        cursor = conn.cursor()
        
        queries = [
            ("STOCK_DAILY_PRICES_3Y", "PRICE_DATE"),
            ("STOCK_NEWS_SENTIMENT", "NEWS_DATE"),
            ("STOCK_INVESTOR_TRADING", "TRADE_DATE"),
            ("STOCK_DISCLOSURES", "DISCLOSURE_DATE"),
        ]
        
        for table, date_col in queries:
            try:
                cursor.execute(f"""
                    SELECT COUNT(*), MIN({date_col}), MAX({date_col})
                    FROM {table}
                """)
                row = cursor.fetchone()
                
                count = row[0]
                min_date = row[1]
                max_date = row[2]
                
                logger.info(f"[{table}]")
                logger.info(f"   - 총 행 수: {count:,} 건")
                logger.info(f"   - 기간: {min_date} ~ {max_date}")
                
                if count == 0:
                     logger.warning("   ⚠️  데이터가 없습니다.")
                
                logger.info("-" * 40)
                
            except Exception as e:
                logger.error(f"   ❌ {table} 조회 실패: {e}")

        cursor.close()
        
    except Exception as e:
        logger.error(f"❌ 오류 발생: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    main()
