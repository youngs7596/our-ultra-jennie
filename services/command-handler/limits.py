"""
services/command-handler/limits.py

레이트 리미트와 수동 거래 횟수 한도 체크 유틸을 모아둡니다.
핵심 로직을 handler 본문에서 분리해 후속 정리·제거가 쉬운 형태로 유지합니다.
"""

import time
from datetime import datetime
from typing import Optional

import shared.redis_cache as redis_cache


def is_rate_limited(chat_id: Optional[int], min_interval_seconds: int) -> bool:
    """명령 최소 간격 제한."""
    if chat_id is None:
        return False
    r = redis_cache.get_redis_connection()
    if not r:
        return False
    key = f"telegram:rl:{chat_id}"
    try:
        last_ts = r.get(key)
        now = int(time.time())
        if last_ts and now - int(last_ts) < min_interval_seconds:
            return True
        r.setex(key, min_interval_seconds, now)
    except Exception:
        return False
    return False


def check_and_increment_manual_trade_limit(
    chat_id: Optional[int],
    daily_limit: int,
) -> Optional[str]:
    """
    일일 수동 거래 횟수 제한을 체크하고 카운트를 증가시킵니다.
    제한을 초과하면 에러 메시지를 반환하고, 아니면 None 반환.
    """
    if chat_id is None:
        return None
    r = redis_cache.get_redis_connection()
    if not r:
        return None
    key = f"telegram:manual_trades:{datetime.now().strftime('%Y%m%d')}:{chat_id}"
    try:
        count = int(r.get(key) or 0)
        if count >= daily_limit:
            return f"⛔ 일일 수동 거래 한도를 초과했습니다. (최대 {daily_limit}건)"
        r.setex(key, 86400, count + 1)
    except Exception:
        return None
    return None
