# shared/strategy_presets.py
# Version: v1.0
"""
Strategy Preset Loader & Utilities - v1.0

이 모듈은 gpt_v2_strategy_presets.json 파일을 로드하여
백테스트/실전 서비스 모두가 동일한 파라미터 세트를 공유하도록 돕는다.

주요 기능:
- 전략 프리셋 JSON 로드 (캐시됨)
- 시장 국면(Regime)에 따른 프리셋 자동 선택
- ConfigManager에 프리셋 값 주입
"""

from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from typing import Dict, List, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRESET_CONFIG_PATH = os.path.join(PROJECT_ROOT, "configs", "gpt_v2_strategy_presets.json")

logger = logging.getLogger(__name__)

# 백테스트 CLI와 실전 서비스가 공유할 기본값
STRATEGY_PARAM_DEFAULTS: Dict[str, float] = {
    "rsi_buy": 30,
    "breakout_buffer_pct": 0.5,
    "bb_buffer_pct": 1.5,
    "llm_threshold": 70,
    "max_position_allocation": 12.0,
    "max_stock_pct": 15.0,
    "max_sector_pct": 30.0,
    "cash_keep_pct": 5.0,
    "target_profit_pct": 8.0,
    "base_stop_loss_pct": 5.0,
    "stop_loss_atr_mult": 1.8,
    "sell_rsi_1": 70.0,
    "sell_rsi_2": 75.0,
    "sell_rsi_3": 80.0,
    "max_buys_per_day": 3,
    "max_hold_days": 30,
}

# Regime별 기본 매핑 (필요 시 서비스 레벨에서 override 가능)
DEFAULT_REGIME_PRESETS = {
    "BULL": "aggressive_swing",
    "SIDEWAYS": "balanced_champion",
    "BEAR": "iron_shield",
}

CONFIG_KEY_MAP = {
    "rsi_buy": "BUY_RSI_OVERSOLD_THRESHOLD",
    "target_profit_pct": "PROFIT_TARGET_FULL",
    "base_stop_loss_pct": "SELL_STOP_LOSS_PCT",
    "stop_loss_atr_mult": "ATR_MULTIPLIER_INITIAL_STOP",
    "sell_rsi_1": "RSI_THRESHOLD_1",
    "sell_rsi_2": "RSI_THRESHOLD_2",
    "sell_rsi_3": "RSI_THRESHOLD_3",
    "max_position_allocation": "MAX_POSITION_VALUE_PCT",
    "max_stock_pct": "MAX_STOCK_PCT",
    "max_sector_pct": "MAX_SECTOR_PCT",
    "cash_keep_pct": "CASH_KEEP_PCT",
    "max_buys_per_day": "MAX_BUY_COUNT_PER_DAY",
    "max_hold_days": "MAX_HOLDING_DAYS",
}


@lru_cache(maxsize=1)
def load_strategy_presets() -> Dict[str, Dict[str, float]]:
    """JSON 파일에서 전략 프리셋 로드 (캐시됨)."""
    if not os.path.exists(PRESET_CONFIG_PATH):
        logger.warning("전략 프리셋 파일이 없습니다: %s", PRESET_CONFIG_PATH)
        return {}

    try:
        with open(PRESET_CONFIG_PATH, "r", encoding="utf-8") as fp:
            raw = json.load(fp)
    except Exception as exc:
        logger.error("전략 프리셋 파일 로드 실패: %s", exc)
        return {}

    presets: Dict[str, Dict[str, float]] = {}
    for key, meta in raw.items():
        params = meta.get("params")
        if params:
            presets[key] = params
    return presets


def list_preset_names() -> List[str]:
    presets = load_strategy_presets()
    return sorted(presets.keys())


def get_preset(name: str | None) -> Dict[str, float]:
    """해당 이름의 프리셋 파라미터를 반환 (없으면 빈 dict)."""
    if not name:
        return {}
    presets = load_strategy_presets()
    return presets.get(name, {})


def get_param_defaults() -> Dict[str, float]:
    """기본 파라미터 dict 복사본을 반환."""
    return dict(STRATEGY_PARAM_DEFAULTS)


def resolve_preset_for_regime(regime: str, fallback: str = "balanced_champion") -> Tuple[str, Dict[str, float]]:
    """
    시장 국면(Regime)에 따라 프리셋을 선택한다.

    Args:
        regime: MarketRegimeDetector가 반환하는 문자열 (예: 'BULL', 'BEAR', 'SIDEWAYS')
        fallback: 매핑에 없거나 프리셋이 없을 경우 사용할 기본 프리셋 이름

    Returns:
        (preset_name, params) 튜플
    """
    preset_name = DEFAULT_REGIME_PRESETS.get(regime, fallback)
    preset = get_preset(preset_name)

    if not preset:
        logger.warning("Regime '%s'에 대한 프리셋 '%s'을 찾지 못했습니다. 기본값을 사용합니다.", regime, preset_name)
        return fallback, get_preset(fallback) or get_param_defaults()

    return preset_name, preset


def apply_preset_to_config(config, preset_params: Dict[str, float], persist_to_db: bool = False) -> None:
    """
    ConfigManager에 프리셋 값을 주입한다. (persist_to_db=False면 메모리 캐시만 업데이트)
    """
    if not config or not preset_params:
        return

    for param_key, config_key in CONFIG_KEY_MAP.items():
        if param_key in preset_params:
            config.set(config_key, preset_params[param_key], persist_to_db=persist_to_db)

