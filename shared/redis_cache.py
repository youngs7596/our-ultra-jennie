"""
shared/redis_cache.py - Redis 캐시 유틸리티 모듈
=================================================

이 모듈은 Redis 캐시 연동을 담당합니다.
database.py에서 분리되어 단일 책임 원칙(SRP)을 준수하며,
의존성 주입(DI)을 지원하여 테스트가 용이합니다.

핵심 기능:
---------
1. Redis 연결 관리: 싱글톤 또는 의존성 주입
2. 시장 국면 캐시: Market Regime 정보 공유
3. 감성 점수 캐시: 뉴스 감성 분석 결과 저장
4. 경쟁사 수혜 점수: 경쟁사 이벤트 기반 점수 관리

사용 예시:
---------
>>> from shared.redis_cache import get_sentiment_score, set_sentiment_score
>>> 
>>> # 기본 사용 (전역 Redis 클라이언트)
>>> set_sentiment_score("005930", 75, "긍정적 뉴스")
>>> data = get_sentiment_score("005930")
>>> print(data)  # {'score': 75, 'reason': '긍정적 뉴스', ...}
>>>
>>> # 테스트용 (의존성 주입)
>>> import fakeredis
>>> fake_redis = fakeredis.FakeStrictRedis(decode_responses=True)
>>> set_sentiment_score("005930", 80, "테스트", redis_client=fake_redis)

환경변수:
--------
- REDIS_URL: Redis 연결 URL (기본: redis://localhost:6379)
"""

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# ============================================================================
# REDIS 연결 관리
# ============================================================================

_redis_client = None
MARKET_REGIME_CACHE_KEY = "market_regime_cache"


def get_redis_connection(redis_client=None):
    """
    Redis 연결 객체를 반환합니다.
    
    Args:
        redis_client: 테스트용 Redis 클라이언트 (의존성 주입)
                     None이면 전역 싱글톤 사용
    
    Returns:
        Redis 클라이언트 또는 None (연결 실패 시)
    """
    # 의존성 주입된 클라이언트가 있으면 사용
    if redis_client is not None:
        return redis_client
    
    global _redis_client
    if _redis_client:
        try:
            _redis_client.ping()
            return _redis_client
        except Exception:
            logger.warning("⚠️ Redis 연결이 끊겨 재연결을 시도합니다.")
            _redis_client = None

    # 지연 import (redis가 설치되지 않은 환경 대응)
    try:
        import redis
    except ImportError:
        logger.error("❌ redis 패키지가 설치되지 않았습니다.")
        return None

    # 환경 변수 REDIS_URL 사용 (예: redis://10.178.0.2:6379)
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    
    try:
        _redis_client = redis.from_url(
            redis_url,
            db=0,
            socket_timeout=0.5,  # Fast fail
            socket_connect_timeout=0.5,
            decode_responses=True  # 문자열로 자동 디코딩
        )
        _redis_client.ping()
        logger.info(f"✅ Redis 연결 성공 ({redis_url})")
        return _redis_client
    except Exception as e:
        logger.error(f"❌ Redis 연결 실패: {e}")
        return None


def reset_redis_connection():
    """
    Redis 연결을 리셋합니다. (테스트용)
    """
    global _redis_client
    _redis_client = None


# ============================================================================
# 시장 국면 (Market Regime) 캐시
# ============================================================================

def set_market_regime_cache(
    regime_payload: dict, 
    ttl_seconds: int = 3600,
    redis_client=None
) -> bool:
    """
    [Redis] 시장 Regime/Risk/Preset 정보를 공유 캐시에 저장합니다.
    
    Args:
        regime_payload: 저장할 데이터 딕셔너리
        ttl_seconds: TTL (기본 1시간)
        redis_client: 테스트용 Redis 클라이언트 (의존성 주입)
    
    Returns:
        성공 여부
    """
    if not regime_payload:
        return False
    
    r = get_redis_connection(redis_client)
    if not r:
        logger.warning("⚠️ Redis 미연결 상태로 Regime 캐시 저장 실패")
        return False
    
    payload = regime_payload.copy()
    payload["_cached_at"] = datetime.now(timezone.utc).isoformat()
    
    try:
        r.setex(MARKET_REGIME_CACHE_KEY, ttl_seconds, json.dumps(payload))
        logger.debug(f"✅ [Redis] Regime 캐시 저장 완료 (TTL={ttl_seconds}s)")
        return True
    except Exception as e:
        logger.error(f"❌ [Redis] Regime 캐시 저장 실패: {e}")
        return False


