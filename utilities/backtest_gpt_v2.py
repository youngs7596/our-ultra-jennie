#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backtest_gpt_v2.py
---------------

Buy/Sell Executor · Price Monitor · Scout Job 동작을 최대한 모사하는
경량 백테스트 러너.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional, Tuple
import math
import random

import pandas as pd
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(PROJECT_ROOT)

from shared import auth, database, strategy  # noqa: E402
from shared.config import ConfigManager  # noqa: E402
from shared.factor_scoring import FactorScorer  # noqa: E402
from shared.market_regime import (  # noqa: E402
    MarketRegimeDetector,
    StrategySelector,
)
from shared.strategy_presets import (  # noqa: E402
    get_param_defaults as get_strategy_defaults,
    get_preset as get_strategy_preset,
    list_preset_names,
)

logger = logging.getLogger(__name__)

SMART_UNIVERSE_PATH = os.path.join(PROJECT_ROOT, "smart_universe.json")
BLUECHIP_FALLBACK = [
    {"code": "005930", "name": "삼성전자"},
    {"code": "000660", "name": "SK하이닉스"},
    {"code": "373220", "name": "LG에너지솔루션"},
    {"code": "051910", "name": "LG화학"},
    {"code": "035420", "name": "NAVER"},
    {"code": "035720", "name": "카카오"},
    {"code": "005380", "name": "현대차"},
    {"code": "000270", "name": "기아"},
    {"code": "006400", "name": "삼성SDI"},
    {"code": "207940", "name": "삼성바이오로직스"},
    {"code": "028260", "name": "삼성물산"},
    {"code": "105560", "name": "KB금융"},
    {"code": "055550", "name": "신한지주"},
    {"code": "010950", "name": "S-Oil"},
    {"code": "014680", "name": "한솔케미칼"},
    {"code": "090430", "name": "아모레퍼시픽"},
    {"code": "024110", "name": "기업은행"},
    {"code": "018260", "name": "삼성에스디에스"},
    {"code": "009540", "name": "한국조선해양"},
    {"code": "086790", "name": "하나금융지주"},
]

TOP_TRADING_LIMIT = 200
TOP_TRADING_LOOKBACK_DAYS = 30


def fetch_top_trading_value_codes(connection, limit: int = TOP_TRADING_LIMIT, lookback_days: int = TOP_TRADING_LOOKBACK_DAYS) -> List[str]:
    # [v1.0] MariaDB 호환 쿼리
    query = """
        SELECT STOCK_CODE, AVG(CLOSE_PRICE * VOLUME) AS AVG_AMT
        FROM STOCK_DAILY_PRICES_3Y
        WHERE PRICE_DATE >= CURDATE() - INTERVAL %s DAY
        GROUP BY STOCK_CODE
        ORDER BY AVG_AMT DESC
        LIMIT %s
    """
    cursor = connection.cursor()
    try:
        cursor.execute(query, (lookback_days, limit))
        rows = cursor.fetchall()
        # DictCursor일 경우와 일반 cursor 모두 처리
        if rows and isinstance(rows[0], dict):
            return [row['STOCK_CODE'] for row in rows if row['STOCK_CODE'] != "0001"]
        return [row[0] for row in rows if row[0] != "0001"]
    except Exception as exc:
        logger.warning(f"거래대금 상위 종목 로드 실패: {exc}")
        return []
    finally:
        cursor.close()

INTRADAY_INTERVAL_MINUTES = 20
MARKET_OPEN_MINUTES = 9 * 60  # 09:00
MARKET_CLOSE_MINUTES = 15 * 60 + 20  # 15:20
SLOT_COUNT = (MARKET_CLOSE_MINUTES - MARKET_OPEN_MINUTES) // INTRADAY_INTERVAL_MINUTES + 1


# ---------------------------------------------------------------------------
# 유틸 함수
# ---------------------------------------------------------------------------


def load_price_series(connection, stock_code: str) -> pd.DataFrame:
    # [v1.0] MariaDB 호환 쿼리
    query = """
        SELECT PRICE_DATE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, CLOSE_PRICE, VOLUME
        FROM STOCK_DAILY_PRICES_3Y
        WHERE STOCK_CODE = %s
        ORDER BY PRICE_DATE ASC
    """
    cursor = connection.cursor()
    try:
        cursor.execute(query, (stock_code,))
        rows = cursor.fetchall()
    finally:
        cursor.close()

    if not rows:
        return pd.DataFrame()

    # DictCursor일 경우 처리
    if rows and isinstance(rows[0], dict):
        df = pd.DataFrame(rows)
    else:
        df = pd.DataFrame(rows, columns=["PRICE_DATE", "OPEN_PRICE", "HIGH_PRICE", "LOW_PRICE", "CLOSE_PRICE", "VOLUME"])
    
    df["PRICE_DATE"] = pd.to_datetime(df["PRICE_DATE"])
    df.set_index("PRICE_DATE", inplace=True)
    return df


