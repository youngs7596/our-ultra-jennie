# youngs75_jennie/strategy.py
# Version: v3.5
# [모듈] 순수한 '전략' 계산(RSI, 수익률 등)을 담당합니다.

import logging
import math
import os

import pandas as pd

# "youngs75_jennie.strategy" 이름으로 로거 생성
logger = logging.getLogger(__name__) 

_RUST_BACKEND_ENABLED = os.getenv("USE_RUST_STRATEGY", "1").lower() not in {"0", "false", "off"}
_rust_moving_average = None
_rust_rsi = None
_rust_atr = None

if _RUST_BACKEND_ENABLED:
    try:
        from strategy_core import atr as _rust_atr  # type: ignore
        from strategy_core import moving_average as _rust_moving_average  # type: ignore
        from strategy_core import rsi as _rust_rsi  # type: ignore
        logger.info("✅ Rust strategy_core 가속 모듈 활성화")
    except Exception as rust_err:  # pragma: no cover - import fail fallback
        logger.warning("⚠️ Rust strategy_core 모듈 로드 실패, Python 구현 사용: %s", rust_err)
        _RUST_BACKEND_ENABLED = False


def _prepare_sequence(values, *, reverse: bool = False):
    if values is None:
        return None
    cleaned = []
    try:
        for value in values:
            float_value = float(value)
            if math.isnan(float_value):
                return None
            cleaned.append(float_value)
    except (TypeError, ValueError):
        return None

    if not cleaned:
        return None

    if reverse:
        cleaned.reverse()
    return cleaned

# --- (기존: calculate_cumulative_return, calculate_moving_average) ---
def calculate_cumulative_return(prices_list):
    """7일 누적 수익률을 계산합니다."""
    if prices_list is None or len(prices_list) < 7:
        logger.warning("   (정보) 7일치 데이터 부족으로 수익률 계산 불가")
        return None
        
    price_yesterday, price_7_days_ago = prices_list[0], prices_list[6] 
    
    if price_7_days_ago == 0: 
        logger.warning("   (정보) 7일 전 가격이 0이라 수익률 계산 불가")
        return 0.0
        
    return ((price_yesterday - price_7_days_ago) / price_7_days_ago) * 100

def calculate_moving_average(prices_list, period=20):
    """
    주어진 가격 리스트로 이동평균(MA)을 계산합니다.
    (prices_list: [최신, ..., 과거] 순서)
    """
    if prices_list is None or len(prices_list) < period:
        logger.debug(f"   (MA) {period}일치 데이터 부족 ({len(prices_list)}일)으로 계산 불가")
        return None
    if _RUST_BACKEND_ENABLED and _rust_moving_average:
        rust_ready = _prepare_sequence(prices_list, reverse=True)
        if rust_ready and len(rust_ready) >= period:
            try:
                result = _rust_moving_average(rust_ready, period)
                if result is not None:
                    return result
            except Exception as rust_err:  # pragma: no cover - diagnostics only
                logger.debug("⚠️ Rust MA 계산 실패, Python 로직으로 폴백: %s", rust_err)
    recent_prices = prices_list[:period]
    series = pd.Series(recent_prices)
    return series.mean()

# --- (수정: calculate_rsi) ---
def calculate_rsi(data, period=14):
    """
    [수정] 주어진 데이터(DataFrame 또는 list)로 RSI(14)를 계산합니다.
    - DataFrame: 'CLOSE_PRICE' 컬럼을 사용하며, 날짜 오름차순으로 가정합니다.
    - list: [최신, ..., 과거] 순서로 가정합니다.
    """
    series = None
    
    # 1. 입력 데이터 타입에 따라 pd.Series로 변환
    if isinstance(data, pd.DataFrame):
        if 'CLOSE_PRICE' not in data.columns or len(data) < period + 1:
            logger.debug(f"   (RSI) DataFrame 데이터 부족 ({len(data)}일) 또는 'CLOSE_PRICE' 컬럼 없음")
            return None
        series = data['CLOSE_PRICE']
    elif isinstance(data, list):
        if len(data) < period + 1:
            logger.debug(f"   (RSI) List 데이터 부족 ({len(data)}일)으로 계산 불가")
            return None
        prices_reversed = data[::-1] # [과거, ..., 최신] 순으로 변경
        series = pd.Series(prices_reversed)
    else:
        logger.error(f"❌ (RSI) 지원하지 않는 데이터 타입: {type(data)}")
        return None

    # 2. RSI 계산 로직 (공통)
    try:
        if _RUST_BACKEND_ENABLED and _rust_rsi:
            rust_ready = _prepare_sequence(series.tolist())
            if rust_ready and len(rust_ready) >= period + 1:
                rust_value = _rust_rsi(rust_ready, period)
                if rust_value is not None:
                    return rust_value
        delta = series.diff(1).dropna()
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
        avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
        
        if avg_loss.iloc[-1] == 0: 
            return 100.0 if avg_gain.iloc[-1] > 0 else 50.0 
            
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return rsi.iloc[-1]
    except Exception as e:
        logger.error(f"❌ (RSI) RSI 계산 중 오류 발생: {e}", exc_info=True)
        return None

