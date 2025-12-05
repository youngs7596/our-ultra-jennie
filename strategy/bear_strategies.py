from __future__ import annotations

from typing import Optional

import pandas as pd

from shared import strategy
from shared.market_regime import MarketRegimeDetector


def evaluate_snipe_dip(
    daily_prices_df: pd.DataFrame,
    last_close_price: float,
    config,
    context: dict,
) -> Optional[dict]:
    """과매도 스나이핑 조건 검사."""
    if daily_prices_df is None or daily_prices_df.empty:
        return None

    rsi = strategy.calculate_rsi(daily_prices_df)
    if not rsi or rsi >= 30:
        return None

    lower_band = strategy.calculate_bollinger_bands(daily_prices_df)
    if lower_band is None or last_close_price > lower_band:
        return None

    if "VOLUME" not in daily_prices_df.columns:
        return None
    vol_series = pd.to_numeric(daily_prices_df["VOLUME"], errors="coerce").fillna(0)
    if len(vol_series) < 20:
        return None
    recent_vol = vol_series.iloc[-1]
    vol_ma20 = vol_series.iloc[-20:].mean()
    volume_multiplier = context.get("volume_multiplier", 1.5)
    if vol_ma20 <= 0 or recent_vol < vol_ma20 * volume_multiplier:
        return None

    atr = strategy.calculate_atr(daily_prices_df, period=context.get("atr_period", 14))
    if not atr:
        return None

    key_metrics = _build_risk_key_metrics(last_close_price, atr, context, "BEAR_SNIPE_DIP")
    key_metrics.update(
        {
            "rsi": float(rsi),
            "bollinger_lower": float(lower_band),
            "recent_volume": float(recent_vol),
            "volume_ma20": float(vol_ma20),
        }
    )
    return {"signal": "BEAR_SNIPE_DIP", "key_metrics": key_metrics}


def evaluate_momentum_breakout(
    daily_prices_df: pd.DataFrame,
    last_close_price: float,
    kospi_prices_df: pd.DataFrame,
    config,
    context: dict,
) -> Optional[dict]:
    """상대강도 돌파 전략 검사."""
    if daily_prices_df is None or len(daily_prices_df) < 20:
        return None

    series = pd.to_numeric(daily_prices_df["CLOSE_PRICE"], errors="coerce").fillna(0)
    ma20 = series.rolling(window=20).mean().iloc[-1]
    if pd.isna(ma20) or last_close_price <= ma20:
        return None

    relative_strength = strategy.calculate_relative_strength(daily_prices_df, kospi_prices_df, period=5)
    if not relative_strength or relative_strength <= 0:
        return None

    if "VOLUME" not in daily_prices_df.columns:
        return None
    vol_series = pd.to_numeric(daily_prices_df["VOLUME"], errors="coerce").fillna(0)
    if len(vol_series) < 20:
        return None
    volume_multiplier = context.get("volume_multiplier", 1.5)
    recent_vol = vol_series.iloc[-1]
    vol_ma20 = vol_series.iloc[-20:].mean()
    if vol_ma20 <= 0 or recent_vol < vol_ma20 * (volume_multiplier - 0.2):
        return None

    atr = strategy.calculate_atr(daily_prices_df, period=context.get("atr_period", 14))
    if not atr:
        return None

    key_metrics = _build_risk_key_metrics(last_close_price, atr, context, "BEAR_MOMENTUM_BREAKOUT")
    key_metrics.update(
        {
            "ma20": float(ma20),
            "relative_strength_pct": float(relative_strength),
            "recent_volume": float(recent_vol),
            "volume_ma20": float(vol_ma20),
        }
    )
    return {"signal": "BEAR_MOMENTUM_BREAKOUT", "key_metrics": key_metrics}


def _build_risk_key_metrics(current_price: float, atr: float, context: dict, strategy_name: str) -> dict:
    atr_mult = context.get("stop_loss_atr_mult", 2.0)
    tp_pct = context.get("tp_pct", 0.03)
    partial_ratio = context.get("partial_ratio", 0.5)
    position_ratio = context.get("position_ratio", 0.2)

    stop_loss = max(0.0, current_price - (atr * atr_mult))
    first_tp = current_price * (1 + tp_pct)

    return {
        "strategy": strategy_name,
        "atr": float(atr),
        "position_ratio": float(position_ratio),
        "stop_loss": float(stop_loss),
        "first_take_profit": float(first_tp),
        "partial_close_ratio": float(partial_ratio),
        "trailing_activation": float(tp_pct),
    }


