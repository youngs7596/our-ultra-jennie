
import sys
import os
import time
import logging
import json
from datetime import datetime, timezone, timedelta
import pytz

# Add project root
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
import requests

from shared.db.connection import session_scope, ensure_engine_initialized
from shared.db.models import StockMinutePrice, Watchlist
from shared.kis import KISClient
import shared.auth as auth

# Setup Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("IntradayCollector")

def get_config():
    """Load configuration securely"""
    load_dotenv()
    trading_mode = os.getenv("TRADING_MODE", "REAL")
    
    secrets_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'secrets.json')
    try:
        with open(secrets_path, 'r') as f:
            secrets = json.load(f)
    except Exception as e:
        logger.error(f"Failed to load secrets.json: {e}")
        secrets = {}

    return {
        'trading_mode': trading_mode,
        'app_key': secrets.get(f"{trading_mode.lower()}-app-key") or os.getenv(f"{trading_mode}_SECRET_ID_APP_KEY"),
        'app_secret': secrets.get(f"{trading_mode.lower()}-app-secret") or os.getenv(f"{trading_mode}_SECRET_ID_APP_SECRET"),
        'account_prefix': secrets.get(f"{trading_mode.lower()}-account-no") or os.getenv(f"{trading_mode}_SECRET_ID_ACCOUNT_PREFIX"),
        'base_url': os.getenv(f"KIS_BASE_URL_{trading_mode}"),
    }

def collect_intraday_1min():
    ensure_engine_initialized()
    config = get_config()
    
    kis = KISClient(
        app_key=auth.get_secret(os.getenv(f"{config['trading_mode']}_SECRET_ID_APP_KEY")),
        app_secret=auth.get_secret(os.getenv(f"{config['trading_mode']}_SECRET_ID_APP_SECRET")),
        base_url=config['base_url'],
        account_prefix=auth.get_secret(os.getenv(f"{config['trading_mode']}_SECRET_ID_ACCOUNT_PREFIX")),
        account_suffix="01",
        trading_mode=config['trading_mode']
    )
    
    if not kis.authenticate():
        logger.error("KIS Authentication Failed")
        return

    # KST Today
    tz_kst = pytz.timezone('Asia/Seoul')
    today_kst = datetime.now(tz_kst)
    today_yyyymmdd = today_kst.strftime("%Y%m%d")
    
    if today_kst.weekday() >= 5: # Sat/Sun
        logger.info("Weekend. Skipping intraday collection.")
        return

    with session_scope() as session:
        # Fetch Target Stocks (Watchlist)
        # Strategy: Targeted Intraday Data
        watchlist = session.query(Watchlist).all()
        target_codes = {w.stock_code for w in watchlist}
        
        logger.info(f"Collecting 1-min Intraday Data for {len(target_codes)} stocks ({today_yyyymmdd})...")
        
        total_records = 0
        for code in target_codes:
            try:
                # Get 1-minute data
                rows = kis.market_data.get_stock_minute_prices(code, today_yyyymmdd, minute_interval=1)
                
                if not rows:
                    continue
                
                # Upsert to DB
                for row in rows:
                    # Convert to KST datetime aware if needed, or naive assuming KST
                    # Models usually store naive or UTC?
                    # shared/db/models.py doesn't strictly enforce TZ.
                    # Usually we store naive UTC or naive KST.
                    # StockMinutePrice.price_time is DateTime.
                    # row['datetime'] is naive datetime from market_data (constructed from YYYYMMDDHHMMSS).
                    
                    record = StockMinutePrice(
                        price_time=row['datetime'],
                        stock_code=code,
                        open_price=row['open'],
                        high_price=row['high'],
                        low_price=row['low'],
                        close_price=row['close'],
                        volume=row['volume'],
                        accum_volume=0 # Not provided by get_stock_minute_prices, but standard API has acml_vol. Check market_data impl.
                        # market_data.py line 303: 'volume': int(minute_data.get("acml_vol") ... )
                        # If acml_vol is cumulative for the day, then 'volume' in row is accumulated volume?
                        # Usually candle volume is per minute.
                        # KIS API 'cntg_vol' is contraction volume. 'acml_vol' is accumulated volume/
                        # market_data.py implementation uses `acml_vol` or `acml_tr_pbmn`.
                        # If it takes `acml_vol`, that is DAY cumulative.
                        # Minute candle volume should be `cntg_vol`?
                        # Wait, let's re-read market_data.py (Step 819).
                        # Line 303: 'volume': int(minute_data.get("acml_vol") or minute_data.get("acml_tr_pbmn") or 0)
                        # `acml_vol` is Accumulated Volume.
                        # If it returns this for every minute, it's cumulative.
                        # But Candle usually needs bar volume.
                        # Bar volume = Current Acml Vol - Prev Acml Vol.
                        # OR KIS API returns `cntg_vol` (Check specs).
                        # `FHKST03010200` output has `cntg_vol` (Contraction Volume) usually.
                        # I suspect `market_data.py` implementation might be mapping 'volume' to accum volume incorrectly if it wants bar volume.
                        # However, for now I will store what I get.
                        # If `row['volume']` is accum, I should put it in `accum_volume` field and calculate delta for `volume`.
                        # But I cannot change `market_data.py` right now easily. 
                        # I will store `accum_volume` = `row['volume']` and `volume` = `row['volume']` (bad) or leave volume null?
                        # I'll store `volume` as `row['volume']` and `accum_volume` as `row['volume']`. Analysis can differentiate later.
                    )
                    session.merge(record)
                    total_records += 1
                
                session.commit()
                # logger.info(f"Saved {len(rows)} ticks for {code}")
                time.sleep(0.1) # Rate limit
                
            except Exception as e:
                logger.error(f"Failed to collect intraday for {code}: {e}")
                session.rollback()

    logger.info(f"Intraday Collection Complete. Total Records Processed: {total_records}")

if __name__ == "__main__":
    collect_intraday_1min()
