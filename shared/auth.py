# youngs75_jennie/auth.py
# [모듈] 로컬 secrets.json / 환경 변수 기반 Secret 로더

import json
import logging
import os
from pathlib import Path

# "youngs75_jennie.auth" 이름으로 로거 생성
logger = logging.getLogger(__name__) 

# Secret 캐시 (메모리 캐싱)
_secret_cache = {}
_local_secrets_cache = None


def _load_local_secrets():
    """
    로컬 secrets.json 파일을 로딩하여 캐싱합니다.
    파일이 없거나 파싱에 실패하면 빈 dict를 반환합니다.
    """
    global _local_secrets_cache
    if _local_secrets_cache is not None:
        return _local_secrets_cache

    secrets_path = os.getenv("SECRETS_FILE", "/app/config/secrets.json")
    path = Path(secrets_path)
    if not path.exists():
        logger.info("ℹ️ secrets.json(%s)이 존재하지 않습니다. Secret Manager 또는 환경 변수로 fallback 합니다.", secrets_path)
        _local_secrets_cache = {}
        return _local_secrets_cache

    try:
        with path.open("r", encoding="utf-8") as fp:
            data = json.load(fp)
            if not isinstance(data, dict):
                raise ValueError("secrets.json must be a JSON object")
            _local_secrets_cache = {str(k): str(v) for k, v in data.items()}
            logger.info("✅ secrets.json 로드 완료: %s", secrets_path)
    except Exception as exc:
        logger.error("❌ secrets.json 로드 실패 (%s): %s", secrets_path, exc)
        _local_secrets_cache = {}

    return _local_secrets_cache

def get_secret(secret_id, project_id=None, use_cache=True):
    """
    secrets.json 또는 환경 변수에서 Secret을 가져옵니다.
    GCP Secret Manager 호출 로직은 완전히 제거되었습니다.
    
    Args:
        secret_id (str): 가져올 Secret의 이름 (예: 'kis-r-app-key')
        project_id (str | None): 이전 호환성을 위한 값 (캐시 키에만 사용)
        use_cache (bool): 캐시 사용 여부 (기본값: True)

    Returns:
        str: Secret 값. 실패 시 None.
    """
    cache_scope = project_id or "local"
    
    # 캐시 확인 (성능 최적화)
    if use_cache:
        cache_key = f"{cache_scope}/{secret_id}"
        if cache_key in _secret_cache:
            logger.debug(f"🔧 Secret 캐시 히트: {secret_id}")
            return _secret_cache[cache_key]
    
    # 1) secrets.json 최우선
    local_secrets = _load_local_secrets()
    
    # 1-1) 정확한 키 매칭
    if secret_id in local_secrets:
        secret_value = local_secrets[secret_id].strip()
        if use_cache:
            cache_key = f"{cache_scope}/{secret_id}"
            _secret_cache[cache_key] = secret_value
        logger.debug("🔐 secrets.json 사용: %s", secret_id)
        return secret_value
        
    # 1-2) 하이픈/언더스코어 변환 시도 (예: telegram_bot_token <-> telegram-bot-token)
    alt_secret_id = secret_id.replace('_', '-') if '_' in secret_id else secret_id.replace('-', '_')
    if alt_secret_id in local_secrets:
        secret_value = local_secrets[alt_secret_id].strip()
        if use_cache:
            cache_key = f"{cache_scope}/{secret_id}"
            _secret_cache[cache_key] = secret_value
        logger.info("🔐 secrets.json 사용 (키 변환): %s -> %s", secret_id, alt_secret_id)
        return secret_value
    
    # 2) 환경 변수 fallback
    # 로컬 테스트를 위한 환경 변수 매핑
    local_env_mapping = {
        "oracle-db-user": "ORACLE_USER",
        "oracle-db-password": "ORACLE_PASSWORD",
        "mock-app-key": "MOCK_APP_KEY",
        "mock-app-secret": "MOCK_APP_SECRET",
        "mock-account-prefix": "MOCK_ACCOUNT_NO",
        "gemini-api-key": "GEMINI_API_KEY",
    }
    
    # 로컬 환경 체크: 환경 변수가 직접 설정되어 있으면 사용
    if secret_id in local_env_mapping:
        env_var = local_env_mapping[secret_id]
        env_value = os.getenv(env_var)
        if env_value:
            logger.info(f"✅ 로컬 환경 변수 사용: {secret_id} -> {env_var}")
            if use_cache:
                cache_key = f"{cache_scope}/{secret_id}"
                _secret_cache[cache_key] = env_value
            return env_value
            
    # 3) 환경 변수 직접 조회 (fallback)
    env_val = os.getenv(secret_id)
    if env_val:
        if use_cache:
             cache_key = f"{cache_scope}/{secret_id}"
             _secret_cache[cache_key] = env_val
        return env_val

    logger.error("❌ Secret '%s'를 secrets.json 또는 환경 변수에서 찾을 수 없습니다.", secret_id)
    return None

def clear_secret_cache():
    """Secret 캐시 초기화 (테스트/디버깅용)"""
    global _secret_cache, _local_secrets_cache
    _secret_cache.clear()
    _local_secrets_cache = None
    logger.info("🔧 Secret 캐시 초기화 완료")
