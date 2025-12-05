# youngs75_jennie/kis/gateway_client.py
# Version: v3.5
# KIS Gateway Client - KIS API 호출을 Gateway를 통해 수행

import os
import logging
import requests
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

class KISGatewayClient:
    """
    KIS Gateway를 통해 KIS API를 호출하는 클라이언트
    
    Gateway를 사용하면:
    - Rate Limiting 자동 관리
    - Circuit Breaker 보호
    - 중앙화된 모니터링
    - 토큰 관리 불필요
    """
    
    def __init__(self, gateway_url: Optional[str] = None, timeout: int = 30):
        """
        Args:
            gateway_url: Gateway URL (기본: 환경 변수에서 읽기)
            timeout: 요청 타임아웃 (초)
        """
        self.gateway_url = gateway_url or os.getenv(
            'KIS_GATEWAY_URL',
            'https://kis-gateway-641885523217.asia-northeast3.run.app'
        )
        self.timeout = timeout
        self.session = requests.Session()
        
        # Cloud Run 인증 토큰 (로컬에서는 gcloud 인증 사용)
        self.use_auth = os.getenv('USE_GATEWAY_AUTH', 'true').lower() == 'true'
        
        # Rate Limit 방지를 위한 클라이언트 측 딜레이 (Gateway 제한: 20req/sec -> 0.05s)
        self.API_CALL_DELAY = 0.05
        
        logger.info(f"✅ KIS Gateway Client 초기화: {self.gateway_url}")
    
    def _get_auth_token(self) -> Optional[str]:
        """Cloud Run 인증 토큰 획득 (서비스 간 통신)"""
        if not self.use_auth:
            return None
        
        try:
            # Cloud Run 내부에서는 Metadata Server에서 토큰 획득
            metadata_url = "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity"
            params = {'audience': self.gateway_url}
            headers = {'Metadata-Flavor': 'Google'}
            
            response = requests.get(
                metadata_url,
                params=params,
                headers=headers,
                timeout=5
            )
            
            if response.status_code == 200:
                return response.text
            else:
                logger.warning(f"Metadata Server 토큰 획득 실패: {response.status_code}")
                return None
        except Exception as e:
            logger.warning(f"인증 토큰 획득 실패 (로컬 환경일 수 있음): {e}")
            return None
    
    def _request(self, method: str, endpoint: str, data: Optional[Dict] = None) -> Optional[Dict[str, Any]]:
        """
        Gateway에 HTTP 요청 전송
        
        Args:
            method: HTTP 메서드 (GET, POST)
            endpoint: API 엔드포인트
            data: 요청 데이터
            
        Returns:
            응답 데이터 또는 None
        """
        url = f"{self.gateway_url}{endpoint}"
        headers = {'Content-Type': 'application/json'}
        
        # 인증 토큰 추가
        if self.use_auth:
            token = self._get_auth_token()
            if token:
                headers['Authorization'] = f'Bearer {token}'
        
        try:
            if method == 'GET':
                response = self.session.get(url, headers=headers, timeout=self.timeout)
            elif method == 'POST':
                response = self.session.post(url, headers=headers, json=data, timeout=self.timeout)
            else:
                raise ValueError(f"Unsupported method: {method}")
            
            response.raise_for_status()
            return response.json()
            
        except requests.exceptions.Timeout:
            logger.error(f"❌ Gateway 타임아웃: {url}")
            return None
        except requests.exceptions.HTTPError as e:
            logger.error(f"❌ Gateway HTTP 오류: {e.response.status_code} - {e.response.text}")
            return None
        except Exception as e:
            logger.error(f"❌ Gateway 요청 실패: {e}")
            return None
        finally:
            # Rate Limiting: 다음 요청까지 대기
            if hasattr(self, 'API_CALL_DELAY'):
                import time
                time.sleep(self.API_CALL_DELAY)
    
    def get_stock_snapshot(self, stock_code: str, is_index: bool = False) -> Optional[Dict[str, Any]]:
        """
        주식 현재가 조회 (Gateway를 통해)
        
        Args:
            stock_code: 종목 코드
            is_index: 지수 여부
            
        Returns:
            현재가 정보 또는 None
        """
        logger.debug(f"📊 [Gateway] Snapshot 요청: {stock_code}")
        
        response = self._request('POST', '/api/market-data/snapshot', {
            'stock_code': stock_code,
            'is_index': is_index
        })
        
        if response and response.get('success'):
            return response.get('data')
        else:
            logger.error(f"❌ Snapshot 조회 실패: {stock_code}")
            return None
    
    def place_buy_order(self, stock_code: str, quantity: int, price: int = 0) -> Optional[str]:
        """
        매수 주문 (Gateway를 통해)
        
        Args:
            stock_code: 종목 코드
            quantity: 수량
            price: 가격 (0이면 시장가)
            
        Returns:
            주문번호 또는 None
        """
        logger.info(f"💰 [Gateway] 매수 주문: {stock_code} x {quantity}주")
        
        response = self._request('POST', '/api/trading/buy', {
            'stock_code': stock_code,
            'quantity': quantity,
            'price': price
        })
        
        if response and response.get('success'):
            order_no = response.get('order_no')
            logger.info(f"✅ 매수 주문 성공: {order_no}")
            return order_no
        else:
            logger.error(f"❌ 매수 주문 실패: {stock_code}")
            return None
    
    def place_sell_order(self, stock_code: str, quantity: int, price: int = 0) -> Optional[str]:
        """
        매도 주문 (Gateway를 통해)
        
        Args:
            stock_code: 종목 코드
            quantity: 수량
            price: 가격 (0이면 시장가)
            
        Returns:
            주문번호 또는 None
        """
        logger.info(f"💸 [Gateway] 매도 주문: {stock_code} x {quantity}주")
        
        response = self._request('POST', '/api/trading/sell', {
            'stock_code': stock_code,
            'quantity': quantity,
            'price': price
        })
        
        if response and response.get('success'):
            order_no = response.get('order_no')
            logger.info(f"✅ 매도 주문 성공: {order_no}")
            return order_no
        else:
            logger.error(f"❌ 매도 주문 실패: {stock_code}")
            return None
    
    def get_stock_daily_prices(self, stock_code: str, num_days_to_fetch: int = 30) -> Optional[Any]:
        """
        일봉 데이터 조회 (Gateway를 통해)
        
        Args:
            stock_code: 종목 코드
            num_days_to_fetch: 조회할 일수
            
        Returns:
            일봉 데이터 (DataFrame 형태의 dict) 또는 None
        """
        logger.debug(f"📈 [Gateway] Daily Prices 요청: {stock_code} ({num_days_to_fetch}일)")
        
        response = self._request('POST', '/api/market-data/daily-prices', {
            'stock_code': stock_code,
            'num_days_to_fetch': num_days_to_fetch
        })
        
        if response and response.get('success'):
            data = response.get('data')
            # DataFrame으로 변환 (원본 KISClient와 호환성 유지)
            try:
                import pandas as pd
                return pd.DataFrame(data) if isinstance(data, list) else data
            except ImportError:
                return data
        else:
            logger.error(f"❌ Daily Prices 조회 실패: {stock_code}")
            return None
    
    def get_account_balance(self) -> Optional[Dict[str, Any]]:
        """
        계좌 잔고 조회 (Gateway를 통해)
        
        Returns:
            계좌 잔고 정보 또는 None
        """
        logger.debug(f"💰 [Gateway] Account Balance 요청")
        
        response = self._request('POST', '/api/account/balance', {})
        
        if response and response.get('success'):
            return response.get('data')
        else:
            logger.error(f"❌ Account Balance 조회 실패")
            return None
    
    def get_cash_balance(self) -> float:
        """
        현금 잔고 조회 (Gateway를 통해)
        
        Returns:
            현금 잔고 (원)
        """
        logger.debug(f"💰 [Gateway] Cash Balance 요청")
        
        response = self._request('POST', '/api/account/cash-balance', {})
        
        if response and response.get('success'):
            return float(response.get('data', 0.0))
        else:
            logger.error(f"❌ Cash Balance 조회 실패")
            return 0.0
    
    def get_health(self) -> Optional[Dict[str, Any]]:
        """Gateway Health Check"""
        return self._request('GET', '/health')
    
    def get_stats(self) -> Optional[Dict[str, Any]]:
        """Gateway 통계 조회"""
        return self._request('GET', '/stats')