def get_market_regime_cache(
    max_age_seconds: int = 3600,
    redis_client=None
) -> Optional[Dict[str, Any]]:
    """
    [Redis] 공유 Regime 캐시를 조회합니다.
    
    Args:
        max_age_seconds: 최대 허용 캐시 나이 (기본 1시간)
        redis_client: 테스트용 Redis 클라이언트 (의존성 주입)
    
    Returns:
        캐시된 데이터 또는 None (없거나 만료됨)
    """
    r = get_redis_connection(redis_client)
    if not r:
        return None
    
    try:
        data_json = r.get(MARKET_REGIME_CACHE_KEY)
        if not data_json:
            return None
        
        data = json.loads(data_json)
        cached_at_str = data.get("_cached_at")
        
        if cached_at_str and max_age_seconds:
            try:
                cached_at = datetime.fromisoformat(cached_at_str)
                age = datetime.now(timezone.utc) - cached_at
                if age > timedelta(seconds=max_age_seconds):
                    logger.info(f"ℹ️ [Redis] Regime 캐시 만료 (Age={age.total_seconds():.0f}s)")
                    return None
            except Exception:
                logger.debug("Regime 캐시 timestamp 파싱 실패 (무시)")
        
        return data
    except Exception as e:
        logger.error(f"❌ [Redis] Regime 캐시 조회 실패: {e}")
        return None


# ============================================================================
# 뉴스 감성 점수 (Sentiment Score) 캐시
# ============================================================================

def set_sentiment_score(
    stock_code: str, 
    score: int, 
    reason: str,
    redis_client=None
) -> bool:
    """
    [Redis] 종목의 실시간 뉴스 감성 점수를 저장합니다. (TTL: 2시간)
    기존 점수가 있다면 지수 이동 평균(EMA)을 적용하여 급격한 변화를 완화합니다.
    (기존 70% + 신규 30%)
    
    Args:
        stock_code: 종목 코드
        score: 감성 점수 (0-100)
        reason: 감성 분석 사유
        redis_client: 테스트용 Redis 클라이언트 (의존성 주입)
    
    Returns:
        성공 여부
    """
    r = get_redis_connection(redis_client)
    if not r:
        return False
    
    key = f"sentiment:{stock_code}"
    
    # 기존 점수 조회
    old_score = 50
    old_data_json = None
    try:
        old_data_json = r.get(key)
        if old_data_json:
            old_data = json.loads(old_data_json)
            old_score = old_data.get('score', 50)
    except Exception:
        pass

    # EMA 계산 (기존 데이터가 없으면 신규 점수 100% 반영)
    if old_data_json:
        final_score = (old_score * 0.7) + (score * 0.3)
        # 이유도 합침 (최신 이유 + 기존 이유 요약)
        final_reason = f"[New: {score}점] {reason} | [Old: {old_score:.1f}점]"
    else:
        final_score = score
        final_reason = reason

    data = {
        "score": round(final_score, 1),
        "reason": final_reason,
        "updated_at": datetime.now().isoformat()
    }
    
    try:
        # 해시(Hash) 대신 JSON 문자열로 저장 (간편함)
        r.setex(key, 7200, json.dumps(data))  # 2시간(7200초) 유효
        logger.debug(f"✅ [Redis] 감성 점수 업데이트: {stock_code} -> {final_score:.1f}점 (Input: {score})")
        return True
    except Exception as e:
        logger.error(f"❌ [Redis] 감성 점수 저장 실패: {e}")
        return False


def get_sentiment_score(
    stock_code: str,
    redis_client=None
) -> Dict[str, Any]:
    """
    [Redis] 종목의 실시간 감성 점수를 조회합니다.
    
    Args:
        stock_code: 종목 코드
        redis_client: 테스트용 Redis 클라이언트 (의존성 주입)
    
    Returns:
        {'score': 50, 'reason': 'No Data'} (기본값)
    """
    default_result = {"score": 50, "reason": "데이터 없음 (중립)"}
    
    r = get_redis_connection(redis_client)
    if not r:
        return default_result
    
    key = f"sentiment:{stock_code}"
    try:
        data_json = r.get(key)
        if data_json:
            return json.loads(data_json)
        return default_result
    except Exception as e:
        logger.error(f"❌ [Redis] 감성 점수 조회 실패: {e}")
        return default_result


# ============================================================================
# 일반 데이터 캐시
# ============================================================================

def set_redis_data(
    key: str, 
    data: dict, 
    ttl: int = 86400,
    redis_client=None
) -> bool:
    """
    [Redis] 일반 데이터를 JSON 형태로 저장합니다.
    
    Args:
        key: Redis 키
        data: 저장할 딕셔너리 데이터
        ttl: 유효 시간 (초, 기본 24시간)
        redis_client: 테스트용 Redis 클라이언트 (의존성 주입)
    
    Returns:
        성공 여부
    """
    r = get_redis_connection(redis_client)
    if not r:
        return False
    
    try:
        r.setex(key, ttl, json.dumps(data, default=str))
        logger.debug(f"✅ [Redis] 데이터 저장: {key}")
        return True
    except Exception as e:
        logger.error(f"❌ [Redis] 데이터 저장 실패 ({key}): {e}")
        return False


