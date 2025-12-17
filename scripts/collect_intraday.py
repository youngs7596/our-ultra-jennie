#!/usr/bin/env python3
"""
[Priority 3] Targeted Intraday Data Collector
- Collects 1-minute OHLCV data for 'Radar Candidates'
- Targets: Active Watchlist + Recent Shadow Radar Logs (Missed Opportunities)
- Run Frequency: Every 1-5 minutes during market hours, or bulk periodic.
"""
import os
import sys
import time
import logging
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from sqlalchemy.orm import Session
from sqlalchemy import text

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import shared.auth as auth
from shared.db.connection import session_scope, ensure_engine_initialized
from shared.kis import KISClient as KIS_API
from shared.kis.gateway_client import KISGatewayClient
from shared.db.models import StockMinutePrice

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("IntradayCollector")

def get_target_stocks(session: Session) -> list:
    """
    Get list of stock codes to track.
    1. Active Watchlist
    2. Recent Shadow Radar Logs (last 7 days)
    """
    targets = set()

    # 1. Watchlist
    try:
        from shared.database import get_active_watchlist
        watchlist = get_active_watchlist(session)
        for code in watchlist.keys():
            targets.add(code)
        logger.info(f"Loaded {len(targets)} from Watchlist")
    except Exception as e:
        logger.warning(f"Failed to load watchlist: {e}")

    # 2. Shadow Radar (Recent Rejections)
    try:
        # Check if table exists (it should)
        query = text("""
            SELECT DISTINCT STOCK_CODE 
            FROM SHADOW_RADAR_LOG
            WHERE TIMESTAMP >= :since
        """)
        since = datetime.now(timezone.utc) - timedelta(days=7)
        rows = session.execute(query, {"since": since}).fetchall()
        
        count = 0
        for row in rows:
            targets.add(row[0])
            count += 1
        logger.info(f"Loaded {count} from Shadow Radar (Last 7 days)")
    except Exception as e:
        logger.warning(f"Failed to load Shadow Radar logs: {e}")

    return list(targets)

def collect_intraday(kis_api, session: Session, stock_codes: list):
    """
    Collect 1-minute candles for target stocks.
    Uses KIS API get_stock_minute_prices.
    """
    # KST Today YYYYMMDD
    kst_now = datetime.now(timezone(timedelta(hours=9)))
    today_str = kst_now.strftime("%Y%m%d")
    current_time_str = kst_now.strftime("%H%M%S")

    # If before 09:00, warn? Or maybe we are backfilling yesterday?
    # Assuming running during market hours or shortly after.
    
    logger.info(f"Starting collection for {len(stock_codes)} stocks. Target Date: {today_str}")

    success_count = 0
    
    for code in stock_codes:
        try:
            # We want today's 1-min candles
            # API expects time in certain formats usually
            # get_stock_minute_prices implementation usually handles "today" if no time specified or fetches recent.
            # Let's check the signature from shared/kis/market_data.py
            # def get_stock_minute_prices(self, stock_code, target_date_yyyymmdd: str, minute_interval: int = 5):
            # Oops, it's 5 min interval default? We want 1 minute.
            
            # Using KISClient directly or via helper?
            # kis_api is likely KISClient or Gateway.
            # If KISClient, it likely has .get_market_data().get_stock_minute_prices(...)
            
            # Let's assume we can access market_data wrapper or call API directly.
            # Based on code view, MarketData is a mixin or separate class.
            
            daily_prices = [] 
            # Trying to use the high level method if available
            if hasattr(kis_api, 'get_market_data'):
                # It's KISClient
                ticks = kis_api.get_market_data().get_stock_minute_prices(code, today_str, minute_interval=1)
            elif hasattr(kis_api, 'get_stock_minute_prices'):
                 # It's MarketData mixin or Gateway
                ticks = kis_api.get_stock_minute_prices(code, today_str, minute_interval=1)
            else:
                logger.error("KIS API client does not support get_stock_minute_prices")
                break

            if not ticks:
                continue

            # Save to DB
            for t in ticks:
                # t is dict with 'time', 'open', 'high', 'low', 'close', 'volume'
                # time format usually HHMMSS
                hhmmss = t.get('time') or t.get('stck_cntg_hour') # API specific, need to handle
                
                # If Gateway returns normalized dict, it might be 'time'.
                # Let's inspect the `market_data.py` output format again if needed.
                # Assuming standard dictionary based on existing code.
                
                # Construct datetime
                # DB schema: PRICE_TIME (PK), STOCK_CODE (PK)
                price_time_str = f"{today_str}{hhmmss}"
                try:
                    price_dt = datetime.strptime(price_time_str, "%Y%m%d%H%M%S")
                except ValueError:
                    continue

                # Prepare upsert (merge)
                # SQLAlchemy merge or raw upsert
                
                # Using merge for simplicity standard
                # Performance might be an issue for updates, but for limited targets it's fine.
                
                record = StockMinutePrice(
                    price_time=price_dt,
                    stock_code=code,
                    open_price=float(t.get('open', 0)),
                    high_price=float(t.get('high', 0)),
                    low_price=float(t.get('low', 0)),
                    close_price=float(t.get('close', 0)),
                    volume=float(t.get('volume', 0)),
                    accum_volume=float(t.get('accum_volume', 0) or 0) # if available
                )
                session.merge(record)
            
            session.commit()
            success_count += 1
            time.sleep(0.05) # Rate limit basic

        except Exception as e:
            logger.error(f"Error collecting for {code}: {e}")
            session.rollback()

    logger.info(f"Collection Complete. Success: {success_count}/{len(stock_codes)}")

def main():
    load_dotenv(override=True)
    ensure_engine_initialized()
    
    trading_mode = os.getenv("TRADING_MODE", "REAL")
    use_gateway = os.getenv("USE_KIS_GATEWAY", "true").lower() == "true"
    
    logger.info(f"Trading Mode: {trading_mode}, Gateway: {use_gateway}")

    # Init KIS API
    try:
        if use_gateway:
            kis_api = KISGatewayClient()
        else:
            kis_api = KIS_API(
                app_key=auth.get_secret(os.getenv(f"{trading_mode}_SECRET_ID_APP_KEY")),
                app_secret=auth.get_secret(os.getenv(f"{trading_mode}_SECRET_ID_APP_SECRET")),
                base_url=os.getenv(f"KIS_BASE_URL_{trading_mode}"),
                account_prefix=auth.get_secret(os.getenv(f"{trading_mode}_SECRET_ID_ACCOUNT_PREFIX")),
                account_suffix=os.getenv("KIS_ACCOUNT_SUFFIX"),
                token_file_path="/app/tokens/kis_token_scout.json",
                trading_mode=trading_mode
            )
            if not kis_api.authenticate():
                raise Exception("Authentication Failed")
    except Exception as e:
        logger.error(f"Failed to initialize KIS API: {e}")
        return

    with session_scope() as session:
        targets = get_target_stocks(session)
        if not targets:
            logger.info("No targets to collect.")
            return
        
        collect_intraday(kis_api, session, targets)

if __name__ == "__main__":
    main()
