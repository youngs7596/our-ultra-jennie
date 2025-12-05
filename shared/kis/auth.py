# youngs75_jennie/kis/auth.py
# [모듈] KIS API 인증 (REST 토큰, WS 승인 키) 관리
# [v2.0] GCS 기반 분산 토큰 관리 적용

import os
import json
import logging
from datetime import datetime
import requests
from filelock import FileLock, Timeout

logger = logging.getLogger(__name__)

# GCS 기반 분산 토큰 관리 사용 여부
USE_DISTRIBUTED_AUTH = os.getenv('USE_DISTRIBUTED_AUTH', 'true').lower() == 'true'
TOKEN_PROVIDER_URL = os.getenv('KIS_TOKEN_PROVIDER_URL')
TOKEN_PROVIDER_TIMEOUT = float(os.getenv('KIS_TOKEN_PROVIDER_TIMEOUT', '5'))

if USE_DISTRIBUTED_AUTH:
    try:
        from .auth_distributed import DistributedKISAuth
        logger.info("✅ [Auth] GCS 기반 분산 토큰 관리 활성화")
    except ImportError as e:
        logger.warning(f"⚠️ [Auth] auth_distributed 모듈 로드 실패, 로컬 방식 사용: {e}")
        USE_DISTRIBUTED_AUTH = False

