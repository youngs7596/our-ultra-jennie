
import sys
import os
import time
import logging
import json
from datetime import datetime, timezone

# Add project root
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
import requests

from shared.db.connection import session_scope, ensure_engine_initialized
from shared.db.models import MarketFlowSnapshot, Watchlist
from shared.kis import KISClient
from shared.archivist import Archivist
import shared.auth as auth

# Setup Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("MarketFlowCollector")

def get_config():
    """Load configuration securely"""
    load_dotenv()
    trading_mode = os.getenv("TRADING_MODE", "REAL")
    
    # Load secrets
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
        'secrets': secrets
    }

def collect_daily_flow():
    ensure_engine_initialized()
    config = get_config()
    
    # Initialize KIS Client with direct secrets if env vars are IDs, but here we try to handle both/fallback
    # Since KISClient uses auth.get_secret(), and I can't easily inject context, 
    # I will assume KISClient can handle it via env vars or I pass raw keys if I modified KISClient. 
    # Current KISClient expects secret IDs if env vars are set.
    # To be safe, I'll instantiate KISClient using standard method which uses auth.get_secret
    
    # However, auth.get_secret relies on GCP Secret Manager OR local secrets.json if configured.
    # shared.auth implementation checks defined env vars.
    
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

    archivist = Archivist(session_scope)
    
    with session_scope() as session:
        # Fetch Target Stocks
        # 1. Active Watchlist
        watchlist = session.query(Watchlist).all()
        target_codes = {w.stock_code for w in watchlist}
        
        # 2. Recent Shadow Radar (Optional - for now just Watchlist to save quota)
        # In v1.1 expand to Shadow Radar
        
        logger.info(f"Collecting Market Flow for {len(target_codes)} stocks...")
        
        for code in target_codes:
            try:
                # 1. Investor Trend (Daily)
                # TR_ID: FHKST01010900
                res_investor = kis.request(
                    method='GET',
                    url='/uapi/domestic-stock/v1/quotations/inquire-investor',
                    headers={'tr_id': 'FHKST01010900'},
                    params={
                        'fid_cond_mrkt_div_code': 'J',
                        'fid_input_iscd': code
                    }
                )
                
                foreign_net = 0
                institution_net = 0
                price = 0
                
                if res_investor and res_investor.json().get('rt_cd') == '0':
                    output = res_investor.json().get('output', [])
                    if output:
                        today_data = output[0] # Most recent
                        foreign_net = int(today_data.get('frgn_ntby_qty', 0))
                        institution_net = int(today_data.get('orgn_ntby_qty', 0))
                        # Some APIs return price in this response too? 'stck_clpr' usually
                        # Not guaranteed in this TR output list.
                
                # 2. Program Trade (Daily)
                # TR_ID: FHKST01011600 (Program Trade Daily)
                res_program = kis.request(
                    method='GET',
                    url='/uapi/domestic-stock/v1/quotations/inquire-program-trade',
                    headers={'tr_id': 'FHKST01011600'},
                    params={
                        'fid_cond_mrkt_div_code': 'J',
                        'fid_input_iscd': code
                    }
                )
                
                program_net = 0
                if res_program and res_program.json().get('rt_cd') == '0':
                    output = res_program.json().get('output', [])
                    if output:
                        today_data = output[0]
                        program_net = int(today_data.get('whol_ntby_qty', 0))
                        price = float(today_data.get('stck_clpr', 0)) # Program trade output usually has price

                # Log to Archivist
                snapshot_data = {
                    'stock_code': code,
                    'price': price,
                    'volume': 0, # Volume not strictly required for flow snapshot if separate price log exists, but nice to have.
                    'foreign_net_buy': foreign_net,
                    'institution_net_buy': institution_net,
                    'program_net_buy': program_net,
                    'data_type': 'DAILY'
                }
                
                archivist.log_market_flow_snapshot(snapshot_data)
                
                time.sleep(0.1) # Rate limit respect
                
            except Exception as e:
                logger.error(f"Failed to collect flow for {code}: {e}")

    logger.info("Market Flow Collection Complete.")

if __name__ == "__main__":
    collect_daily_flow()
