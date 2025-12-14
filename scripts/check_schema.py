
import os
import sys
import logging

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(PROJECT_ROOT)

import shared.database as database
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def main():
    load_dotenv()
    conn = database.get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DESCRIBE STOCK_NEWS_SENTIMENT")
        rows = cursor.fetchall()
        for row in rows:
            logger.info(row)
    except Exception as e:
        logger.error(f"Error: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    main()