# --- (기존: calculate_atr) ---
def calculate_atr(daily_prices_df, period=14):
    """
    '3단계 ATR 스탑'을 위한 ATR(Average True Range)을 계산합니다.
    (daily_prices_df: 날짜 오름차순 정렬)
    """
    if daily_prices_df is None or len(daily_prices_df) < period:
        logger.debug(f"   (ATR) {period}일치 데이터 부족 ({len(daily_prices_df)}일)으로 계산 불가")
        return None
        
    try:
        if _RUST_BACKEND_ENABLED and _rust_atr:
            highs = _prepare_sequence(daily_prices_df['HIGH_PRICE'].tolist())
            lows = _prepare_sequence(daily_prices_df['LOW_PRICE'].tolist())
            closes = _prepare_sequence(daily_prices_df['CLOSE_PRICE'].tolist())
            if (
                highs
                and lows
                and closes
                and len(highs) == len(lows) == len(closes)
            ):
                rust_value = _rust_atr(highs, lows, closes, period)
                if rust_value is not None:
                    return rust_value

        df = daily_prices_df.copy()
        df['prev_close'] = df['CLOSE_PRICE'].shift(1)
        df['tr1'] = df['HIGH_PRICE'] - df['LOW_PRICE']
        df['tr2'] = abs(df['HIGH_PRICE'] - df['prev_close'])
        df['tr3'] = abs(df['LOW_PRICE'] - df['prev_close'])
        df['TR'] = df[['tr1', 'tr2', 'tr3']].max(axis=1)
        
        atr_series = df['TR'].ewm(com=period - 1, min_periods=period).mean()
        latest_atr = atr_series.iloc[-1]
        return latest_atr
        
    except Exception as e:
        logger.error(f"❌ (ATR) ATR 계산 중 오류 발생: {e}", exc_info=True)
        return None

# -----------------------------------------------------------
# '평균 회귀' 신호 (볼린저 밴드)
# -----------------------------------------------------------
def calculate_bollinger_bands(daily_prices_df, period=20, std_dev=2):
    """
    볼린저 밴드의 하단(Lower Band) 값을 계산합니다.
    (daily_prices_df: 날짜 오름차순 정렬)
    """
    if len(daily_prices_df) < period:
        logger.debug(f"   (BB) {period}일치 데이터 부족 ({len(daily_prices_df)}일)으로 계산 불가")
        return None
    
    try:
        series = daily_prices_df['CLOSE_PRICE']
        rolling_mean = series.rolling(window=period).mean()
        rolling_std = series.rolling(window=period).std()
        
        # 가장 최신(마지막) 값
        latest_mean = rolling_mean.iloc[-1]
        latest_std = rolling_std.iloc[-1]
        
        lower_band = latest_mean - (latest_std * std_dev)
        return lower_band
        
    except Exception as e:
        logger.error(f"❌ (BB) 볼린저 밴드 계산 중 오류 발생: {e}", exc_info=True)
        return None

