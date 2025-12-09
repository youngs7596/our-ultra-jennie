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
    source_url: Optional[str] = None,
    stock_name: Optional[str] = None,
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
        source_url: 뉴스 원문 링크
        stock_name: 종목명 (옵션)
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
    existing_url = None
    existing_name = None
    try:
        old_data_json = r.get(key)
        if old_data_json:
            old_data = json.loads(old_data_json)
            old_score = old_data.get('score', 50)
            existing_url = old_data.get('source_url')
            existing_name = old_data.get('stock_name')
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
        "source_url": source_url or existing_url, # URL은 최신꺼 우선, 없으면 기존꺼
        "stock_name": stock_name or existing_name,
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
        {'score': 50, 'reason': 'No Data', 'source_url': None, 'stock_name': None} (기본값)
    """
    default_result = {"score": 50, "reason": "데이터 없음 (중립)", "source_url": None, "stock_name": None}
    
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


# ============================================================================
# Trading Control Flags (Telegram 명령어용)
# ============================================================================

# Redis Key 상수
TRADING_PAUSE_KEY = "trading:pause"
TRADING_STOP_KEY = "trading:stop"
TRADING_DRYRUN_KEY = "trading:dryrun"
CONFIG_MIN_LLM_SCORE_KEY = "config:min_llm_score"
CONFIG_MAX_BUY_PER_DAY_KEY = "config:max_buy_per_day"
NOTIFICATION_MUTE_KEY = "notification:mute"


def set_trading_flag(
    flag_name: str,
    value: bool,
    reason: str = "",
    ttl_seconds: int = 86400,  # 기본 24시간
    redis_client=None
) -> bool:
    """
    [Redis] 트레이딩 제어 플래그를 설정합니다.
    
    Args:
        flag_name: 플래그 이름 (pause, stop, dryrun)
        value: True/False
        reason: 설정 사유 (로깅용)
        ttl_seconds: TTL (기본 24시간, 다음날 자동 해제)
        redis_client: 테스트용 Redis 클라이언트
    
    Returns:
        성공 여부
    """
    r = get_redis_connection(redis_client)
    if not r:
        return False
    
    key_map = {
        "pause": TRADING_PAUSE_KEY,
        "stop": TRADING_STOP_KEY,
        "dryrun": TRADING_DRYRUN_KEY
    }
    
    key = key_map.get(flag_name.lower())
    if not key:
        logger.error(f"❌ [Redis] 알 수 없는 플래그: {flag_name}")
        return False
    
    try:
        data = {
            "value": value,
            "reason": reason,
            "set_at": datetime.now(timezone.utc).isoformat()
        }
        r.setex(key, ttl_seconds, json.dumps(data))
        
        status = "ON ✅" if value else "OFF ⭕"
        logger.info(f"🚦 [Redis] Trading Flag 설정: {flag_name.upper()} = {status} (이유: {reason})")
        return True
    except Exception as e:
        logger.error(f"❌ [Redis] Trading Flag 설정 실패: {e}")
        return False


def get_trading_flag(
    flag_name: str,
    redis_client=None
) -> Dict[str, Any]:
    """
    [Redis] 트레이딩 제어 플래그를 조회합니다.
    
    Args:
        flag_name: 플래그 이름 (pause, stop, dryrun)
        redis_client: 테스트용 Redis 클라이언트
    
    Returns:
        {'value': False, 'reason': '', 'set_at': None} (기본값)
    """
    default_result = {"value": False, "reason": "", "set_at": None}
    
    r = get_redis_connection(redis_client)
    if not r:
        return default_result
    
    key_map = {
        "pause": TRADING_PAUSE_KEY,
        "stop": TRADING_STOP_KEY,
        "dryrun": TRADING_DRYRUN_KEY
    }
    
    key = key_map.get(flag_name.lower())
    if not key:
        return default_result
    
    try:
        data_json = r.get(key)
        if data_json:
            return json.loads(data_json)
        return default_result
    except Exception as e:
        logger.error(f"❌ [Redis] Trading Flag 조회 실패: {e}")
        return default_result


def is_trading_paused(redis_client=None) -> bool:
    """
    [Redis] 매수가 일시 중지되었는지 확인합니다.
    
    Returns:
        True면 매수 중지 상태
    """
    flag = get_trading_flag("pause", redis_client)
    return flag.get("value", False)


def is_trading_stopped(redis_client=None) -> bool:
    """
    [Redis] 전체 거래가 중단되었는지 확인합니다.
    
    Returns:
        True면 전체 거래 중단 상태
    """
    flag = get_trading_flag("stop", redis_client)
    return flag.get("value", False)


def is_dryrun_enabled(redis_client=None) -> bool:
    """
    [Redis] DRY_RUN 모드가 활성화되었는지 확인합니다.
    (환경변수 DRY_RUN보다 Redis 설정이 우선)
    
    Returns:
        True면 DRY_RUN 모드
    """
    flag = get_trading_flag("dryrun", redis_client)
    # Redis에 설정이 있으면 그 값 사용, 없으면 환경변수 사용
    if flag.get("set_at"):
        return flag.get("value", False)
    
    # 환경변수 fallback
    return os.getenv("DRY_RUN", "true").lower() == "true"


def get_all_trading_flags(redis_client=None) -> Dict[str, Dict[str, Any]]:
    """
    [Redis] 모든 트레이딩 플래그 상태를 조회합니다.
    
    Returns:
        {'pause': {...}, 'stop': {...}, 'dryrun': {...}}
    """
    return {
        "pause": get_trading_flag("pause", redis_client),
        "stop": get_trading_flag("stop", redis_client),
        "dryrun": get_trading_flag("dryrun", redis_client)
    }


def set_config_value(
    config_name: str,
    value: Any,
    ttl_seconds: int = 86400,
    redis_client=None
) -> bool:
    """
    [Redis] 동적 설정값을 저장합니다.
    
    Args:
        config_name: 설정 이름 (min_llm_score, max_buy_per_day)
        value: 설정값
        ttl_seconds: TTL (기본 24시간)
        redis_client: 테스트용 Redis 클라이언트
    
    Returns:
        성공 여부
    """
    r = get_redis_connection(redis_client)
    if not r:
        return False
    
    key_map = {
        "min_llm_score": CONFIG_MIN_LLM_SCORE_KEY,
        "max_buy_per_day": CONFIG_MAX_BUY_PER_DAY_KEY
    }
    
    key = key_map.get(config_name.lower())
    if not key:
        logger.error(f"❌ [Redis] 알 수 없는 설정 이름: {config_name}")
        return False
    
    try:
        data = {
            "value": value,
            "set_at": datetime.now(timezone.utc).isoformat()
        }
        r.setex(key, ttl_seconds, json.dumps(data))
        logger.info(f"⚙️ [Redis] 설정 변경: {config_name} = {value}")
        return True
    except Exception as e:
        logger.error(f"❌ [Redis] 설정 저장 실패: {e}")
        return False


def get_config_value(
    config_name: str,
    default_value: Any = None,
    redis_client=None
) -> Any:
    """
    [Redis] 동적 설정값을 조회합니다.
    
    Args:
        config_name: 설정 이름
        default_value: 기본값
        redis_client: 테스트용 Redis 클라이언트
    
    Returns:
        설정값 또는 기본값
    """
    r = get_redis_connection(redis_client)
    if not r:
        return default_value
    
    key_map = {
        "min_llm_score": CONFIG_MIN_LLM_SCORE_KEY,
        "max_buy_per_day": CONFIG_MAX_BUY_PER_DAY_KEY
    }
    
    key = key_map.get(config_name.lower())
    if not key:
        return default_value
    
    try:
        data_json = r.get(key)
        if data_json:
            data = json.loads(data_json)
            return data.get("value", default_value)
        return default_value
    except Exception as e:
        logger.error(f"❌ [Redis] 설정 조회 실패: {e}")
        return default_value


def set_notification_mute(
    until_timestamp: int,
    redis_client=None
) -> bool:
    """
    [Redis] 알림 음소거를 설정합니다.
    
    Args:
        until_timestamp: 음소거 해제 시각 (Unix timestamp)
        redis_client: 테스트용 Redis 클라이언트
    
    Returns:
        성공 여부
    """
    r = get_redis_connection(redis_client)
    if not r:
        return False
    
    try:
        # TTL은 음소거 시간과 동일하게 설정
        now = int(datetime.now(timezone.utc).timestamp())
        ttl = max(0, until_timestamp - now)
        
        data = {
            "until": until_timestamp,
            "set_at": datetime.now(timezone.utc).isoformat()
        }
        r.setex(NOTIFICATION_MUTE_KEY, ttl, json.dumps(data))
        logger.info(f"🔇 [Redis] 알림 음소거 설정: {ttl}초 동안")
        return True
    except Exception as e:
        logger.error(f"❌ [Redis] 알림 음소거 설정 실패: {e}")
        return False


def is_notification_muted(redis_client=None) -> bool:
    """
    [Redis] 알림이 음소거 상태인지 확인합니다.
    
    Returns:
        True면 음소거 상태
    """
    r = get_redis_connection(redis_client)
    if not r:
        return False
    
    try:
        data_json = r.get(NOTIFICATION_MUTE_KEY)
        if data_json:
            data = json.loads(data_json)
            until = data.get("until", 0)
            now = int(datetime.now(timezone.utc).timestamp())
            return now < until
        return False
    except Exception as e:
        logger.error(f"❌ [Redis] 알림 음소거 상태 조회 실패: {e}")
        return False


def clear_notification_mute(redis_client=None) -> bool:
    """
    [Redis] 알림 음소거를 해제합니다.
    
    Returns:
        성공 여부
    """
    r = get_redis_connection(redis_client)
    if not r:
        return False
    
    try:
        r.delete(NOTIFICATION_MUTE_KEY)
        logger.info("🔔 [Redis] 알림 음소거 해제")
        return True
    except Exception as e:
        logger.error(f"❌ [Redis] 알림 음소거 해제 실패: {e}")
        return False
