"""
shared/config.py - Ultra Jennie 설정 관리 모듈
=============================================

이 모듈은 중앙화된 설정 관리를 담당합니다.

설정값 우선순위:
--------------
1. 메모리 캐시 (런타임 변경값)
2. DB CONFIG 테이블 (동적 설정)
3. 환경 변수
4. 기본값 (하드코딩)

주요 설정 카테고리:
-----------------
1. 매수 관련: RSI 기준, 볼린저밴드, 골든크로스 등
2. 매도 관련: 익절/손절 비율, RSI 과매수 구간
3. 포지션 관리: 최대 보유 종목, 섹터/종목별 비중 제한
4. 시장 국면: Bull/Bear/Sideways별 전략 파라미터

사용 예시:
---------
>>> from shared.config import ConfigManager
>>>
>>> config = ConfigManager()
>>> scan_interval = config.get('SCAN_INTERVAL_SEC', default=600)
>>> rsi_threshold = config.get('BUY_RSI_OVERSOLD_THRESHOLD', default=30)

주요 설정 키:
-----------
- SCAN_INTERVAL_SEC: 스캔 간격 (초)
- BUY_RSI_OVERSOLD_THRESHOLD: 과매도 RSI 기준 (기본: 30)
- MAX_HOLDING_STOCKS: 최대 보유 종목 수
- PROFIT_TARGET_FULL: 전량 익절 목표 (%)
- SELL_STOP_LOSS_PCT: 손절 기준 (%)
- MAX_SECTOR_PCT: 섹터별 최대 비중 (%)
- MAX_STOCK_PCT: 종목별 최대 비중 (%)
"""

import os
import logging
from typing import Any, Optional, Dict
from functools import lru_cache

logger = logging.getLogger(__name__)


