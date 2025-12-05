# youngs75_jennie/kis/client.py
# Version: v3.5
# KIS API 메인 클라이언트

import logging
import os
from .auth import KISAuth
from .request import safe_request
from .market_data import MarketData
from .trading import Trading
from .websocket import WebsocketManager

logger = logging.getLogger(__name__)

class KISClient:
    """
    한국투자증권(KIS) API와의 모든 상호작용을 캡슐화하는 메인 클라이언트입니다.
    """
    # Rate Limit 안전성을 위해 딜레이 증가 (0.3초)
    API_CALL_DELAY_REAL = 0.3
    API_CALL_DELAY_MOCK = 0.5
    TIMEOUT = 5

    def __init__(self, app_key, app_secret, base_url, account_prefix, account_suffix, trading_mode, token_file_path=None):
        """
        Args:
            ...
            trading_mode (str): 'REAL' 또는 'MOCK'
            token_file_path (str, optional): 토큰을 저장할 파일 경로. 
                                             None이면 환경 변수 또는 기본값(/tmp/kis_token.json)을 사용합니다.
        """
        self.APP_KEY = app_key
        self.APP_SECRET = app_secret
        self.BASE_URL = base_url
        self.ACCOUNT_PREFIX = account_prefix
        self.ACCOUNT_SUFFIX = account_suffix
        # v3.5: GCS 분산 인증 로직 제거 이후 로컬 파일 경로를 기본값으로 사용
        self.TOKEN_FILE_PATH = token_file_path or os.getenv("KIS_TOKEN_FILE_PATH", "/tmp/kis_token.json")
        self.TRADING_MODE = trading_mode
        
        # REAL 모드일 때 API_CALL_DELAY_REAL 사용
        self.API_CALL_DELAY = self.API_CALL_DELAY_MOCK if self.TRADING_MODE == "MOCK" else self.API_CALL_DELAY_REAL
        
        self.headers = None
        
        # 기능별 모듈 인스턴스화
        self.auth = KISAuth(self)
        self.market_data = MarketData(self)
        self.trading = Trading(self)
        self.websocket = WebsocketManager(self)
        
        logger.info(f"✅ KISClient 인스턴스 생성 완료 (모드: {self.TRADING_MODE})")

    def authenticate(self, force_new=False):
        """인증을 수행하고 API 요청에 필요한 헤더를 설정합니다."""
        access_token = self.auth.get_access_token(force_new)
        if access_token:
            self.headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {access_token}",
                "appkey": self.APP_KEY,
                "appsecret": self.APP_SECRET,
            }
            return True
        return False

    def request(self, method, url, headers=None, params=None, data=None, tr_id=""):
        """모든 API 요청을 위한 중앙 래퍼 메소드"""
        return safe_request(self, method, url, headers, params, data, tr_id)

    # --- 편의를 위한 바로가기(Shortcut) 메소드 ---
    # 기존 KIS_API 클래스와의 호환성을 위해 자주 사용하는 메소드들을 바로 호출할 수 있도록 연결합니다.
    def check_market_open(self): return self.market_data.check_market_open()
    def get_stock_daily_prices(self, *args, **kwargs): return self.market_data.get_stock_daily_prices(*args, **kwargs)
    def get_stock_snapshot(self, *args, **kwargs): return self.market_data.get_stock_snapshot(*args, **kwargs)
    def get_overseas_stock_price(self, *args, **kwargs): return self.market_data.get_overseas_stock_price(*args, **kwargs)
    def place_buy_order(self, *args, **kwargs): return self.trading.place_buy_order(*args, **kwargs)
    def place_sell_order(self, *args, **kwargs): return self.trading.place_sell_order(*args, **kwargs)
    def start_realtime_monitoring(self, *args, **kwargs): return self.websocket.start_realtime_monitoring(*args, **kwargs)