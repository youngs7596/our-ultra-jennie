import os
import sys
import logging
from datetime import datetime
# from dotenv import load_dotenv

# Add project root to path
sys.path.append(os.getcwd())

import unittest
from unittest.mock import MagicMock
import logging
from datetime import datetime, timedelta

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Standalone Class with the exact logic we implemented ---
class StandaloneMarketData:
    def __init__(self, client):
        self.client = client

    # COPIED FROM shared/kis/market_data.py
    def get_investor_trend(self, stock_code: str, start_date: str = None, end_date: str = None):
        if not end_date:
            end_date = datetime.now().strftime("%Y%m%d")
        if not start_date:
            start_date = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
            
        URL = f"{self.client.BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-investor"
        
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": stock_code,
            "FID_INPUT_DATE_1": start_date,
            "FID_INPUT_DATE_2": end_date
        }
        tr_id = "FHKST01010900" 
        
        logger.info(f"   (Data) [{stock_code}] 투자자 동향 조회 ({start_date}~{end_date})")
        res_data = self.client.request('GET', URL, params=params, tr_id=tr_id)
        
        rows = []
        if res_data and res_data.get('output'):
            for item in res_data['output']:
                try:
                    rows.append({
                        'date': item['stck_bsop_date'],
                        'price': float(item.get('stck_clpr', 0)),
                        'individual_net_buy': int(item.get('prsn_ntby_qty', 0)),
                        'foreigner_net_buy': int(item.get('frgn_ntby_qty', 0)),
                        'institution_net_buy': int(item.get('orgn_ntby_qty', 0)),
                        'pension_net_buy': int(item.get('ntik_ntby_qty', 0) or 0),
                        'program_net_buy': 0
                    })
                except Exception as e:
                    continue
        
        return rows

class TestMarketFlow(unittest.TestCase):
    def test_get_investor_trend_logic(self):
        logger.info("--- Testing Logic Isolation ---")
        
        mock_client = MagicMock()
        mock_client.BASE_URL = "https://mock-api.com"
        
        mock_response = {
            "output": [
                {
                    "stck_bsop_date": "20231215",
                    "stck_clpr": "73000",
                    "prsn_ntby_qty": "-1000",
                    "frgn_ntby_qty": "5000",
                    "orgn_ntby_qty": "-4000",
                    "ntik_ntby_qty": "100"
                }
            ]
        }
        mock_client.request.return_value = mock_response
        
        market_data = StandaloneMarketData(mock_client)
        trends = market_data.get_investor_trend("005930", start_date="20231201", end_date="20231215")
        
        # Verify Logic
        self.assertEqual(len(trends), 1)
        self.assertEqual(trends[0]['foreigner_net_buy'], 5000)
        
        # Verify URL construction logic
        args, kwargs = mock_client.request.call_args
        self.assertEqual(args[1], "https://mock-api.com/uapi/domestic-stock/v1/quotations/inquire-investor")
        self.assertEqual(kwargs['params']['FID_INPUT_ISCD'], "005930")
        
        logger.info("✅ Logic Verification Successful")

if __name__ == "__main__":
    suite = unittest.TestLoader().loadTestsFromTestCase(TestMarketFlow)
    unittest.TextTestRunner(verbosity=2).run(suite)
