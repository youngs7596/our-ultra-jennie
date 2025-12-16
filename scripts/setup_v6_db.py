
import sys
import os
from sqlalchemy import create_engine
from dotenv import load_dotenv

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.db.models import Base, LLMDecisionLedger, ShadowRadarLog, MarketFlowSnapshot, StockMinutePrice

def init_db():
    load_dotenv()
    
    # Load secrets
    import json
    secrets_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'secrets.json')
    try:
        with open(secrets_path, 'r') as f:
            secrets = json.load(f)
            db_user = secrets.get('mariadb-user', os.getenv("MARIADB_USER", "antigravity"))
            db_password = secrets.get('mariadb-password', os.getenv("MARIADB_PASSWORD", "antigravity"))
    except Exception as e:
        print(f"⚠️ Failed to load secrets.json: {e}")
        db_user = os.getenv("MARIADB_USER", "antigravity")
        db_password = os.getenv("MARIADB_PASSWORD", "antigravity")

    db_host = os.getenv("MARIADB_HOST", "127.0.0.1")
    db_port = os.getenv("MARIADB_PORT", "3306")
    db_name = os.getenv("MARIADB_DBNAME", "jennie_db") 
    
    db_url = f"mysql+pymysql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
    print(f"Target DB: {db_host}:{db_port}/{db_name}")
    
    print(f"Connecting to database...")
    engine = create_engine(db_url)
    
    print("Creating tables if not exist...")
    # Create specific tables
    try:
        LLMDecisionLedger.__table__.create(engine)
        print("✅ Created LLM_DECISION_LEDGER")
    except Exception as e:
        print(f"⚠️ LLM_DECISION_LEDGER might already exist: {e}")

    try:
        ShadowRadarLog.__table__.create(engine)
        print("✅ Created SHADOW_RADAR_LOG")
    except Exception as e:
        print(f"⚠️ SHADOW_RADAR_LOG might already exist: {e}")

    try:
        MarketFlowSnapshot.__table__.create(engine)
        print("✅ Created MARKET_FLOW_SNAPSHOT")
    except Exception as e:
        print(f"⚠️ MARKET_FLOW_SNAPSHOT might already exist: {e}")
        
    try:
        StockMinutePrice.__table__.create(engine)
        print("✅ Created STOCK_MINUTE_PRICE")
    except Exception as e:
        print(f"⚠️ STOCK_MINUTE_PRICE might already exist: {e}")
    
    print("Verification complete.")

if __name__ == "__main__":
    init_db()