def get_redis_data(
    key: str,
    redis_client=None
) -> Dict[str, Any]:
    """
    [Redis] 일반 데이터를 조회합니다.
    
    Args:
        key: Redis 키
        redis_client: 테스트용 Redis 클라이언트 (의존성 주입)
    
    Returns:
        저장된 딕셔너리 데이터 또는 빈 딕셔너리
    """
    r = get_redis_connection(redis_client)
    if not r:
        return {}
    
    try:
        data_json = r.get(key)
        if data_json:
            return json.loads(data_json)
        return {}
    except Exception as e:
        logger.error(f"❌ [Redis] 데이터 조회 실패 ({key}): {e}")
        return {}


# ============================================================================
# 경쟁사 수혜 점수 (Competitor Benefit Score) 캐시
# ============================================================================

def set_competitor_benefit_score(
    stock_code: str, 
    score: int, 
    reason: str,
    affected_stock: str, 
    event_type: str, 
    ttl: int = 1728000,
    redis_client=None
) -> bool:
    """
    [Redis] 경쟁사 수혜 점수를 저장합니다. (기본 TTL: 20일)
    
    Args:
        stock_code: 수혜 받는 종목 코드
        score: 수혜 점수
        reason: 수혜 사유
        affected_stock: 악재 발생 종목
        event_type: 이벤트 유형 (보안사고, 리콜 등)
        ttl: 유효 시간 (초)
        redis_client: 테스트용 Redis 클라이언트 (의존성 주입)
    
    Returns:
        성공 여부
    """
    r = get_redis_connection(redis_client)
    if not r:
        return False
    
    key = f"competitor_benefit:{stock_code}"
    data = {
        "score": score,
        "reason": reason,
        "affected_stock": affected_stock,
        "event_type": event_type,
        "updated_at": datetime.now(timezone.utc).isoformat()
    }
    
    try:
        # 기존 점수가 있으면 더 높은 점수 유지
        existing = r.get(key)
        if existing:
            existing_data = json.loads(existing)
            if existing_data.get("score", 0) > score:
                logger.debug(f"ℹ️ [Redis] 경쟁사 수혜: {stock_code} 기존 점수가 더 높음 (Skip)")
                return True
        
        r.setex(key, ttl, json.dumps(data))
        logger.info(f"✅ [Redis] 경쟁사 수혜 저장: {stock_code} +{score}점 ({reason})")
        return True
    except Exception as e:
        logger.error(f"❌ [Redis] 경쟁사 수혜 저장 실패: {e}")
        return False


def get_competitor_benefit_score(
    stock_code: str,
    redis_client=None
) -> Dict[str, Any]:
    """
    [Redis] 경쟁사 수혜 점수를 조회합니다.
    
    Args:
        stock_code: 종목 코드
        redis_client: 테스트용 Redis 클라이언트 (의존성 주입)
    
    Returns:
        {'score': 0, 'reason': '', 'affected_stock': '', 'event_type': ''} (기본값)
    """
    default_result = {"score": 0, "reason": "", "affected_stock": "", "event_type": ""}
    
    r = get_redis_connection(redis_client)
    if not r:
        return default_result
    
    key = f"competitor_benefit:{stock_code}"
    try:
        data_json = r.get(key)
        if data_json:
            return json.loads(data_json)
        return default_result
    except Exception as e:
        logger.error(f"❌ [Redis] 경쟁사 수혜 조회 실패: {e}")
        return default_result


def get_all_competitor_benefits(redis_client=None) -> Dict[str, Dict[str, Any]]:
    """
    [Redis] 모든 경쟁사 수혜 점수를 조회합니다.
    
    Args:
        redis_client: 테스트용 Redis 클라이언트 (의존성 주입)
    
    Returns:
        {stock_code: {score, reason, ...}, ...}
    """
    r = get_redis_connection(redis_client)
    if not r:
        return {}
    
    try:
        keys = r.keys("competitor_benefit:*")
        results = {}
        for key in keys:
            stock_code = key.replace("competitor_benefit:", "")
            data_json = r.get(key)
            if data_json:
                results[stock_code] = json.loads(data_json)
        return results
    except Exception as e:
        logger.error(f"❌ [Redis] 경쟁사 수혜 전체 조회 실패: {e}")
        return {}

