"""
Gemini API Key 헬퍼.

로컬/WSL 환경에서는 ADC 대신 Gemini API Key를 secrets.json에서 읽어
`GOOGLE_API_KEY` 환경 변수에 주입한 뒤 반환한다.
"""

import logging
import os

from . import auth

logger = logging.getLogger(__name__)

_cached_gemini_api_key: str | None = None


def ensure_gemini_api_key() -> str:
    """
    Gemini API Key를 로드하여 반환합니다.
    우선순위:
      1) 이미 설정된 GOOGLE_API_KEY 환경 변수
      2) SECRET_ID_GEMINI_API_KEY / GCP_PROJECT_ID 조합으로 secrets.json 또는 GCP SM에서 로드
    반환된 키는 os.environ["GOOGLE_API_KEY"]에 저장되며, 이후 호출에서는 캐시를 사용합니다.
    """
    global _cached_gemini_api_key

    if _cached_gemini_api_key:
        return _cached_gemini_api_key

    api_key = os.getenv("GOOGLE_API_KEY")
    if api_key:
        _cached_gemini_api_key = api_key.strip()
        return _cached_gemini_api_key

    secret_id = os.getenv("SECRET_ID_GEMINI_API_KEY", "gemini-api-key")
    project_id = os.getenv("GCP_PROJECT_ID", "local")  # 로컬 환경에서는 "local" 사용

    # 먼저 secrets.json에서 직접 로드 시도
    api_key = auth.get_secret(secret_id, project_id)
    if not api_key:
        raise RuntimeError(f"Gemini API Key를 Secret '{secret_id}'에서 찾을 수 없습니다.")

    _cached_gemini_api_key = api_key.strip()
    os.environ["GOOGLE_API_KEY"] = _cached_gemini_api_key
    logger.info("✅ Gemini API Key 로드 완료 (GOOGLE_API_KEY 설정)")
    return _cached_gemini_api_key