class ConfigManager:
    """
    중앙화된 설정 관리 클래스
    
    설정값 우선순위:
    1. 메모리 캐시 (런타임 변경값)
    2. DB CONFIG 테이블 (동적 설정)
    3. 환경 변수
    4. 기본값 (하드코딩)
    
    사용 예시:
        config = ConfigManager(db_conn=connection)
        scan_interval = config.get('SCAN_INTERVAL_SEC', default=600)
        daily_limit = config.get('DAILY_BUY_LIMIT_AMOUNT', default=10000000)
    """
    
    def __init__(self, db_conn=None, cache_ttl: int = 300):
        """
        Args:
            db_conn: DB 연결 (더 이상 사용되지 않음, 하위 호환성을 위해 유지)
            cache_ttl: 캐시 TTL (초, 기본값 5분)
        
        Note:
            db_conn 파라미터는 하위 호환성을 위해 유지되지만 실제로는 사용되지 않습니다.
            ConfigManager는 연결 풀을 직접 사용하여 DB에 접근합니다.
        """
        # db_conn은 더 이상 사용하지 않음 (연결 풀을 직접 사용)
        self.db_conn = None
        self.cache_ttl = cache_ttl
        self._memory_cache: Dict[str, tuple] = {}  # {key: (value, timestamp)}
        self._db_cache: Dict[str, tuple] = {}  # {key: (value, timestamp)}
        
        # 기본값 정의 (AgentConfig에서 가져온 값들)
        self._defaults = {
            # 매수 관련
            'SCAN_INTERVAL_SEC': 600,
            'MARKET_INDEX_MA_PERIOD': 20,
            'BUY_BOLLINGER_PERIOD': 20,
            'BUY_RSI_OVERSOLD_THRESHOLD': 30,
            'BUY_GOLDEN_CROSS_SHORT': 5,
            'BUY_GOLDEN_CROSS_LONG': 20,
            'MAX_HOLDING_STOCKS': 50,
            'DEFAULT_DAILY_BUY_LIMIT_AMOUNT': 10000000,
            'BUY_QUANTITY_PER_TRADE': 1,
            'ALLOW_BEAR_TRADING': False,
            'MIN_LLM_CONFIDENCE_BEAR': 85,
            'BEAR_POSITION_RATIO': 0.2,
            'BEAR_STOP_LOSS_ATR_MULT': 2.0,
            'BEAR_FIRST_TP_PCT': 0.03,
            'BEAR_PARTIAL_CLOSE_RATIO': 0.5,
            'BEAR_VOLUME_SPIKE_MULTIPLIER': 1.5,
            
            # 매도 관련
            'ATR_PERIOD': 14,
            'ATR_MULTIPLIER_INITIAL_STOP': 2.0,
            'ATR_MULTIPLIER_TRAILING_STOP': 1.5,
            'SELL_RSI_THRESHOLD': 70,
            'RSI_THRESHOLD_1': 72.0,
            'RSI_THRESHOLD_2': 75.0,
            'RSI_THRESHOLD_3': 78.0,
            'PROFIT_TARGET_FULL': 8.0,
            'PROFIT_TARGET_PARTIAL': 6.0,
            'TIME_BASED_BULL': 20,
            'TIME_BASED_SIDEWAYS': 35,
            
            # 포지션 사이징
            'MAX_POSITION_PCT': 15, # Legacy (keep for safety)
            'MAX_POSITION_VALUE_PCT': 15.0, # [v3.5] Backtest Optimized (15%)
            'CASH_KEEP_PCT': 10, # [v3.5] Backtest Optimized (10%)
            'RISK_PER_TRADE_PCT': 2.0, # 거래당 리스크 기본값 (2%)
        }
    
    def get(self, key: str, default: Any = None, use_cache: bool = True) -> Any:
        """
        설정값 조회 (우선순위: 메모리 캐시 > DB > 환경 변수 > 기본값)
        
        Args:
            key: 설정 키
            default: 기본값 (없으면 _defaults에서 조회)
            use_cache: 캐시 사용 여부
        
        Returns:
            설정값 (타입 자동 변환 시도)
        """
        import time
        current_time = time.time()
        
        # 1. 메모리 캐시 확인
        if use_cache and key in self._memory_cache:
            value, timestamp = self._memory_cache[key]
            if current_time - timestamp < self.cache_ttl:
                logger.debug(f"[Config] 메모리 캐시에서 '{key}' 조회: {value}")
                return value
        
        # 2. DB CONFIG 테이블 확인
        # 컨텍스트 매니저가 Pool 또는 Stateless 연결을 자동으로 처리
        try:
            # DB 캐시 확인
            if use_cache and key in self._db_cache:
                value, timestamp = self._db_cache[key]
                if current_time - timestamp < self.cache_ttl:
                    logger.debug(f"[Config] DB 캐시에서 '{key}' 조회: {value}")
                    return value
            
            # DB 조회 (Pool 또는 Stateless 모드 자동 처리)
            from . import database
            with database.get_db_connection_context() as db_conn:
                db_value = database.get_config(db_conn, key, silent=True)
                if db_value is not None:
                    # DB 캐시 업데이트
                    self._db_cache[key] = (db_value, current_time)
                    logger.debug(f"[Config] DB에서 '{key}' 조회: {db_value}")
                    return self._convert_type(key, db_value)
        except Exception as e:
            logger.warning(f"[Config] DB에서 '{key}' 조회 실패 (환경변수/기본값으로 대체): {e}")
        
        # 3. 환경 변수 확인
        env_value = os.getenv(key)
        if env_value is not None:
            logger.debug(f"[Config] 환경 변수에서 '{key}' 조회: {env_value}")
            return self._convert_type(key, env_value)
        
        # 4. 기본값 반환
        if default is not None:
            logger.debug(f"[Config] 기본값 사용 '{key}': {default}")
            return default
        
        if key in self._defaults:
            default_value = self._defaults[key]
            # 초기화 시에는 INFO 레벨로 로그 (설정값이 제대로 적용되는지 확인)
            logger.info(f"[Config] 내장 기본값 사용 '{key}': {default_value}")
            return default_value
        
        logger.warning(f"[Config] 설정값 '{key}'를 찾을 수 없습니다. None 반환.")
        return None
    
    def set(self, key: str, value: Any, persist_to_db: bool = False) -> bool:
        """
        설정값 설정 (메모리 캐시에 저장, 선택적으로 DB에도 저장)
        
        Args:
            key: 설정 키
            value: 설정값
            persist_to_db: DB에도 저장할지 여부
        
        Returns:
            성공 여부
        """
        import time
        current_time = time.time()
        
        # 메모리 캐시 업데이트
        self._memory_cache[key] = (value, current_time)
        logger.info(f"[Config] 메모리 캐시에 '{key}' 설정: {value}")
        
        # DB에도 저장 (컨텍스트 매니저가 자동으로 Pool/Stateless 처리)
        if persist_to_db:
            try:
                from . import database
                with database.get_db_connection_context() as db_conn:
                    database.set_config(db_conn, key, str(value))
                # DB 캐시도 업데이트
                self._db_cache[key] = (str(value), current_time)
                logger.info(f"[Config] DB에도 '{key}' 저장: {value}")
                return True
            except Exception as e:
                logger.error(f"[Config] DB에 '{key}' 저장 실패: {e}")
                return False
        
        return True
    
    def get_int(self, key: str, default: int = None) -> int:
        """정수형 설정값 조회"""
        value = self.get(key, default=default)
        if value is None:
            return default if default is not None else 0
        try:
            return int(value)
        except (ValueError, TypeError):
            logger.warning(f"[Config] '{key}'를 정수로 변환 실패: {value}, 기본값 사용")
            return default if default is not None else 0
    
    def get_float(self, key: str, default: float = None) -> float:
        """실수형 설정값 조회"""
        value = self.get(key, default=default)
        if value is None:
            return default if default is not None else 0.0
        try:
            return float(value)
        except (ValueError, TypeError):
            logger.warning(f"[Config] '{key}'를 실수로 변환 실패: {value}, 기본값 사용")
            return default if default is not None else 0.0
    
    def get_bool(self, key: str, default: bool = None) -> bool:
        """불린형 설정값 조회"""
        value = self.get(key, default=default)
        if value is None:
            return default if default is not None else False
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ('true', '1', 'yes', 'on')
        return bool(value)
    
    def clear_cache(self, key: Optional[str] = None):
        """
        캐시 초기화
        
        Args:
            key: 특정 키만 초기화 (None이면 전체 초기화)
        """
        if key:
            self._memory_cache.pop(key, None)
            self._db_cache.pop(key, None)
            logger.debug(f"[Config] 캐시 초기화: '{key}'")
        else:
            self._memory_cache.clear()
            self._db_cache.clear()
            logger.debug("[Config] 전체 캐시 초기화")
    
    def _convert_type(self, key: str, value: Any) -> Any:
        """
        설정값 타입 자동 변환
        
        기본값의 타입을 참고하여 변환 시도
        """
        if value is None:
            return None
        
        # 기본값이 있는 경우 타입 참고
        if key in self._defaults:
            default_value = self._defaults[key]
            if isinstance(default_value, int):
                try:
                    return int(value)
                except (ValueError, TypeError):
                    pass
            elif isinstance(default_value, float):
                try:
                    return float(value)
                except (ValueError, TypeError):
                    pass
            elif isinstance(default_value, bool):
                if isinstance(value, str):
                    return value.lower() in ('true', '1', 'yes', 'on')
                return bool(value)
        
        return value
    
    def get_all(self) -> Dict[str, Any]:
        """
        모든 설정값 조회 (디버깅/모니터링용)
        
        Returns:
            모든 설정값 딕셔너리
        """
        all_config = {}
        
        # 기본값
        for key in self._defaults:
            all_config[key] = self.get(key)
        
        return all_config


# 전역 ConfigManager 인스턴스 (선택적 사용)
_global_config: Optional[ConfigManager] = None


def get_global_config(db_conn=None) -> ConfigManager:
    """
    전역 ConfigManager 인스턴스 반환 (싱글톤 패턴)
    
    Args:
        db_conn: DB 연결 (더 이상 사용되지 않음, 하위 호환성을 위해 유지)
    
    Returns:
        전역 ConfigManager 인스턴스
    
    Note:
        db_conn 파라미터는 하위 호환성을 위해 유지되지만 실제로는 사용되지 않습니다.
        ConfigManager는 연결 풀을 직접 사용합니다.
    """
    global _global_config
    if _global_config is None:
        _global_config = ConfigManager(db_conn=None)  # 연결 풀을 직접 사용하므로 None
    return _global_config


def reset_global_config():
    """전역 ConfigManager 인스턴스 초기화 (테스트용)"""
    global _global_config
    _global_config = None
