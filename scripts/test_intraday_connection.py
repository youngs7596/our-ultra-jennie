
import os
import sys
import logging
from datetime import datetime
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(PROJECT_ROOT)

import shared.auth as auth
from shared.kis.client import KISClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("TestIntraday")

def main():
    load_dotenv()
    trading_mode = "REAL"
    
    # Fallback to standard secret IDs
    prefix = 'r'
    app_key_id = f"kis-{prefix}-app-key"
    app_secret_id = f"kis-{prefix}-app-secret"
    account_prefix_id = f"kis-{prefix}-account-no"
    
    default_base_url = "https://openapi.koreainvestment.com:9443"
    
    kis = KISClient(
        app_key=auth.get_secret(app_key_id),
        app_secret=auth.get_secret(app_secret_id),
        base_url=default_base_url,
        account_prefix=auth.get_secret(account_prefix_id),
        account_suffix="01",
        trading_mode=trading_mode,
        token_file_path=os.path.join(PROJECT_ROOT, "tokens", "test_token.json")
    )
    
    if not kis.authenticate():
        logger.error("Auth Failed")
        return

    code = "005930" # Samsung Elec
    date = datetime.now().strftime("%Y%m%d")
    
    # Direct MarketData usage
    logger.info(f"Fetching DAILY prices for {code}")
    rows = kis.market_data.get_stock_daily_prices(code, 5)
    
    logger.info(f"Daily Rows: {len(rows) if rows else 0}")
    if rows:
        print(rows[0])
        
    # Retry Minute Check
    logger.info(f"Fetching minute prices for {code} on {date}")
    rows_min = kis.market_data.get_stock_minute_prices(code, date, 1)
    logger.info(f"Minute Rows: {len(rows_min) if rows_min else 0}")

if __name__ == "__main__":
    main()