# -----------------------------------------------------------
# '추세 돌파' 신호 (골든 크로스)
# -----------------------------------------------------------
def check_golden_cross(daily_prices_df, short_period=5, long_period=20):
    """
    단기 이평선(5일)이 장기 이평선(20일)을 상향 돌파(골든 크로스)했는지 확인합니다.
    (daily_prices_df: 날짜 오름차순 정렬)
    """
    if len(daily_prices_df) < long_period:
        logger.debug(f"   (MA) {long_period}일치 데이터 부족 ({len(daily_prices_df)}일)으로 계산 불가")
        return False
        
    try:
        series = daily_prices_df['CLOSE_PRICE']
        short_ma = series.rolling(window=short_period).mean()
        long_ma = series.rolling(window=long_period).mean()
        
        # [오늘(어제 종가)]과 [그저께]의 이평선 값
        today_short_ma = short_ma.iloc[-1]
        today_long_ma = long_ma.iloc[-1]
        yesterday_short_ma = short_ma.iloc[-2]
        yesterday_long_ma = long_ma.iloc[-2]

        # 골든 크로스: 어제는 5일선<20일선 이었고, 오늘은 5일선>20일선
        if yesterday_short_ma <= yesterday_long_ma and today_short_ma > today_long_ma:
            return True
        return False
        
    except Exception as e:
        logger.error(f"❌ (MA) 골든 크로스 계산 중 오류 발생: {e}", exc_info=True)
        return False
# -----------------------------------------------------------
# '추세 이탈' 신호 (데드 크로스)
# -----------------------------------------------------------
def check_death_cross(daily_prices_df, short_period=5, long_period=20):
    """
    단기 이평선(5일)이 장기 이평선(20일)을 하향 돌파(데드 크로스)했는지 확인합니다.
    (daily_prices_df: 날짜 오름차순 정렬)
    """
    if len(daily_prices_df) < long_period:
        logger.debug(f"   (MA) {long_period}일치 데이터 부족 ({len(daily_prices_df)}일)으로 계산 불가")
        return False
        
    try:
        series = daily_prices_df['CLOSE_PRICE']
        short_ma = series.rolling(window=short_period).mean()
        long_ma = series.rolling(window=long_period).mean()
        
        # [오늘(어제 종가)]과 [그저께]의 이평선 값
        today_short_ma = short_ma.iloc[-1]
        today_long_ma = long_ma.iloc[-1]
        yesterday_short_ma = short_ma.iloc[-2]
        yesterday_long_ma = long_ma.iloc[-2]

        # 데드 크로스: 어제는 5일선 >= 20일선 이었고, 오늘은 5일선 < 20일선
        if yesterday_short_ma >= yesterday_long_ma and today_short_ma < today_long_ma:
            return True
        return False
        
    except Exception as e:
        logger.error(f"❌ (MA) 데드 크로스 계산 중 오류 발생: {e}", exc_info=True)
        return False

# -----------------------------------------------------------
# '수익 실현' 신호 (RSI 과열)
# -----------------------------------------------------------
def check_rsi_overbought(prices_list, period=14, threshold=75):
    """
    실시간 가격이 반영된 RSI가 과열(예: 75) 기준을 넘었는지 확인합니다.
    (prices_list: [최신, ..., 과거] 순서)
    """
    try:
        rsi = calculate_rsi(prices_list, period)
        if rsi and rsi >= threshold:
            return rsi # 과열 상태일 때 RSI 값 반환
        return None # 과열 아님
    except Exception as e:
        logger.error(f"❌ (RSI) 과열 체크 중 오류 발생: {e}", exc_info=True)
        return None

# -----------------------------------------------------------
# 추세 추종 전략 지표
# -----------------------------------------------------------

def calculate_momentum(daily_prices_df, period=5):
    """
    모멘텀을 계산합니다. (최근 N일 수익률)
    급등장에서 최근 상승세가 강한 종목을 찾기 위한 지표입니다.
    
    Args:
        daily_prices_df: 일봉 데이터 (날짜 오름차순)
        period: 기간 (기본값: 5일)
        
    Returns:
        모멘텀 값 (수익률 %)
    """
    if len(daily_prices_df) < period + 1:
        logger.debug(f"   (Momentum) {period}일치 데이터 부족 ({len(daily_prices_df)}일)으로 계산 불가")
        return None
    
    try:
        prices = daily_prices_df['CLOSE_PRICE'].tolist()
        current_price = prices[-1]
        past_price = prices[-period-1] if len(prices) > period else prices[0]
        
        if past_price == 0:
            return None
        
        momentum = ((current_price - past_price) / past_price) * 100
        return momentum
    except Exception as e:
        logger.error(f"❌ (Momentum) 모멘텀 계산 중 오류 발생: {e}", exc_info=True)
        return None


