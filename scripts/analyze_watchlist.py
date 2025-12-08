
import os
import sys
import pandas as pd
from datetime import datetime, timedelta

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from shared.database import get_db_connection
from shared.db import connection as sa_connection

# Force MariaDB mode for this script
os.environ["MARIADB_HOST"] = "localhost" # Assuming tunneling or local instance available based on context, or relies on env vars if set. 
# Better to rely on env vars from run context, but script needs them.
# The user env is WSL, but DB is on Windows host "host.docker.internal" (from docker context) or "127.0.0.1" (from wsl context mapping).
# shared/database.py uses MARIADB_HOST env var.

def analyze_watchlist_diffs():
    conn = get_db_connection()
    if not conn:
        print("Failed to connect to DB")
        return

    try:
        cursor = conn.cursor()
        
        print("\n--- [1. Current WatchList Status] ---")
        cursor.execute("SELECT COUNT(*), MIN(LLM_UPDATED_AT), MAX(LLM_UPDATED_AT) FROM WatchList")
        row = cursor.fetchone()
        if row:
            print(f"Total Items: {row[0]}")
            print(f"Oldest Update: {row[1]}")
            print(f"Newest Update: {row[2]}")
        
        print("\n--- [2. WatchList History Analysis] ---")
        # Check available snapshots
        cursor.execute("SELECT DISTINCT SNAPSHOT_DATE FROM WATCHLIST_HISTORY ORDER BY SNAPSHOT_DATE DESC LIMIT 10")
        dates = [row[0] for row in cursor.fetchall()]
        
        if not dates:
            print("No history data found in WATCHLIST_HISTORY.")
            return

        print(f"Found snapshots: {[d.strftime('%Y-%m-%d') for d in dates]}")
        
        history_data = []
        for d in dates:
            cursor.execute("SELECT STOCK_CODE FROM WATCHLIST_HISTORY WHERE SNAPSHOT_DATE = %s", (d,))
            codes = set(row[0] for row in cursor.fetchall())
            history_data.append({'date': d, 'codes': codes})
        
        # Calculate Churn
        print("\n--- [3. Daily Churn Rate] ---")
        for i in range(len(history_data) - 1):
            curr = history_data[i]
            prev = history_data[i+1]
            
            added = curr['codes'] - prev['codes']
            removed = prev['codes'] - curr['codes']
            kept = curr['codes'] & prev['codes']
            
            print(f"Comparison: {prev['date']} -> {curr['date']}")
            print(f"  - Kept: {len(kept)}")
            print(f"  - Added: {len(added)} ({list(added)[:5]}...)")
            print(f"  - Removed: {len(removed)}")
            churn_rate = (len(added) + len(removed)) / len(prev['codes']) if prev['codes'] else 0
            print(f"  - Churn Rate: {churn_rate:.1%}")
            
    except Exception as e:
        print(f"Error: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    # Ensure env vars are loaded if possible, otherwise rely on system env
    analyze_watchlist_diffs()