def prepare_indicators(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    delta = df["CLOSE_PRICE"].diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    roll_up = up.ewm(alpha=1 / 14, min_periods=14).mean()
    roll_down = down.ewm(alpha=1 / 14, min_periods=14).mean()
    rs = roll_up / roll_down.replace(0, pd.NA)
    df["RSI"] = 100 - (100 / (1 + rs))

    df["MA_5"] = df["CLOSE_PRICE"].rolling(5).mean()
    df["MA_20"] = df["CLOSE_PRICE"].rolling(20).mean()
    std20 = df["CLOSE_PRICE"].rolling(20).std()
    df["BB_LOWER"] = df["MA_20"] - (std20 * 2)
    df["RES_20"] = df["HIGH_PRICE"].rolling(20).max().shift(1)

    prev_close = df["CLOSE_PRICE"].shift(1)
    tr = pd.concat(
        [
            df["HIGH_PRICE"] - df["LOW_PRICE"],
            (df["HIGH_PRICE"] - prev_close).abs(),
            (df["LOW_PRICE"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    df["ATR"] = tr.ewm(alpha=1 / 14, min_periods=14).mean()
    return df


def get_row_at_or_before(df: pd.DataFrame, date: datetime) -> Optional[pd.Series]:
    if df.empty:
        return None
    if date in df.index:
        row = df.loc[date]
        return row if isinstance(row, pd.Series) else row.iloc[-1]
    subset = df.loc[:date]
    if subset.empty:
        return None
    return subset.iloc[-1]


def piecewise_linear(points: List[Tuple[float, float]], t: float) -> float:
    """Control points are sorted by t. Returns interpolated value."""
    if not points:
        return 0.0
    points = sorted(points, key=lambda x: x[0])
    if t <= points[0][0]:
        return points[0][1]
    if t >= points[-1][0]:
        return points[-1][1]
    for idx in range(len(points) - 1):
        t0, v0 = points[idx]
        t1, v1 = points[idx + 1]
        if t0 <= t <= t1:
            if t1 == t0:
                return v1
            ratio = (t - t0) / (t1 - t0)
            return v0 + (v1 - v0) * ratio
    return points[-1][1]


# ---------------------------------------------------------------------------
# 데이터 모델
# ---------------------------------------------------------------------------


@dataclass
class Candidate:
    code: str
    price: float
    signal: str
    score: float
    metadata: Dict[str, float] = field(default_factory=dict)
    factor_score: float = 0.0
    llm_score: float = 0.0
    llm_reason: str = ""


@dataclass
class Position:
    code: str
    quantity: int
    avg_price: float
    entry_date: datetime
    stop_loss_price: float
    target_price: float
    atr: float
    sector: str
    original_quantity: int
    sold_ratio: float = 0.0
    high_price: float = 0.0


@dataclass
class SellAction:
    code: str
    quantity: int
    price: float
    reason: str
    partial: bool


# ---------------------------------------------------------------------------
# 스캐너
# ---------------------------------------------------------------------------


class ScannerLite:
    def __init__(
        self,
        price_cache: Dict[str, pd.DataFrame],
        regime_detector: MarketRegimeDetector,
        strategy_selector: StrategySelector,
        factor_scorer: FactorScorer,
        stock_names: Dict[str, str],
        rsi_threshold: int,
        breakout_buffer_pct: float,
        bb_buffer_pct: float,
        top_n: int,
        watchlist_cache: Dict[str, Dict] = None,  # [v1.0] LLM 점수 조회용
    ):
        self.price_cache = price_cache
        self.regime_detector = regime_detector
        self.strategy_selector = strategy_selector
        self.factor_scorer = factor_scorer
        self.stock_names = stock_names
        self.rsi_threshold = rsi_threshold
        self.breakout_buffer_pct = breakout_buffer_pct
        self.bb_buffer_pct = bb_buffer_pct
        self.top_n = top_n
        self.watchlist_cache = watchlist_cache or {}  # [v1.0] Watchlist 캐시

    def detect_regime(self, kospi_slice: pd.DataFrame) -> Tuple[str, List[str]]:
        close_df = kospi_slice[["CLOSE_PRICE"]].rename(columns={"CLOSE_PRICE": "CLOSE_PRICE"})
        current_price = float(close_df["CLOSE_PRICE"].iloc[-1])
        regime, _ = self.regime_detector.detect_regime(close_df, current_price, quiet=True)
        strategies = self.strategy_selector.select_strategies(regime)
        return regime, strategies

    def generate_candidates(
        self,
        current_date: datetime,
        regime: str,
        strategies: List[str],
        kospi_slice: pd.DataFrame,
        price_lookup: Callable[[str], float],
    ) -> List[Candidate]:
        candidates: List[Candidate] = []
        # 재현성을 위해 정렬된 순서로 순회
        for code in sorted(self.price_cache.keys()):
            df = self.price_cache[code]
            if code == "0001":
                continue

            df_window = df.loc[:current_date].tail(220)
            if df_window.empty or current_date not in df_window.index:
                continue
            row = df_window.loc[current_date]
            # [Fix Look-Ahead Bias]
            # 장중(intraday) 의사결정 시에는 '어제' 기준의 지표나 레벨을 사용해야 함.
            # 오늘자(row)의 RSI, MA 등은 오늘 종가(Close)가 포함된 값이므로 장중에는 알 수 없음.

            prev_idx = df_window.index.get_loc(current_date) - 1
            if prev_idx < 0:
                continue
            prev_row = df_window.iloc[prev_idx]

            # 어제 기준 지표들
            prev_rsi = float(prev_row.get("RSI", 0)) if not pd.isna(prev_row.get("RSI")) else 0.0
            prev_bb_lower = float(prev_row.get("BB_LOWER", 0)) if not pd.isna(prev_row.get("BB_LOWER")) else 0.0
            prev_ma5 = float(prev_row.get("MA_5", 0)) if not pd.isna(prev_row.get("MA_5")) else 0.0
            prev_ma20 = float(prev_row.get("MA_20", 0)) if not pd.isna(prev_row.get("MA_20")) else 0.0

            # 그 전날(2일 전) 데이터 (골든크로스 확인용)
            prev2_idx = prev_idx - 1
            prev2_ma5 = prev2_ma20 = 0.0
            if prev2_idx >= 0:
                prev2_row = df_window.iloc[prev2_idx]
                prev2_ma5 = float(prev2_row.get("MA_5", 0)) if not pd.isna(prev2_row.get("MA_5")) else 0.0
                prev2_ma20 = float(prev2_row.get("MA_20", 0)) if not pd.isna(prev2_row.get("MA_20")) else 0.0

            price = float(price_lookup(code))
            if price <= 0:
                continue

            score = 0.0
            signal = ""
            metadata: Dict[str, float] = {}

            if StrategySelector.STRATEGY_MEAN_REVERSION in strategies:
                # BB Lower Touch: 어제 기준 하단 밴드 사용
                if prev_bb_lower > 0 and price <= prev_bb_lower * (1 + self.bb_buffer_pct / 100):
                    signal = "BB_TOUCH"
                    score = 60 - prev_rsi / 2
                    metadata = {"bb_lower": prev_bb_lower, "prev_rsi": prev_rsi}

                # RSI Oversold: 어제 RSI가 과매도권이었는지 확인 (오늘 장중 RSI는 알 수 없음)
                # 또는 "어제 RSI < 30" 상태에서 진입
                threshold = self.rsi_threshold if regime != MarketRegimeDetector.REGIME_BULL else self.rsi_threshold + 10
                if prev_rsi > 0 and prev_rsi <= threshold:
                    if score == 0 or (65 - prev_rsi) > score:
                        signal = "RSI_OVERSOLD"
                        score = 65 - prev_rsi
                        metadata = {"prev_rsi": prev_rsi}

            if StrategySelector.STRATEGY_TREND_FOLLOWING in strategies and signal == "":
                # Golden Cross: 어제 MA5가 MA20을 상향 돌파했는지 확인 (오늘 장중 크로스는 종가 확정 전이라 불확실)
                # 혹은 "어제는 아래였는데 오늘은 위" -> 이건 오늘 종가 필요하므로 Bias.
                # 따라서 "어제 골든크로스가 발생했다"를 신호로 잡거나,
                # "어제 MA5 < MA20" 이고 "현재가 > 어제 MA20" (돌파 시도) 등으로 잡아야 함.
                # 여기서는 안전하게 "어제 골든크로스 발생" 종목을 오늘 시초/장중에 잡는 것으로 가정.

                if prev_ma5 > 0 and prev_ma20 > 0 and prev2_ma5 > 0:
                    if prev_ma5 > prev_ma20 and prev2_ma5 <= prev2_ma20:
                        signal = "GOLDEN_CROSS"
                        score = 55 + min(10, price / (prev_row["CLOSE_PRICE"] + 1e-6) * 5)
                        metadata = {"prev_ma5": prev_ma5, "prev_ma20": prev_ma20}

                # Resistance Breakout: RES_20은 이미 shift(1) 되어 있어서 "어제까지의 고점"임.
                # 따라서 row['RES_20']을 써도 됨 (prepare_indicators에서 shift(1) 함).
                # 하지만 row는 오늘 날짜 행이므로, 오늘 날짜 행의 RES_20 컬럼 값은 "어제까지의 고점"이 맞음.
                # 확인: df['RES_20'] = df['HIGH_PRICE'].rolling(20).max().shift(1)
                # 맞음. row['RES_20']은 안전함.
                res_20 = row.get("RES_20")
                if not pd.isna(res_20) and price >= res_20 * (1 + self.breakout_buffer_pct / 100):
                    signal = "RES_BREAK"
                    score = max(score, 70)
                    metadata = {"res_20": res_20, "price": price}

            if signal:
                factor_score = self._compute_factor_score(df_window, kospi_slice, regime)
                llm_score = self._estimate_llm_score(code, factor_score, score, signal)
                name = self.stock_names.get(code, code)
                metadata = {**metadata, "name": name}
                candidates.append(
                    Candidate(
                        code=code,
                        price=price,
                        signal=signal,
                        score=score,
                        metadata=metadata,
                        factor_score=factor_score,
                        llm_score=llm_score,
                        llm_reason=f"{name} | {signal} | 팩터 {factor_score:.1f}",
                    )
                )

        candidates.sort(key=lambda c: (c.llm_score, c.factor_score), reverse=True)
        return candidates[: self.top_n]

    def _compute_factor_score(
        self,
        stock_window: pd.DataFrame,
        kospi_slice: pd.DataFrame,
        regime: str,
    ) -> float:
        try:
            kospi_window = kospi_slice.tail(len(stock_window))
            momentum, _ = self.factor_scorer.calculate_momentum_score(stock_window, kospi_window)
            quality, _ = self.factor_scorer.calculate_quality_score(roe=None, sales_growth=None, eps_growth=None, daily_prices_df=stock_window)
            value, _ = self.factor_scorer.calculate_value_score(pbr=None, per=None)
            technical, _ = self.factor_scorer.calculate_technical_score(stock_window)
            final_score, _ = self.factor_scorer.calculate_final_score(momentum, quality, value, technical, regime)
            return final_score / 10.0
        except Exception as exc:
            logger.debug(f"팩터 점수 계산 실패: {exc}")
            return 50.0

    def _estimate_llm_score(
        self, 
        code: str, 
        factor_score: float, 
        raw_score: float, 
        signal: str
    ) -> float:
        """
        [v1.0 개선] LLM 점수 추정 - DB Watchlist 우선 조회
        
        실제 Scout Pipeline 결과가 DB에 있으면 해당 점수를 사용하고,
        없으면 팩터 점수 + 신호 보너스 기반으로 추정합니다.
        
        추정 공식 (회귀 분석 기반):
        - 기본점수: 50점 (중립)
        - 팩터 점수 기여: factor_score × 0.35 (35% 반영)
        - 신호 점수 기여: raw_score × 0.08
        - 신호 유형 보너스: RES_BREAK > GOLDEN_CROSS > RSI_OVERSOLD > BB_TOUCH
        - 랜덤 노이즈: ±3점 (실제 LLM 판단의 변동성 모사)
        """
        # 1. DB Watchlist에서 실제 LLM 점수 조회
        watchlist_info = self.watchlist_cache.get(code, {})
        db_llm_score = watchlist_info.get('llm_score')
        
        if db_llm_score is not None and db_llm_score > 0:
            # 실제 Scout 결과 사용 (시간에 따른 감쇠 없이 그대로 사용)
            return float(db_llm_score)
        
        # 2. DB에 없으면 추정 (회귀 분석 기반 개선된 공식)
        # 신호 유형별 보너스 (실제 Scout 결과와 비교하여 조정)
        signal_bonus = {
            "RES_BREAK": 8,       # 저항선 돌파: 강력한 모멘텀 신호
            "GOLDEN_CROSS": 6,    # 골든크로스: 중기 추세 전환
            "TREND_UP": 4,        # 상승 추세 확인
            "RSI_OVERSOLD": 3,    # 과매도 반등: 단기 기회
            "BB_TOUCH": 2,        # 볼린저 밴드 터치: 약한 신호
        }.get(signal, 0)
        
        # 기본 점수 계산
        base_score = 50.0  # 중립 기준
        factor_contribution = factor_score * 0.35  # 팩터 점수 35% 반영
        signal_contribution = raw_score * 0.08     # 신호 강도 8% 반영
        
        # 시장 국면별 조정 (watchlist_info에 regime 정보가 있으면 사용)
        regime_adjustment = 0.0
        if watchlist_info.get('market_regime') == 'BULL':
            regime_adjustment = 3.0  # 강세장에서 약간의 가산점
        elif watchlist_info.get('market_regime') == 'BEAR':
            regime_adjustment = -5.0  # 약세장에서 보수적 평가
        
        # 랜덤 노이즈 추가 (실제 LLM 판단의 변동성 모사, 재현성을 위해 code 기반)
        noise_seed = hash(code) % 1000 / 1000.0  # 0~1 사이 값
        noise = (noise_seed - 0.5) * 6  # -3 ~ +3점 범위
        
        estimated_score = (
            base_score 
            + factor_contribution 
            + signal_contribution 
            + signal_bonus 
            + regime_adjustment 
            + noise
        )
        
        return max(0.0, min(99.0, estimated_score))


# ---------------------------------------------------------------------------
# 포트폴리오 엔진
# ---------------------------------------------------------------------------


class PortfolioEngine:
    def __init__(
        self,
        initial_capital: float,
        max_position_pct: float,
        max_positions: int,
        target_profit_pct: float,
        stop_loss_pct: float,
        stop_loss_atr_mult: float,
        max_hold_days: int,
    ):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.positions: Dict[str, Position] = {}
        self.max_position_pct = max_position_pct
        self.max_positions = max_positions
        self.target_profit_pct = target_profit_pct
        self.base_stop_loss_pct = stop_loss_pct
        self.stop_loss_atr_mult = stop_loss_atr_mult
        self.max_hold_days = max_hold_days
        self.trade_log: List[Dict] = []

    def total_value(
        self,
        date: datetime,
        price_cache: Dict[str, pd.DataFrame],
        price_lookup: Optional[Callable[[str], float]] = None,
    ) -> float:
        equity = self.cash
        for pos in self.positions.values():
            row = get_row_at_or_before(price_cache[pos.code], date)
            if row is None:
                continue
            if price_lookup:
                price = float(price_lookup(pos.code))
            else:
                price = float(row["CLOSE_PRICE"])
            equity += price * pos.quantity
        return equity

    def snapshot(
        self,
        date: datetime,
        price_cache: Dict[str, pd.DataFrame],
        price_lookup: Optional[Callable[[str], float]] = None,
    ) -> Tuple[float, Dict[str, float], Dict[str, float]]:
        equity = self.cash
        exposures: Dict[str, float] = {}
        holdings: Dict[str, float] = {}
        for pos in self.positions.values():
            row = get_row_at_or_before(price_cache[pos.code], date)
            if row is None:
                continue
            if price_lookup:
                price = float(price_lookup(pos.code))
            else:
                price = float(row["CLOSE_PRICE"])
            value = price * pos.quantity
            exposures[pos.sector] = exposures.get(pos.sector, 0.0) + value
            holdings[pos.code] = value
            equity += value
        return equity, exposures, holdings

    def can_add_position(self) -> bool:
        return len(self.positions) < self.max_positions

    # 수수료율 상수 (한국투자증권 OpenAPI 기준)
    FEE_BUY = 0.0000841   # 0.00841%
    FEE_SELL = 0.0005841  # 0.05841%

    def execute_buy(
        self,
        candidate: Candidate,
        qty: int,
        trade_date: datetime,
        slot_timestamp: datetime,
        atr: float,
        sector: str,
        risk_setting: Dict,
    ) -> bool:
        price = candidate.price
        cost = qty * price
        fee = cost * self.FEE_BUY
        total_cost = cost + fee

        if qty <= 0 or total_cost > self.cash:
            return False

        stop_loss_pct = abs(risk_setting.get("stop_loss_pct", -self.base_stop_loss_pct))
        target_profit_pct = risk_setting.get("target_profit_pct", self.target_profit_pct)

        stop_price = price * (1 - stop_loss_pct)
        target_price = price * (1 + target_profit_pct)
        high_price = price

        self.positions[candidate.code] = Position(
            code=candidate.code,
            quantity=qty,
            avg_price=price,
            entry_date=slot_timestamp,
            stop_loss_price=stop_price,
            target_price=target_price,
            atr=atr,
            sector=sector,
            original_quantity=qty,
            high_price=high_price,
        )
        self.cash -= total_cost
        self.trade_log.append(
            {
                "type": "BUY",
                "code": candidate.code,
                "price": price,
                "quantity": qty,
                "reason": candidate.signal,
                "date": slot_timestamp,
                "fee": fee,
            }
        )
        logger.info(
            f"💰 [BUY_COMMIT] {candidate.code} | {slot_timestamp:%Y-%m-%d %H:%M} | "
            f"{qty}주 @ {price:,.0f}원 | 수수료 {fee:,.0f}원 | Stop {stop_loss_pct * 100:.2f}% | "
            f"Target {target_profit_pct * 100:.2f}% | Cash {self.cash:,.0f}원"
        )
        return True

    def process_slot(
        self,
        slot_timestamp: datetime,
        trade_date: datetime,
        price_lookup: Callable[[str], float],
        price_cache: Dict[str, pd.DataFrame],
        risk_setting: Dict,
        rsi_thresholds: Tuple[float, float, float],
    ) -> List[SellAction]:
        actions: List[SellAction] = []
        for code, pos in list(self.positions.items()):
            row = get_row_at_or_before(price_cache[code], trade_date)
            if row is None:
                continue
            price = float(price_lookup(code))
            atr = float(row.get("ATR", pos.atr)) if not pd.isna(row.get("ATR")) else pos.atr
            if not pd.isna(atr):
                trailing = price - atr * self.stop_loss_atr_mult
                if trailing > pos.stop_loss_price:
                    pos.stop_loss_price = trailing
            pos.high_price = max(pos.high_price, price)

            # 수익률 계산 시 수수료 고려 (매수 수수료는 이미 cash에서 차감됨, 매도 수수료 예상치 반영)
            # 보수적인 판단을 위해 매도 수수료를 뺀 금액으로 수익률 계산
            estimated_proceeds = (price * pos.quantity) * (1 - self.FEE_SELL)
            cost_basis = pos.avg_price * pos.quantity
            profit = (estimated_proceeds - cost_basis) / cost_basis
            
            reason: Optional[str] = None
            quantity = pos.quantity
            partial = False

            if price <= pos.stop_loss_price:
                reason = "ATR_STOP"
            elif profit <= risk_setting.get("stop_loss_pct", -self.base_stop_loss_pct):
                reason = "DYNAMIC_STOP"
            elif profit >= risk_setting.get("target_profit_pct", self.target_profit_pct):
                reason = "TARGET_PROFIT"
            else:
                rsi = row.get("RSI")
                if not pd.isna(rsi) and pos.sold_ratio < 0.99:
                    increment = 0.0
                    reason_map = None
                    if rsi >= rsi_thresholds[2] and pos.sold_ratio < 0.8:
                        increment = 0.2
                        reason_map = "RSI_TAKE_PROFIT_80"
                    elif rsi >= rsi_thresholds[1] and pos.sold_ratio < 0.5:
                        increment = 0.5
                        reason_map = "RSI_TAKE_PROFIT_75"
                    elif rsi >= rsi_thresholds[0] and pos.sold_ratio < 0.3:
                        increment = 0.3
                        reason_map = "RSI_TAKE_PROFIT_70"

                    if increment > 0 and reason_map:
                        target_ratio = min(1.0, pos.sold_ratio + increment)
                        sell_ratio = target_ratio - pos.sold_ratio
                        sell_qty = max(1, int(pos.original_quantity * sell_ratio))
                        sell_qty = min(sell_qty, pos.quantity)
                        if sell_qty > 0:
                            proceeds = sell_qty * price
                            fee = proceeds * self.FEE_SELL
                            net_proceeds = proceeds - fee
                            
                            self.cash += net_proceeds
                            pos.quantity -= sell_qty
                            pos.sold_ratio = target_ratio
                            partial = True
                            quantity = sell_qty
                            reason = reason_map
                            actions.append(SellAction(code, sell_qty, price, reason, True))
                            self.trade_log.append(
                                {
                                    "type": "SELL",
                                    "code": code,
                                    "price": price,
                                    "quantity": sell_qty,
                                    "reason": reason,
                                    "date": slot_timestamp,
                                    "fee": fee,
                                }
                            )
                            logger.info(
                                f"✂️ [SELL_PARTIAL] {code} | {slot_timestamp:%Y-%m-%d %H:%M} | "
                                f"{sell_qty}주 @ {price:,.0f}원 | 수수료 {fee:,.0f}원 | 이유 {reason} | 누적 매도 {target_ratio * 100:.0f}%"
                            )
                            if pos.quantity <= 0:
                                del self.positions[code]
                        continue

                # [v1.0 추가] Death Cross 체크 (MA5 < MA20 하향 크로스)
                ma5 = row.get("MA_5")
                ma20 = row.get("MA_20")
                if not pd.isna(ma5) and not pd.isna(ma20):
                    # Death Cross: MA5가 MA20 아래로 떨어지면 매도 신호
                    # 추가 조건: 이전에 MA5 > MA20 이었어야 함 (크로스 발생)
                    if ma5 < ma20 and pos.high_price > pos.avg_price * 1.02:
                        # 고점 대비 2% 이상 올랐다가 Death Cross 발생 시에만
                        reason = "DEATH_CROSS"
                
                holding_days = (slot_timestamp - pos.entry_date).days
                if holding_days >= self.max_hold_days:
                    reason = "TIME_EXIT"

            if reason:
                proceeds = quantity * price
                fee = proceeds * self.FEE_SELL
                net_proceeds = proceeds - fee
                
                self.cash += net_proceeds
                actions.append(SellAction(code, quantity, price, reason, partial))
                self.trade_log.append(
                    {
                        "type": "SELL",
                        "code": code,
                        "price": price,
                        "quantity": quantity,
                        "reason": reason,
                        "date": slot_timestamp,
                        "fee": fee,
                    }
                )
                del self.positions[code]
                logger.info(
                    f"💸 [SELL_EXIT] {code} | {slot_timestamp:%Y-%m-%d %H:%M} | "
                    f"{quantity}주 @ {price:,.0f}원 | 수수료 {fee:,.0f}원 | 이유 {reason} | 손익 {profit * 100:.2f}%"
                )

        return actions


# ---------------------------------------------------------------------------
# 메인 백테스트 드라이버
# ---------------------------------------------------------------------------


class BacktestGPT:
    def __init__(self, connection, args):
        self.connection = connection
        self.args = args
        self.config = ConfigManager(db_conn=connection)
        self.regime_detector = MarketRegimeDetector()
        self.strategy_selector = StrategySelector()
        self.factor_scorer = FactorScorer()
        self.price_cache: Dict[str, pd.DataFrame] = {}
        self.calendar: List[datetime] = []
        self.scanner: Optional[ScannerLite] = None
        self.portfolio: Optional[PortfolioEngine] = None
        self.stock_metadata: Dict[str, Dict[str, str]] = {}
        self.trade_log: List[Dict] = []
        self.watchlist_cache: Dict[str, Dict] = {}
        self.rsi_sell_thresholds = (
            self.args.sell_rsi_1,
            self.args.sell_rsi_2,
            self.args.sell_rsi_3,
        )
        self.max_buys_per_day = self.args.max_buys_per_day
        self.max_holdings = self.args.max_holdings
        self.llm_threshold = self.args.llm_threshold
        self.cash_keep_pct = self.args.cash_keep_pct
        self.max_sector_pct = self.args.max_sector_pct
        self.max_stock_pct = self.args.max_stock_pct
        self.slot_offsets = self._build_slot_offsets()
        self.intraday_price_cache: Dict[Tuple[str, pd.Timestamp], List[float]] = {}

    def _load_universe(self) -> List[str]:
        watchlist = database.get_active_watchlist(self.connection)
        if watchlist:
            self.watchlist_cache = watchlist
            codes = [code for code in watchlist.keys() if code != "0001"]
            limit = self.args.universe_limit or len(codes)
            return codes[:limit]

        logger.warning("Watchlist가 비어 있습니다. Top 200 거래대금 기반으로 대체합니다.")
        top_codes = fetch_top_trading_value_codes(self.connection, limit=self.args.universe_limit or TOP_TRADING_LIMIT)
        if top_codes:
            self.watchlist_cache = {code: {"name": self.stock_metadata.get(code, {}).get("name", code)} for code in top_codes}
            return top_codes

        logger.warning("거래대금 기반 추출도 실패했습니다. 블루칩 fallback을 사용합니다.")
        fallback_codes = self._load_fallback_universe(self.args.universe_limit)
        self.watchlist_cache = {code: {"name": self.stock_metadata.get(code, {}).get("name", code)} for code in fallback_codes}
        return fallback_codes

    def _prefetch_data(self, codes: List[str]) -> None:
        kospi_df = load_price_series(self.connection, "0001")
        self.price_cache["0001"] = prepare_indicators(kospi_df)
        for code in codes:
            df = load_price_series(self.connection, code)
            if df.empty:
                continue
            self.price_cache[code] = prepare_indicators(df)
        self._load_stock_metadata()

    def _build_calendar(self, days: Optional[int]) -> None:
        kospi_df = self.price_cache["0001"]
        start = kospi_df.index.min()
        end = kospi_df.index.max()
        if days:
            start = max(start, end - timedelta(days=days))
        self.calendar = list(kospi_df.loc[start:end].index)

    def _init_components(self) -> None:
        stock_names = {code: meta.get("name", code) for code, meta in self.stock_metadata.items()}
        self.scanner = ScannerLite(
            price_cache=self.price_cache,
            regime_detector=self.regime_detector,
            strategy_selector=self.strategy_selector,
            factor_scorer=self.factor_scorer,
            stock_names=stock_names,
            rsi_threshold=self.args.rsi_buy,
            breakout_buffer_pct=self.args.breakout_buffer_pct,
            bb_buffer_pct=self.args.bb_buffer_pct,
            top_n=self.args.top_n,
            watchlist_cache=self.watchlist_cache,  # [v1.0] LLM 점수 조회용
        )
        self.portfolio = PortfolioEngine(
            initial_capital=self.args.initial_capital,
            max_position_pct=self.args.max_position_allocation / 100.0,
            max_positions=self.args.max_holdings,
            target_profit_pct=self.args.target_profit_pct / 100.0,
            stop_loss_pct=self.args.base_stop_loss_pct / 100.0,
            stop_loss_atr_mult=self.args.stop_loss_atr_mult,
            max_hold_days=self.args.max_hold_days,
        )

    def run(self) -> Dict[str, float]:
        universe = self._load_universe()
        if not universe:
            raise RuntimeError("Universe is empty.")

        logger.info(f"🎯 Universe: {len(universe)}개 종목")
        self._prefetch_data(universe)
        self._build_calendar(self.args.days)
        self._init_components()

        kospi_df = self.price_cache["0001"]
        daily_buy_limit = self.args.max_buys_per_day

        for current_date in self.calendar:
            kospi_slice_full = kospi_df.loc[:current_date]
            if kospi_slice_full.empty:
                continue
            if current_date in kospi_slice_full.index:
                kospi_slice_for_regime = kospi_slice_full.iloc[:-1]
            else:
                kospi_slice_for_regime = kospi_slice_full
            if len(kospi_slice_for_regime) < 30:
                continue

            regime, strategies = self.scanner.detect_regime(kospi_slice_for_regime)
            risk_setting = self.regime_detector.get_dynamic_risk_setting(regime)
            buys_today = 0

            for slot_idx, offset in enumerate(self.slot_offsets):
                slot_timestamp = current_date + offset
                price_lookup = lambda code, d=current_date, i=slot_idx: self._get_intraday_price(code, d, i)

                self.portfolio.process_slot(
                    slot_timestamp,
                    current_date,
                    price_lookup,
                    self.price_cache,
                    risk_setting,
                    self.rsi_sell_thresholds,
                )

                equity = self.portfolio.total_value(current_date, self.price_cache, price_lookup)

                if buys_today >= daily_buy_limit:
                    continue

                candidates = self.scanner.generate_candidates(current_date, regime, strategies, kospi_slice_full, price_lookup)
                for candidate in candidates:
                    if buys_today >= daily_buy_limit:
                        break
                    if self._attempt_buy(candidate, slot_timestamp, price_lookup, current_date, regime, risk_setting):
                        buys_today += 1

            closing_lookup = lambda code, d=current_date: self._get_intraday_price(code, current_date, len(self.slot_offsets) - 1)
            equity = self.portfolio.total_value(current_date, self.price_cache, closing_lookup)
            self.trade_log.append({"type": "EOD", "date": current_date, "equity": equity})
            self._clear_intraday_cache(current_date)

        return self._report()

    def _attempt_buy(
        self,
        candidate: Candidate,
        slot_timestamp: datetime,
        price_lookup: Callable[[str], float],
        trade_date: datetime,
        regime: str,
        risk_setting: Dict,
    ) -> bool:
        logger.info(
            "🛒 [BUY_ATTEMPT] %s | %s | Regime %s | Signal %s | LLM %.1f | Factor %.1f",
            candidate.code,
            slot_timestamp.strftime("%Y-%m-%d %H:%M"),
            regime,
            candidate.signal,
            candidate.llm_score,
            candidate.factor_score,
        )

        def skip(reason: str) -> bool:
            self._log_buy_skip(candidate.code, slot_timestamp, reason)
            return False

        if candidate.llm_score < self.llm_threshold:
            return skip(f"LLM 점수 미달 ({candidate.llm_score:.1f} < {self.llm_threshold})")
        if not self.portfolio.can_add_position():
            return skip("보유 종목 한도 도달")
        if candidate.code in self.portfolio.positions:
            return skip("이미 보유 중")
        if regime == MarketRegimeDetector.REGIME_BEAR:
            return skip("약세장 매수 금지")

        equity, sector_exposure, holdings_value = self.portfolio.snapshot(trade_date, self.price_cache, price_lookup)
        available_cash = self.portfolio.cash
        if equity <= 0 or available_cash <= 0:
            return skip("자본 또는 현금 부족")

        logger.info(
            f"    - 총자산 {equity:,.0f}원 | 현금 {available_cash:,.0f}원 | 보유종목 {len(self.portfolio.positions)}개"
        )

        row = get_row_at_or_before(self.price_cache[candidate.code], trade_date)
        if row is None:
            return skip("가격 데이터 부족")
        atr = float(row.get("ATR", candidate.price * 0.02)) if not pd.isna(row.get("ATR")) else candidate.price * 0.02

        base_allocation = equity * self.portfolio.max_position_pct
        risk_ratio = risk_setting.get("position_size_ratio", 1.0)
        allocation = min(available_cash, base_allocation * risk_ratio)
        stop_loss_pct = abs(risk_setting.get("stop_loss_pct", -self.portfolio.base_stop_loss_pct))
        target_profit_pct = risk_setting.get("target_profit_pct", self.portfolio.target_profit_pct)
        risk_amount = allocation * stop_loss_pct

        logger.info(f"    [Position Sizing] {candidate.code} 수량 계산 시작...")
        logger.info(f"    - 현재가: {candidate.price:,.0f}원 | ATR: {atr:,.0f}원")
        logger.info(
            f"    - 최대 비중 {self.portfolio.max_position_pct * 100:.2f}% | "
            f"Risk Ratio {risk_ratio:.2f} | 목표 배분 {allocation:,.0f}원"
        )
        logger.info(f"    - 예상 위험 금액: {risk_amount:,.0f}원 (손절 {stop_loss_pct * 100:.2f}%)")

        raw_qty = allocation / candidate.price if candidate.price > 0 else 0
        logger.info(f"    - 계산된 수량(연속): {raw_qty:.2f}주")
        qty = int(allocation // candidate.price)
        logger.info(f"    - 라운드 후 수량: {qty}주")
        if qty <= 0:
            return skip("배분된 수량이 0주")

        max_stock_pct = self.args.max_stock_pct if regime != MarketRegimeDetector.REGIME_STRONG_BULL else min(20.0, self.args.max_stock_pct * 1.5)
        new_qty = self._apply_stock_limit(candidate.code, qty, candidate.price, equity, holdings_value, max_stock_pct)
        if new_qty != qty:
            logger.info(f"    - 단일 종목 비중 제한 적용: {qty} → {new_qty}주 (최대 {max_stock_pct:.1f}%)")
        qty = new_qty
        if qty <= 0:
            return skip("단일 종목 비중 제한으로 0주")

        sector = self._get_sector(candidate.code)
        max_sector_pct = self.args.max_sector_pct if regime != MarketRegimeDetector.REGIME_STRONG_BULL else min(50.0, self.args.max_sector_pct + 10.0)
        new_qty = self._apply_sector_limit(sector, qty, candidate.price, equity, sector_exposure, max_sector_pct)
        if new_qty != qty:
            logger.info(f"    - 섹터 비중 제한 적용: {qty} → {new_qty}주 (최대 {max_sector_pct:.1f}%)")
        qty = new_qty
        if qty <= 0:
            return skip(f"섹터 {sector} 비중 제한으로 0주")

        cost = qty * candidate.price
        cash_floor = equity * (self.args.cash_keep_pct / 100.0)
        if cost > available_cash or (available_cash - cost) < cash_floor:
            return skip("현금 유지 비율 위반")

        logger.info(
            f"    - 최종 수량 {qty}주 | 비용 {cost:,.0f}원 | 목표수익 {target_profit_pct * 100:.2f}% | "
            f"손절 {stop_loss_pct * 100:.2f}%"
        )

        executed = self.portfolio.execute_buy(candidate, qty, trade_date, slot_timestamp, atr, sector, risk_setting)
        if executed:
            logger.info(
                f"✅ [BUY_FILLED] {candidate.code} | {slot_timestamp:%Y-%m-%d %H:%M} | "
                f"{qty}주 @ {candidate.price:,.0f}원 | 섹터 {sector} | 남은 현금 {self.portfolio.cash:,.0f}원"
            )
        else:
            self._log_buy_skip(candidate.code, slot_timestamp, "주문 실패")
        return executed

    def _log_buy_skip(self, code: str, slot_timestamp: datetime, reason: str) -> None:
        logger.info("⏭️ [BUY_SKIP] %s | %s | %s", code, slot_timestamp.strftime("%Y-%m-%d %H:%M"), reason)

    def _apply_stock_limit(
        self,
        code: str,
        qty: int,
        price: float,
        equity: float,
        holdings_value: Dict[str, float],
        max_pct: float,
    ) -> int:
        existing_value = holdings_value.get(code, 0.0)
        allowed_value = (max_pct / 100.0) * equity - existing_value
        if allowed_value <= 0:
            return 0
        allowed_qty = int(allowed_value // price)
        if allowed_qty <= 0:
            return 0
        if allowed_qty < qty and allowed_qty < qty * 0.5:
            return 0
        return min(qty, allowed_qty)

    def _apply_sector_limit(
        self,
        sector: str,
        qty: int,
        price: float,
        equity: float,
        exposures: Dict[str, float],
        max_pct: float,
    ) -> int:
        current_value = exposures.get(sector, 0.0)
        allowed_value = (max_pct / 100.0) * equity - current_value
        if allowed_value <= 0:
            return 0
        allowed_qty = int(allowed_value // price)
        if allowed_qty <= 0:
            return 0
        if allowed_qty < qty and allowed_qty < qty * 0.5:
            return 0
        return min(qty, allowed_qty)

    def _load_fallback_universe(self, limit: int) -> List[str]:
        limit = limit or len(BLUECHIP_FALLBACK)
        if os.path.exists(SMART_UNIVERSE_PATH):
            try:
                with open(SMART_UNIVERSE_PATH, "r", encoding="utf-8") as fp:
                    data = json.load(fp)
                return [item["code"] for item in data[:limit]]
            except Exception as exc:
                logger.warning(f"Smart Universe 로드 실패: {exc}")
        return [item["code"] for item in BLUECHIP_FALLBACK[:limit]]

    def _load_stock_metadata(self) -> None:
        self.stock_metadata = {}
        try:
            cursor = self.connection.cursor()
            cursor.execute("SELECT STOCK_CODE, STOCK_NAME, NVL(SECTOR_KOSPI200, '기타') FROM STOCK_MASTER")
            for code, name, sector in cursor.fetchall():
                self.stock_metadata[code] = {"name": name, "sector": sector or "기타"}
            cursor.close()
        except Exception as exc:
            logger.warning(f"STOCK_MASTER 로드 실패: {exc}")

        for code, meta in self.watchlist_cache.items():
            if code not in self.stock_metadata:
                self.stock_metadata[code] = {"name": meta.get("name", code), "sector": "기타"}

    def _get_sector(self, code: str) -> str:
        return self.stock_metadata.get(code, {}).get("sector", "기타")

    def _build_slot_offsets(self) -> List[pd.Timedelta]:
        return [pd.Timedelta(minutes=i * INTRADAY_INTERVAL_MINUTES) for i in range(SLOT_COUNT)]

    def _get_intraday_price(self, code: str, date: pd.Timestamp, slot_idx: int) -> float:
        key = (code, date)
        curve = self.intraday_price_cache.get(key)
        if curve is None:
            curve = self._generate_intraday_curve(code, date)
            self.intraday_price_cache[key] = curve
        if not curve:
            return 0.0
        slot_idx = max(0, min(slot_idx, len(curve) - 1))
        return curve[slot_idx]

    def _generate_intraday_curve(self, code: str, date: pd.Timestamp) -> List[float]:
        df = self.price_cache.get(code)
        slots = len(self.slot_offsets) or 1
        if df is None or df.empty:
            return [0.0] * slots

        row = get_row_at_or_before(df, date)
        if row is None:
            last_price = float(df["CLOSE_PRICE"].iloc[-1])
            return [last_price] * slots

        close_price = float(row.get("CLOSE_PRICE", 0)) or 0.0
        open_price = float(row.get("OPEN_PRICE", close_price)) or close_price
        high_price = float(row.get("HIGH_PRICE", max(open_price, close_price))) or max(open_price, close_price)
        low_price = float(row.get("LOW_PRICE", min(open_price, close_price))) or min(open_price, close_price)

        # 보수적으로 범위 보정
        high_price = max(high_price, open_price, close_price)
        low_price = min(low_price, open_price, close_price)

        return self._simulate_intraday_path(open_price, high_price, low_price, close_price, slots)

    def _simulate_intraday_path(
        self,
        open_price: float,
        high_price: float,
        low_price: float,
        close_price: float,
        slots: int
    ) -> List[float]:
        if slots <= 1:
            return [close_price]

        # V자 또는 A자 형태의 결정론적 경로 생성
        # 시가 -> 저가 -> 고가 -> 종가 (V자 형태)
        # 시가 -> 고가 -> 저가 -> 종가 (A자 형태)
        # 여기서는 간단하게 V자 형태를 기본으로 사용
        key_points = [open_price, low_price, high_price, close_price]

        segments = len(key_points) - 1
        steps_remaining = slots - 1
        extras = steps_remaining % segments if segments > 0 else 0

        path = [max(0.0, key_points[0])]
        for idx in range(segments):
            base_steps = max(1, steps_remaining // segments) if segments > 0 else 1
            start = key_points[idx]
            end = key_points[idx + 1]
            steps = base_steps + (1 if idx < extras else 0)
            steps = max(1, steps)
            for step in range(1, steps + 1):
                t = step / steps
                ease = -(math.cos(math.pi * t) - 1) / 2
                value = start + (end - start) * ease
                path.append(max(0.0, value))

        if len(path) < slots:
            path.extend([close_price] * (slots - len(path)))
        return path[:slots]

    def _clear_intraday_cache(self, date: pd.Timestamp) -> None:
        keys_to_delete = [key for key in self.intraday_price_cache.keys() if key[1] == date]
        for key in keys_to_delete:
            self.intraday_price_cache.pop(key, None)

    def _report(self) -> Dict[str, float]:
        equity_curve = [entry["equity"] for entry in self.trade_log if entry.get("type") == "EOD"]
        last_equity = equity_curve[-1] if equity_curve else self.portfolio.initial_capital
        total_return_pct = (last_equity / self.portfolio.initial_capital - 1) * 100
        peak = -float("inf")
        mdd_pct = 0.0
        for value in equity_curve:
            if value > peak:
                peak = value
            drawdown = (value - peak) / peak if peak > 0 else 0
            mdd_pct = min(mdd_pct, drawdown)

        trade_entries = self.portfolio.trade_log
        stats = {
            "final_equity": last_equity,
            "total_return_pct": total_return_pct,
            "mdd_pct": mdd_pct * 100,
            "trades": len(trade_entries),
            "open_positions": len(self.portfolio.positions),
        }
        days = len(equity_curve)
        monthly_return_pct = 0.0
        if days > 0 and last_equity > 0 and self.portfolio.initial_capital > 0:
            monthly_return = (last_equity / self.portfolio.initial_capital) ** (30 / days) - 1
            monthly_return_pct = monthly_return * 100
        stats["monthly_return_pct"] = monthly_return_pct

        monthly_target_pct = 1.4

        logger.info("=== 백테스트 결과 ===")
        logger.info("최종 누적 수익률: %.2f%%", stats["total_return_pct"])
        logger.info(
            f"최종 자산: {stats['final_equity']:,.0f}원 (초기: {self.portfolio.initial_capital:,.0f}원)"
        )
        logger.info("최대 낙폭(MDD): %.2f%%", stats["mdd_pct"])
        logger.info(
            "월간 수익률: %.2f%% (목표: %.1f%%)%s",
            stats["monthly_return_pct"],
            monthly_target_pct,
            " ✅" if stats["monthly_return_pct"] >= monthly_target_pct else "",
        )
        logger.info("최종 누적 수익률: %.2f%%", stats["total_return_pct"])
        logger.info("최대 낙폭(MDD): %.2f%%", stats["mdd_pct"])
        logger.info("월간 수익률: %.2f%%", stats["monthly_return_pct"])
        logger.info("최종 자산: %s원", f"{stats['final_equity']:,.0f}")
        logger.info("누적 거래 횟수: %d회 | 보유 중인 포지션: %d개", stats["trades"], stats["open_positions"])
        logger.info("--- ✅ 백테스트 완료 ---")

        try:
            log_dir = os.path.join(PROJECT_ROOT, self.args.log_dir)
            os.makedirs(log_dir, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")

            trades_df = pd.DataFrame(trade_entries)
            if not trades_df.empty:
                trades_path = os.path.join(log_dir, f"backtest_gpt_v2_trades_{timestamp}.csv")
                trades_df.to_csv(trades_path, index=False)

            equity_df = pd.DataFrame([entry for entry in self.trade_log if entry.get("type") == "EOD"])
            if not equity_df.empty:
                equity_path = os.path.join(log_dir, f"backtest_gpt_v2_equity_{timestamp}.csv")
                equity_df.to_csv(equity_path, index=False)
        except Exception as exc:
            logger.warning(f"결과 CSV 저장 실패: {exc}")

        # Optimizer를 위한 JSON 출력 (stdout에 직접 출력)
        print("__BACKTEST_RESULT_JSON_START__")
        print(json.dumps(stats, ensure_ascii=False))
        print("__BACKTEST_RESULT_JSON_END__")

        return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def setup_logging(level: str, log_file: Optional[str] = None) -> None:
    log_level = getattr(logging, level.upper(), logging.INFO)
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - [%(funcName)s] - %(message)s")

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(log_level)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    root_logger.addHandler(stream_handler)

    if log_file:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)


def parse_args() -> argparse.Namespace:
    preset_choices = list_preset_names()

    parser = argparse.ArgumentParser(description="Lightweight backtest runner (GPT edition)")
    parser.add_argument("--preset", choices=preset_choices if preset_choices else None,
                        help="미리 정의된 전략 프리셋 이름")
    parser.add_argument("--initial-capital", type=float, default=150_000_000)
    parser.add_argument("--days", type=int, default=180, help="최근 N일만 시뮬레이션")
    parser.add_argument("--universe-limit", type=int, default=60, help="Watchlist 상위 N개만 사용")
    parser.add_argument("--top-n", type=int, default=5, help="일별 후보 수")
    parser.add_argument("--rsi-buy", type=int, default=None)
    parser.add_argument("--breakout-buffer-pct", type=float, default=None)
    parser.add_argument("--bb-buffer-pct", type=float, default=None)
    parser.add_argument("--target-profit-pct", type=float, default=None)
    parser.add_argument("--base-stop-loss-pct", type=float, default=None)
    parser.add_argument("--max-position-allocation", type=float, default=None, help="단일 포지션 최대 비중(%)")
    parser.add_argument("--max-buys-per-day", type=int, default=None)
    parser.add_argument("--max-holdings", type=int, default=12)
    parser.add_argument("--cash-keep-pct", type=float, default=None)
    parser.add_argument("--max-stock-pct", type=float, default=None)
    parser.add_argument("--max-sector-pct", type=float, default=None)
    parser.add_argument("--llm-threshold", type=int, default=None)
    parser.add_argument("--stop-loss-atr-mult", type=float, default=None)
    parser.add_argument("--max-hold-days", type=int, default=None)
    parser.add_argument("--sell-rsi-1", type=float, default=None)
    parser.add_argument("--sell-rsi-2", type=float, default=None)
    parser.add_argument("--sell-rsi-3", type=float, default=None)
    parser.add_argument("--log-level", type=str, default="INFO")
    parser.add_argument("--log-dir", type=str, default="logs", help="자동 로그 저장 디렉터리")
    parser.add_argument("--seed", type=int, default=67, help="랜덤 시드 (기본값 67)")
    args = parser.parse_args()
    apply_strategy_defaults(args)
    return args


def apply_strategy_defaults(args: argparse.Namespace) -> None:
    preset_params = get_strategy_preset(args.preset)
    strategy_defaults = get_strategy_defaults()

    for key, fallback in strategy_defaults.items():
        if getattr(args, key, None) is None:
            if key in preset_params:
                setattr(args, key, preset_params[key])
            else:
                setattr(args, key, fallback)

    if args.preset and not preset_params:
        logger.warning("요청한 프리셋 '%s'을(를) 찾을 수 없습니다. 기본값을 사용합니다.", args.preset)
    elif args.preset:
        logger.info("전략 프리셋 '%s' 적용: %s", args.preset, preset_params)


def main() -> None:
    args = parse_args()
    
    # 재현성을 위한 시드 고정
    random.seed(args.seed)
    # np.random.seed(args.seed) # numpy 사용 시 필요
    
    timestamp = datetime.now().strftime("%Y%m%d%H%M")
    log_dir = os.path.join(PROJECT_ROOT, args.log_dir)
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"backtest_{timestamp}.log")
    setup_logging(args.log_level, log_file)
    logger.info(f"📝 로그 파일: {log_file}")

    env_path = os.path.join(PROJECT_ROOT, ".env")
    if os.path.exists(env_path):
        load_dotenv(env_path)

    # [v1.0] secrets.json 경로 설정 (로컬 실행용)
    secrets_path = os.path.join(PROJECT_ROOT, "secrets.json")
    if os.path.exists(secrets_path):
        os.environ.setdefault("SECRETS_FILE", secrets_path)

    # [v1.0] MariaDB 연결 (환경변수에서 직접 읽음)
    # 필요한 환경변수: MARIADB_HOST, MARIADB_PORT, MARIADB_USER, MARIADB_PASSWORD, MARIADB_DBNAME
    # 또는 secrets.json의 mariadb-user, mariadb-password 사용
    
    # secrets.json에서 DB 정보 로드 시도
    mariadb_user = auth.get_secret("mariadb-user")
    mariadb_password = auth.get_secret("mariadb-password")
    
    # 환경변수 설정 (get_db_connection에서 사용)
    if mariadb_user:
        os.environ.setdefault("MARIADB_USER", mariadb_user)
    if mariadb_password:
        os.environ.setdefault("MARIADB_PASSWORD", mariadb_password)
    os.environ.setdefault("MARIADB_HOST", "127.0.0.1")
    os.environ.setdefault("MARIADB_PORT", "3306")
    os.environ.setdefault("MARIADB_DBNAME", "jennie_db")
    os.environ.setdefault("DB_TYPE", "MARIADB")

    connection = database.get_db_connection()
    
    if connection is None:
        # SQLAlchemy 엔진에서 직접 연결 획득 시도
        from shared.db import connection as sa_connection
        engine = sa_connection.get_engine()
        if engine is not None:
            connection = engine.raw_connection()
            logger.info("✅ SQLAlchemy 엔진에서 직접 연결 획득 성공!")
        else:
            raise RuntimeError("❌ DB 연결을 얻을 수 없습니다. MariaDB가 실행 중인지 확인하세요.")

    try:
        runner = BacktestGPT(connection, args)
        runner.run()
    finally:
        if connection:
            connection.close()


if __name__ == "__main__":
    main()