class KISAuth:
    """KIS API 인증(토큰, 승인키)을 담당하는 클래스"""

    def __init__(self, client):
        self.client = client # KISClient 인스턴스
        
        # GCS 기반 분산 토큰 관리 사용
        if USE_DISTRIBUTED_AUTH:
            self.distributed_auth = DistributedKISAuth(client)
            logger.info(f"   [Auth] DistributedKISAuth 초기화 완료 (모드: {client.TRADING_MODE})")
        else:
            self.distributed_auth = None
            logger.info("   [Auth] 로컬 파일 기반 토큰 관리 사용")

    def get_access_token(self, force_new=False):
        """
        KIS API 접근 토큰을 발급받거나, 유효한 경우 캐시된 토큰을 사용합니다.
        
        - USE_DISTRIBUTED_AUTH=true: GCS 기반 분산 토큰 관리 (Cloud Run 권장)
        - USE_DISTRIBUTED_AUTH=false: 로컬 파일 기반 토큰 관리 (로컬 테스트용)
        """
        # 우선순위 1: 외부 토큰 제공자 (예: KIS Gateway)
        token = self._try_token_provider(force_new=force_new)
        if token:
            return token
        
        # GCS 기반 분산 토큰 관리 사용
        if self.distributed_auth:
            return self.distributed_auth.get_access_token(force_new)
        
        # Fallback: 로컬 파일 기반 토큰 관리 (기존 방식)
        return self._get_local_token(force_new)

    def _try_token_provider(self, force_new=False):
        """Gateway 등 외부 토큰 제공자 API를 통해 토큰을 가져옵니다."""
        if not TOKEN_PROVIDER_URL:
            return None

        payload = {
            "force_new": force_new,
            "mode": self.client.TRADING_MODE,
        }

        try:
            res = requests.post(
                TOKEN_PROVIDER_URL,
                json=payload,
                timeout=TOKEN_PROVIDER_TIMEOUT,
            )
            res.raise_for_status()
            data = res.json()
            access_token = data.get("access_token")
            if not access_token:
                logger.error(f"❌ (Auth) 토큰 제공자 응답에 access_token이 없습니다. data={data}")
                return None
            return access_token
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ (Auth) 토큰 제공자 호출 실패: {e}")
            return None
    
    def _get_local_token(self, force_new=False):
        """
        로컬 파일 기반 토큰 관리 (기존 방식, Fallback용)
        """
        lock_path = self.client.TOKEN_FILE_PATH + ".lock"
        lock = FileLock(lock_path, timeout=10) # 10초 타임아웃

        try:
            with lock:
                # --- 임계 영역 시작 ---
                # 1. 파일에서 토큰 로드 시도
                if not force_new and os.path.exists(self.client.TOKEN_FILE_PATH):
                    with open(self.client.TOKEN_FILE_PATH, 'r') as f:
                        token_data = json.load(f)
                    
                    expires_at_val = token_data.get('expires_at', 0)
                    expires_at_ts = 0.0
                    if isinstance(expires_at_val, (int, float)):
                        expires_at_ts = float(expires_at_val)
                    elif isinstance(expires_at_val, str):
                        try:
                            expires_at_ts = datetime.fromisoformat(expires_at_val).timestamp()
                        except ValueError:
                            try:
                                expires_at_ts = float(expires_at_val)
                            except ValueError:
                                expires_at_ts = 0.0

                    if datetime.now().timestamp() < expires_at_ts - 600:
                        logger.info("   (Auth) ✅ 유효한 캐시 토큰 사용. (잠금 내)")
                        return token_data['access_token']

                # 2. 토큰이 없거나 만료된 경우, 새로 발급
                logger.info("   (Auth) ... 새 접근 토큰 발급 시도 (잠금 획득) ...")
                
                # APP_KEY와 APP_SECRET 검증
                if not self.client.APP_KEY or not self.client.APP_SECRET:
                    logger.error(f"❌ (Auth) APP_KEY 또는 APP_SECRET이 없습니다. APP_KEY={'있음' if self.client.APP_KEY else '없음'}, APP_SECRET={'있음' if self.client.APP_SECRET else '없음'}")
                    return None
                
                TOKEN_URL = f"{self.client.BASE_URL}/oauth2/tokenP"
                headers = {"Content-Type": "application/json"}
                data = {"grant_type": "client_credentials", "appkey": self.client.APP_KEY, "appsecret": self.client.APP_SECRET}
                
                try:
                    logger.debug(f"   (Auth) 토큰 발급 요청: URL={TOKEN_URL}, APP_KEY={self.client.APP_KEY[:10]}...")
                    res = requests.post(TOKEN_URL, headers=headers, data=json.dumps(data), timeout=self.client.TIMEOUT)
                    
                    if res.status_code == 403:
                        logger.error(f"❌ (Auth) 토큰 발급 실패 (403 Forbidden): 응답={res.text[:200]}")
                        logger.error(f"❌ (Auth) 요청 URL: {TOKEN_URL}")
                        logger.error(f"❌ (Auth) APP_KEY 길이: {len(self.client.APP_KEY) if self.client.APP_KEY else 0}, APP_SECRET 길이: {len(self.client.APP_SECRET) if self.client.APP_SECRET else 0}")
                    
                    res.raise_for_status()
                    res_data = res.json()
                    
                    access_token = res_data.get('access_token')
                    if not access_token:
                        logger.error(f"❌ (Auth) 토큰 발급 실패: 응답에 'access_token'이 없습니다. (응답: {res_data})")
                        return None

                    expires_in = int(res_data.get('expires_in', 86400))
                    token_data = {
                        'access_token': access_token,
                        'expires_at': datetime.now().timestamp() + expires_in
                    }
                    with open(self.client.TOKEN_FILE_PATH, 'w') as f:
                        json.dump(token_data, f)
                    
                    logger.info("   (Auth) ✅ 새 접근 토큰 발급 및 저장 완료.")
                    return access_token
                    
                except requests.exceptions.ConnectionError as e:
                    if "Connection refused" in str(e):
                        logger.warning(f"⚠️ (Auth) Mock KIS API 서버에 연결할 수 없습니다 (URL: {TOKEN_URL})")
                        logger.warning(f"⚠️ (Auth) Mock KIS API 서버를 먼저 실행하세요: python utilities/mock_kis_api_server.py")
                    else:
                        logger.error(f"❌ (Auth) 토큰 발급 API 연결 실패: {e}")
                    return None
                except requests.exceptions.RequestException as e:
                    logger.error(f"❌ (Auth) 토큰 발급 API 요청 실패: {e}")
                    return None
                # --- 임계 영역 종료 ---
        except Timeout:
            logger.error(f"❌ (Auth) 토큰 파일 잠금 시간 초과: {lock_path}. 다른 프로세스에 의해 잠겨있을 수 있습니다.")
            return None

    def get_ws_approval_key(self):
        """실시간 웹소켓 접속을 위한 승인 키를 발급받습니다."""
        URL = f"{self.client.BASE_URL}/oauth2/Approval"
        data = {"grant_type": "client_credentials", "appkey": self.client.APP_KEY, "secretkey": self.client.APP_SECRET}
        res_data = self.client.request('POST', URL, data=data, tr_id="KIS-WS-AUTH")
        return res_data.get('approval_key') if res_data else None