def calculate_relative_strength(stock_prices_df: pd.DataFrame, kospi_prices_df: pd.DataFrame, period=5):
    """
    KOSPI 대비 상대 강도를 계산합니다.
    급등장에서 시장보다 더 강하게 상승하는 종목을 찾기 위한 지표입니다.
    
    Args:
        stock_prices_df: 종목 일봉 데이터 (날짜 오름차순)
        kospi_prices_df: KOSPI 일봉 데이터 (날짜 오름차순)
        period: 기간 (기본값: 5일)
        
    Returns:
        상대 강도 값 (종목 수익률 - KOSPI 수익률)
    """
    if len(stock_prices_df) < period + 1 or len(kospi_prices_df) < period + 1:
        logger.debug(f"   (Relative Strength) 데이터 부족으로 계산 불가")
        return None
    
    try:
        # 종목 수익률
        stock_prices = stock_prices_df['CLOSE_PRICE'].tolist()
        stock_current = stock_prices[-1]
        stock_past = stock_prices[-period-1] if len(stock_prices) > period else stock_prices[0]
        stock_return = ((stock_current - stock_past) / stock_past) * 100 if stock_past > 0 else 0
        
        # KOSPI 수익률
        kospi_prices = kospi_prices_df['CLOSE_PRICE'].tolist()
        kospi_current = kospi_prices[-1]
        kospi_past = kospi_prices[-period-1] if len(kospi_prices) > period else kospi_prices[0]
        kospi_return = ((kospi_current - kospi_past) / kospi_past) * 100 if kospi_past > 0 else 0
        
        # 상대 강도 = 종목 수익률 - KOSPI 수익률
        relative_strength = stock_return - kospi_return
        return relative_strength
    except Exception as e:
        logger.error(f"❌ (Relative Strength) 상대 강도 계산 중 오류 발생: {e}", exc_info=True)
        return None


def check_resistance_breakout(daily_prices_df, period=20):
    """
    저항선 돌파를 확인합니다.
    최근 N일 고점을 돌파한 경우 추세 전환 신호로 간주합니다.
    
    Args:
        daily_prices_df: 일봉 데이터 (날짜 오름차순)
        period: 기간 (기본값: 20일)
        
    Returns:
        (is_breakout, resistance_level): 돌파 여부와 저항선 레벨
    """
    if len(daily_prices_df) < period:
        logger.debug(f"   (Breakout) {period}일치 데이터 부족 ({len(daily_prices_df)}일)으로 계산 불가")
        return False, None
    
    try:
        # 최근 N일 고점
        recent_highs = daily_prices_df['HIGH_PRICE'].tail(period)
        resistance_level = recent_highs.max()
        
        # 현재가가 저항선을 돌파했는지 확인
        current_price = daily_prices_df['CLOSE_PRICE'].iloc[-1]
        is_breakout = current_price > resistance_level
        
        return is_breakout, resistance_level
    except Exception as e:
        logger.error(f"❌ (Breakout) 저항선 돌파 확인 중 오류 발생: {e}", exc_info=True)
        return False, None

# -----------------------------------------------------------
# '거래량 & 장기 추세' 신호 (v3.5)
# -----------------------------------------------------------
def check_volume_spike(current_vol, ma_vol, multiplier=2.0):
    """
    거래량 폭발 여부를 확인합니다.
    
    Args:
        current_vol: 현재 거래량
        ma_vol: 평균 거래량 (예: 20일 평균)
        multiplier: 배수 (기본값: 2.0배)
        
    Returns:
        True if current_vol >= ma_vol * multiplier else False
    """
    if ma_vol is None or ma_vol == 0:
        return False
    return current_vol >= (ma_vol * multiplier)

def check_long_term_trend(current_price, ma_value):
    """
    장기 추세(이평선) 위에 있는지 확인합니다.
    
    Args:
        current_price: 현재가
        ma_value: 이동평균값 (예: 120일 이평선)
        
    Returns:
        True if current_price >= ma_value else False
    """
    if ma_value is None or ma_value == 0:
        return False
    return current_price >= ma_value
