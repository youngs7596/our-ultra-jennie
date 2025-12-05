#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# backtest.py
# 다중 전략 기반 백테스트 엔진
#
# - 데이터 소스: STOCK_DAILY_PRICES_3Y (data_collector.py로 미리 적재)
# - 시뮬레이션:
#   1) Day-by-day로 진행
#   2) 매일 KOSPI로 시장 상황(Regime) 판단 (MarketRegimeDetector)
#   3) Regime에 맞는 전략(StrategySelector) 순서대로 BUY 신호 탐지
#   4) SELL 신호: 3단계 ATR 스탑, RSI 과열 익절
# - 결과:
#   1) 최종 누적 수익률, 최대 낙폭(MDD) 리포트
#   2) BACKTEST_TRADELOG 에 모든 가상 거래 기록 저장
#

import os
import sys
import math
import logging
from datetime import datetime, timedelta, time
from typing import Dict, List, Tuple
import argparse
from dotenv import load_dotenv
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed

# v14.6: 모듈 경로 문제를 해결하기 위해 프로젝트 루트를 sys.path에 추가
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(PROJECT_ROOT)

import shared.auth as auth
import shared.database as database
import shared.strategy as strategy
from shared.market_regime import MarketRegimeDetector, StrategySelector
# [개선 v2] Live Agent와 동일한 로직을 사용하기 위해 모듈 임포트
from shared.config import ConfigManager
from shared.position_sizing import PositionSizer
from shared.portfolio_diversification import DiversificationChecker
from shared.sector_classifier import SectorClassifier
from shared.kis.client import KISClient as KIS_API
from shared.kis.gateway_client import KISGatewayClient

from shared.factor_scoring import FactorScorer
import json # JSON 로깅을 위해 추가

import logging.handlers

# 로깅 설정 (기본값)
# 나중에 main()에서 setup_logging()을 호출하여 재설정함
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - [%(funcName)s] - %(message)s')
logger = logging.getLogger(__name__)

def setup_logging(mode: str = 'stream', log_file: str = None):
    """
    로깅 모드 설정
    :param mode: 'stream' (기본, stdout), 'buffered' (메모리 버퍼 -> 파일), 'quiet' (결과만 출력)
    :param log_file: 파일로 저장할 경로 (buffered 모드 필수)
    """
    root_logger = logging.getLogger()
    
    # 기존 핸들러 제거
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
        
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - [%(funcName)s] - %(message)s')
    
    if mode == 'quiet':
        # Quiet 모드: WARNING 이상만 출력 (속도 최적화)
        root_logger.setLevel(logging.WARNING)
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)
        
    elif mode == 'buffered':
        # Buffered 모드: 메모리에 모았다가 파일로 한 번에 기록 (I/O 최소화)
        root_logger.setLevel(logging.INFO)
        
        if not log_file:
            log_file = f"backtest_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            
        file_handler = logging.FileHandler(log_file, mode='w', encoding='utf-8')
        file_handler.setFormatter(formatter)
        
        # MemoryHandler: 10000개 레코드 또는 flush() 호출 시 파일로 기록
        memory_handler = logging.handlers.MemoryHandler(
            capacity=10000,
            target=file_handler,
            flushLevel=logging.ERROR # 에러 발생 시 즉시 플러시
        )
        root_logger.addHandler(memory_handler)
        
        # 진행 상황은 stdout으로 최소한만 출력 (선택 사항)
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.WARNING) # WARNING 이상만 콘솔 출력
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)
        
        print(f"🚀 Logging in BUFFERED mode. Full logs will be saved to: {log_file}")
        
    else: # 'stream' (Default)
        root_logger.setLevel(logging.INFO)
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)
        
        if log_file:
            file_handler = logging.FileHandler(log_file, mode='w', encoding='utf-8')
            file_handler.setFormatter(formatter)
            root_logger.addHandler(file_handler)

KOSPI_CODE = "0001"
INITIAL_CAPITAL = 150_000_000  # 1.5억원 (v1.0: 자산 증식 목표)

DDL_BACKTEST_TRADELOG = """
CREATE TABLE IF NOT EXISTS BACKTEST_TRADELOG (
  LOG_ID            INT AUTO_INCREMENT,
  TRADE_DATE        DATE NOT NULL,
  STOCK_CODE        VARCHAR(16) NOT NULL,
  STOCK_NAME        VARCHAR(128),
  TRADE_TYPE        VARCHAR(8) NOT NULL,
  QUANTITY          INT,
  PRICE             DECIMAL(15,2),
  REASON            VARCHAR(500),
  STRATEGY_SIGNAL   VARCHAR(64),
  KEY_METRICS_JSON  TEXT,
  REGIME            VARCHAR(32),
  CREATED_AT        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (LOG_ID)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

def ensure_backtest_log_table(connection):
    cur = None
    try:
        cur = connection.cursor()
        # MariaDB용 테이블 존재 확인 쿼리
        cur.execute("""
            SELECT COUNT(*) as cnt
            FROM information_schema.tables 
            WHERE table_schema = DATABASE() 
            AND table_name = 'BACKTEST_TRADELOG'
        """)
        row = cur.fetchone()
        # 딕셔너리 또는 튜플 모두 지원
        if isinstance(row, dict):
            exists = row.get('cnt', row.get('COUNT(*)', 0)) > 0
        else:
            exists = row[0] > 0
        if not exists:
            logger.info("테이블 'BACKTEST_TRADELOG' 미존재. 생성 시도...")
            cur.execute(DDL_BACKTEST_TRADELOG)
            connection.commit()
            logger.info("✅ 'BACKTEST_TRADELOG' 생성 완료.")
        else:
            logger.info("✅ 'BACKTEST_TRADELOG' 이미 존재.")
    except Exception as e:
        logger.error(f"❌ BACKTEST_TRADELOG 생성 확인 중 오류: {e}", exc_info=True)
        raise
    finally:
        if cur:
            cur.close()

def load_codes_from_3y(connection) -> List[str]:
    """
    3년치 테이블에서 가용한 종목 코드 목록을 가져옵니다. (KOSPI 제외는 호출부에서 처리)
    
    ⚠️ 중요: STOCK_DAILY_PRICES_3Y 테이블을 사용합니다 (30일치 STOCK_DAILY_PRICES 아님)
    """
    cur = None
    try:
        cur = connection.cursor()
        # ✅ 3년치 데이터 테이블 사용 (STOCK_DAILY_PRICES_3Y)
        cur.execute("""
            SELECT DISTINCT STOCK_CODE
            FROM STOCK_DAILY_PRICES_3Y
        """)
        codes = [r[0] for r in cur.fetchall()]
        return codes
    except Exception as e:
        logger.error(f"❌ 코드 목록 로드 실패: {e}", exc_info=True)
        return []
    finally:
        if cur:
            cur.close()

def load_price_series(connection, stock_code: str) -> pd.DataFrame:
    """
    특정 코드의 3년치 일봉 시계열을 날짜 오름차순으로 로드합니다.
    
    ⚠️ 중요: STOCK_DAILY_PRICES_3Y 테이블을 사용합니다 (30일치 STOCK_DAILY_PRICES 아님)
    """
    cur = None
    try:
        cur = connection.cursor()
        # ✅ 3년치 데이터 테이블 사용 (STOCK_DAILY_PRICES_3Y)
        cur.execute("""
            SELECT PRICE_DATE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, CLOSE_PRICE, VOLUME
            FROM STOCK_DAILY_PRICES_3Y
            WHERE STOCK_CODE = %s
            ORDER BY PRICE_DATE ASC
        """, (stock_code,))
        rows = cur.fetchall()
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows, columns=["PRICE_DATE", "OPEN_PRICE", "HIGH_PRICE", "LOW_PRICE", "CLOSE_PRICE", "VOLUME"])
        return df
    except Exception as e:
        logger.error(f"❌ {stock_code} 일봉 로드 실패: {e}", exc_info=True)
        return pd.DataFrame()
    finally:
        if cur:
            cur.close()

def load_financial_data(connection, stock_code: str, as_of_date: datetime = None) -> dict:
    """
    v14.0: 특정 종목의 재무제표 데이터 조회
    
    Args:
        connection: DB 연결
        stock_code: 종목 코드
        as_of_date: 기준일 (None이면 최신 데이터)
    
    Returns:
        재무제표 데이터 딕셔너리 (sales_growth, eps_growth 등)
    """
    cur = None
    try:
        cur = connection.cursor()
        
        if as_of_date:
            # 특정 날짜 이전의 최신 재무제표
            cur.execute("""
                SELECT SALES_GROWTH, EPS_GROWTH, SALES, NET_INCOME, REPORT_DATE, REPORT_TYPE
                FROM (
                    SELECT SALES_GROWTH, EPS_GROWTH, SALES, NET_INCOME, REPORT_DATE, REPORT_TYPE
                    FROM FINANCIAL_DATA
                    WHERE STOCK_CODE = %s AND REPORT_DATE <= %s
                    ORDER BY REPORT_DATE DESC
                )
                WHERE ROWNUM <= 1
            """, [stock_code, as_of_date])
        else:
            # 최신 재무제표
            cur.execute("""
                SELECT SALES_GROWTH, EPS_GROWTH, SALES, NET_INCOME, REPORT_DATE, REPORT_TYPE
                FROM (
                    SELECT SALES_GROWTH, EPS_GROWTH, SALES, NET_INCOME, REPORT_DATE, REPORT_TYPE
                    FROM FINANCIAL_DATA
                    WHERE STOCK_CODE = %s
                    ORDER BY REPORT_DATE DESC
                )
                WHERE ROWNUM <= 1
            """, [stock_code])
        
        row = cur.fetchone()
        if row:
            return {
                'sales_growth': float(row[0]) if row[0] is not None else None,
                'eps_growth': float(row[1]) if row[1] is not None else None,
                'sales': float(row[2]) if row[2] is not None else None,
                'net_income': float(row[3]) if row[3] is not None else None,
                'report_date': row[4],
                'report_type': row[5]
            }
        return {}
    except Exception as e:
        logger.debug(f"재무제표 데이터 조회 실패 ({stock_code}): {e}")
        return {}
    finally:
        if cur:
            cur.close()

def append_backtest_tradelog(connection, trade_date, code, name, trade_type, qty, price, reason, strategy_signal, key_metrics_json, regime):
    cur = None
    try:
        cur = connection.cursor()
        cur.execute("""
            INSERT INTO BACKTEST_TRADELOG (
              TRADE_DATE, STOCK_CODE, STOCK_NAME, TRADE_TYPE,
              QUANTITY, PRICE, REASON, STRATEGY_SIGNAL, KEY_METRICS_JSON, REGIME
            ) VALUES (
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
        """, [
            trade_date, code, name, trade_type, qty, price, reason, strategy_signal, key_metrics_json, regime
        ])
        connection.commit()
    except Exception as e:
        logger.error(f"❌ BACKTEST_TRADELOG insert 실패: {e}", exc_info=True)
        if connection:
            connection.rollback()
    finally:
        if cur:
            cur.close()



def generate_signals_for_stock(args):
    """
    별도 프로세스에서 실행될 시그널 생성 함수 (Picklable해야 함)
    Args:
        args: (code, df, regime_map, config_dict, scan_intervals_per_day) 튜플
    Returns:
        List[dict]: 발생한 매수 신호 리스트
    """
    code, df, regime_map, config_dict, scan_intervals_per_day = args
    signals = []
    
    try:
        # KOSPI 제외
        if code == "0001":
            return []

        # 데이터프레임 인덱스가 datetime인지 확인
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index)

        # 날짜별로 순회하지 않고, 데이터프레임을 순회하며 처리
        # 하지만 하이브리드 모드는 '날짜' + '시간(구간)' 루프가 필요함.
        # 효율성을 위해 df의 날짜 인덱스를 기반으로 순회
        
        # 1. 유효한 날짜 필터링 (Regime Map에 있는 날짜만)
        valid_dates = [d for d in df.index if d in regime_map]
        
        for current_date in valid_dates:
            # 날짜별 데이터 조회 (Direct Lookup)
            try:
                idx = df.index.get_loc(current_date)
                if isinstance(idx, slice):
                    idx = idx.stop - 1
                
                # 최소 데이터 요구량 (20일)
                if idx < 20:
                    continue
                    
                row = df.iloc[idx]
                prev_row = df.iloc[idx-1]
            except Exception:
                continue

            regime = regime_map[current_date]
            
            # 전략 매핑 (v10.8 제니's 픽) - Config에서 가져오거나 하드코딩
            # 여기서는 함수 내에 정의 (Pickling 문제 방지)
            active_strategies = []
            if regime == "STRONG_BULL":
                active_strategies = ["RESISTANCE_BREAKOUT", "VOLUME_MOMENTUM", "TREND_FOLLOWING"]
            elif regime == "BULL":
                active_strategies = ["TREND_FOLLOWING", "MEAN_REVERSION", "VOLATILITY_BREAKOUT"]
            elif regime == "SIDEWAYS":
                active_strategies = ["VOLATILITY_BREAKOUT", "MEAN_REVERSION", "TREND_FOLLOWING"]
            
            if not active_strategies:
                continue

            # 기본 데이터 추출
            day_open = row.get("OPEN_PRICE") or row["CLOSE_PRICE"]
            day_high = float(row["HIGH_PRICE"])
            day_low = float(row["LOW_PRICE"])
            day_close = float(row["CLOSE_PRICE"])
            last_volume = float(row["VOLUME"])
            rsi_current = row['RSI']
            atr_val = row['ATR']
            
            # 39개 구간에 대해 시뮬레이션
            for interval_idx in range(scan_intervals_per_day):
                # 가상 실시간 가격 생성 (Inline Logic)
                progress = interval_idx / (scan_intervals_per_day - 1)
                deterministic_factor = math.sin(interval_idx * 0.5) * 0.005
                
                if progress < 0.5:
                    base_price = day_low + (day_close - day_low) * (progress * 2)
                else:
                    afternoon_progress = (progress - 0.5) * 2
                    if afternoon_progress < 0.7:
                        base_price = day_close + (day_high - day_close) * (afternoon_progress / 0.7)
                    else:
                        base_price = day_high - (day_high - day_close) * ((afternoon_progress - 0.7) / 0.3)
                
                virtual_price = base_price * (1 + deterministic_factor)
                virtual_price = max(day_low, min(day_high, virtual_price))
                
                if virtual_price <= 0:
                    continue

                buy_signal_type = None
                key_metrics = {}

                for stype in active_strategies:
                    if stype == "MEAN_REVERSION":
                        bb_lower = row['BB_LOWER']
                        if not pd.isna(bb_lower) and virtual_price <= bb_lower:
                            buy_signal_type = "BB_LOWER"
                            key_metrics = {"close": day_close, "virtual_price": virtual_price, "bb_lower": bb_lower, "rsi": rsi_current}
                            break
                        
                        # RSI Reversal (Cross above 30)
                        prev_rsi = prev_row.get('RSI')
                        if not pd.isna(rsi_current) and not pd.isna(prev_rsi):
                            rsi_threshold = config_dict.get('BUY_RSI_OVERSOLD_THRESHOLD', 30)
                            if prev_rsi <= rsi_threshold and rsi_current > rsi_threshold:
                                # Volume Confirmation (Optional but recommended)
                                vol_ma_20 = row.get("VOL_MA_20", 0)
                                if vol_ma_20 > 0 and last_volume >= (vol_ma_20 * 2.0):
                                    buy_signal_type = "RSI_REVERSAL"
                                    key_metrics = {"rsi": rsi_current, "prev_rsi": prev_rsi, "virtual_price": virtual_price, "vol_ratio": last_volume/vol_ma_20}
                                    break
                    
                    elif stype == "VOLATILITY_BREAKOUT":
                        # Larry Williams Volatility Breakout
                        # Target = Open + (Prev Range * k)
                        # Refinement: k=0.7 (was 0.5) and Volume Filter
                        prev_high = prev_row.get("HIGH_PRICE")
                        prev_low = prev_row.get("LOW_PRICE")
                        prev_vol_ma = prev_row.get("VOL_MA_20")
                        current_vol = row.get("VOLUME")

                        if not pd.isna(prev_high) and not pd.isna(prev_low):
                            prev_range = prev_high - prev_low
                            k = 0.7 # Refined k value
                            target_price = day_open + (prev_range * k)

                            # Breakout check with Volume Filter
                            # Volume filter: Current volume > 20-day MA (Confirming interest)
                            # Note: Using daily volume is a proxy; in real-time, we'd check accumulated volume or projected volume.
                            is_volume_valid = True
                            if not pd.isna(prev_vol_ma) and not pd.isna(current_vol):
                                if current_vol <= prev_vol_ma:
                                    is_volume_valid = False
                            
                            if is_volume_valid and virtual_price >= target_price:
                                buy_signal_type = "VOLATILITY_BREAKOUT"
                                key_metrics = {
                                    "target_price": target_price, 
                                    "virtual_price": virtual_price, 
                                    "prev_range": prev_range,
                                    "vol_ratio": round(current_vol / prev_vol_ma, 2) if prev_vol_ma else 0
                                }
                                break
                    
                    elif stype == "TREND_FOLLOWING":
                        ma5 = row['MA_5']
                        ma20 = row['MA_20']
                        prev_ma5 = prev_row['MA_5']
                        prev_ma20 = prev_row['MA_20']
                        
                        if not pd.isna(ma5) and not pd.isna(ma20) and not pd.isna(prev_ma5) and not pd.isna(prev_ma20):
                            if ma5 > ma20 and prev_ma5 <= prev_ma20:
                                buy_signal_type = "GOLDEN_CROSS"
                                key_metrics = {"signal": "GOLDEN_CROSS_5_20", "rsi": rsi_current, "virtual_price": virtual_price}
                                break
                        
                        res_level = row.get('RES_20')
                        if not pd.isna(res_level) and virtual_price > res_level:
                            buy_signal_type = "RESISTANCE_BREAKOUT"
                            key_metrics = {"resistance": res_level, "close": day_close, "virtual_price": virtual_price, "rsi": rsi_current}
                            break
                            
                        if regime == "BULL" and idx >= 3:
                            ma5_3ago = df.iloc[idx-3]['MA_5']
                            ma20_3ago = df.iloc[idx-3]['MA_20']
                            if (ma5 > ma20 and ma5 > ma5_3ago and ma20 > ma20_3ago):
                                buy_signal_type = "TREND_UPWARD"
                                key_metrics = {"short_ma": ma5, "long_ma": ma20, "rsi": rsi_current, "virtual_price": virtual_price}
                                break
                    
                    elif stype == "VOLUME_MOMENTUM":
                        ma_120 = row.get("MA_120", 0)
                        if pd.isna(ma_120) or ma_120 == 0 or virtual_price < ma_120:
                            continue
                        vol_ma_20 = row.get("VOL_MA_20", 0)
                        if pd.isna(vol_ma_20) or vol_ma_20 == 0 or last_volume < (vol_ma_20 * 2.0):
                            continue
                        momentum_ok = True
                        if idx >= 120:
                            price_120_ago = float(df.iloc[idx-120]["CLOSE_PRICE"])
                            if price_120_ago > 0:
                                momentum_6m = ((virtual_price - price_120_ago) / price_120_ago) * 100
                                if momentum_6m <= 0:
                                    momentum_ok = False
                        if momentum_ok:
                            buy_signal_type = "VOLUME_MOMENTUM"
                            key_metrics = {"close": day_close, "virtual_price": virtual_price, "ma_120": ma_120, "vol_current": last_volume, "vol_ma_20": vol_ma_20}
                            break

                if buy_signal_type:
                    # 신호 발생!
                    # scan_time 계산
                    base_time = datetime.combine(current_date.date(), time(9, 0))
                    scan_time = base_time + timedelta(minutes=interval_idx * 10)
                    
                    signals.append({
                        "time": scan_time,
                        "code": code,
                        "price": virtual_price,
                        "type": buy_signal_type,
                        "atr": atr_val,
                        "key_metrics": key_metrics,
                        "regime": regime
                    })
                    # 하루에 한 번만 매수한다고 가정하면 break 할 수도 있지만,
                    # 여기서는 모든 신호를 수집하고 Backtester에서 필터링 (시간순 처리)
                    # 단, 같은 날 같은 종목이 여러 번 신호를 낼 수 있음.
                    # Backtester의 로직상 하루 1회 매수 제한 등이 있으므로,
                    # 여기서는 가장 빠른 신호 하나만 남기는 게 효율적일 수 있음.
                    # 하지만 "매수 후 매도 후 다시 매수" 시나리오도 있으므로 다 수집.
                    
    except Exception as e:
        # 로깅은 메인 프로세스에서 처리하는 게 안전하지만, 여기선 print로 디버깅
        print(f"Error processing {code}: {e}")
        return []

    return signals

class Backtester:
    def __init__(
        self,
        connection,
        diagnose_mode=False,
        diagnose_csv_path=None,
        hybrid_mode=False,
        smart_universe=False
    ):
        self.connection = connection
        # [개선 v2] Live Agent와 동일한 컴포넌트 사용
        self.config = ConfigManager(db_conn=connection)
        
        # v14.4: scout-job 호환성 및 main 함수 실행을 위한 파라미터 처리
        self.hybrid_mode = hybrid_mode
        self.diagnose_mode = diagnose_mode
        self.diagnose_csv_path = diagnose_csv_path
        self.smart_universe = smart_universe
        self.days = None # [추가] 최근 N일 백테스트 지원 (kwargs에서 제거됨)
        
        self.diagnose_records = []
        self.signal_hit_stats = {}
        self.equity_at_rocket_start = INITIAL_CAPITAL
        self.rocket_start_date = datetime(2025, 5, 1)
        
        self.market_regime_detector = MarketRegimeDetector()
        self.strategy_selector = StrategySelector()
        self.position_sizer = PositionSizer(self.config)
        self.sector_classifier = SectorClassifier(kis=None, db_pool_initialized=True)
        self.diversification_checker = DiversificationChecker(self.config, self.sector_classifier)
        self.kis = KISGatewayClient()
        self.config_manager = ConfigManager(db_conn=connection)
        self.telegram_bot = None

        
        # v14.7: 스캔 간격 설정 (10분 단위, 09:00 ~ 15:30 = 39개 구간)
        self.scan_intervals_per_day = 39

        # Data caches
        self.all_prices_cache: Dict[str, pd.DataFrame] = {}
        self.all_fundamentals_cache: Dict[str, Dict] = {}
        self.stock_names: Dict[str, str] = {} # [v16.6] code -> name mapping

        # 시뮬레이션 범위
        self.start_date = None
        self.end_date = None

        # 시뮬레이션 상태 변수
        self.cash = INITIAL_CAPITAL
        self.portfolio: Dict[str, dict] = {}
        self.equity_curve = []
        self.current_portfolio_value = 0.0  # [Optimization] 캐싱된 포트폴리오 가치
        self.portfolio_info_cache = {} # [Optimization] 포트폴리오 정보 캐시 (Diversification Check용)

    def _update_portfolio_cache(self, current_date):
        """[Optimization] 현재 포트폴리오 가치 및 정보를 계산하여 캐싱 (하루 1회 호출)"""
        portfolio_value = 0.0
        self.portfolio_info_cache = {} # 캐시 초기화
        
        for code, pos in self.portfolio.items():
            df = self.all_prices_cache.get(code)
            current_price = pos['avg_price'] # 기본값
            
            if df is not None and not df.empty:
                # 현재 날짜의 가격 조회 (없으면 가장 최근 과거 가격)
                try:
                    # 1. 정확히 해당 날짜에 데이터가 있는 경우
                    if current_date in df.index:
                        current_price = float(df.loc[current_date]["CLOSE_PRICE"])
                    else:
                        # 2. 해당 날짜 데이터가 없으면 직전 데이터 사용 (휴장일 등)
                        past_prices = df.loc[:current_date]
                        if not past_prices.empty:
                            current_price = float(past_prices["CLOSE_PRICE"].iloc[-1])
                except Exception:
                    pass
            
            val = current_price * pos["quantity"]
            portfolio_value += val
            
            # 캐시 업데이트
            self.portfolio_info_cache[code] = {
                'code': code, 
                'name': self.stock_names.get(code, code), 
                'quantity': pos['quantity'],
                'avg_price': pos['avg_price'], 
                'current_p_price': current_price
            }
        
        self.current_portfolio_value = portfolio_value
        # logger.debug(f"💰 Portfolio Value Updated: {self.current_portfolio_value:,.0f} KRW | Cash: {self.cash:,.0f} KRW")

    def _update_portfolio_value(self, current_date):
        """[Deprecated] _update_portfolio_cache로 대체됨. 호환성을 위해 남겨두거나 래핑함."""
        self._update_portfolio_cache(current_date)

    def _log_params(self):
        logger.info("="*50)
        logger.info("Backtest Parameters:")
        logger.info(f"  - Initial Capital: {INITIAL_CAPITAL:,.0f} KRW")
        logger.info(f"  - Hybrid Mode: {self.hybrid_mode}")
        logger.info(f"  - Diagnose Mode: {self.diagnose_mode}")
        
        # [개선 v2] ConfigManager에서 파라미터 가져와서 로깅
        params_to_log = [
            'MAX_BUYS_PER_DAY', 
            'PROFIT_TARGET_FULL', 
            'MAX_POSITION_PCT', 
            'CASH_KEEP_PCT', 
            'RISK_PER_TRADE_PCT',
            'ATR_PERIOD',
            'BUY_BOLLINGER_PERIOD',
            'BUY_RSI_OVERSOLD_THRESHOLD',
            'SELL_RSI_THRESHOLD'
        ]
        self.config.set('ATR_PERIOD', 14) # ATR 기간
        self.config.set('BUY_BOLLINGER_PERIOD', 20) # 볼린저 밴드 기간
        self.config.set('BUY_RSI_OVERSOLD_THRESHOLD', 30) # [Reverted] RSI 과매도 기준 (35 -> 30)
        self.config.set('SELL_RSI_THRESHOLD', 70) # RSI 과매수 기준 (매도용)
        for key in params_to_log:
            value = self.config.get(key)
            logger.info(f"  - {key}: {value}")
        logger.info("="*50)

    def _load_stock_names(self):
        """[v16.6] STOCK_MASTER에서 종목명 로드 (섹터 분류 정확도 향상용)"""
        try:
            cursor = self.connection.cursor()
            # STOCK_MASTER가 없거나 비어있을 수 있으므로 안전하게 조회
            try:
                cursor.execute("SELECT STOCK_CODE, STOCK_NAME FROM STOCK_MASTER")
                for row in cursor:
                    self.stock_names[row[0]] = row[1]
                logger.info(f"✅ Loaded {len(self.stock_names)} stock names from STOCK_MASTER")
            except Exception as e:
                logger.warning(f"⚠️ Failed to load stock names from STOCK_MASTER: {e}")
                # Fallback: STOCK_INFO 테이블 시도 (존재 여부 불확실하지만 시도)
                try:
                    cursor.execute("SELECT STOCK_CODE, STOCK_NAME FROM STOCK_INFO")
                    for row in cursor:
                        self.stock_names[row[0]] = row[1]
                    logger.info(f"✅ Loaded {len(self.stock_names)} stock names from STOCK_INFO")
                except:
                    pass
        except Exception as e:
            logger.error(f"❌ Error loading stock names: {e}")
        finally:
            if cursor:
                cursor.close()

    def _preload_data(self, stock_codes: List[str]):
        """시뮬레이션에 필요한 모든 데이터를 미리 로드하여 캐시에 저장 및 지표 선계산"""
        logger.info(f"Preloading data and calculating indicators for {len(stock_codes)} stocks...")
        
        # 1. 가격 데이터 프리로드 및 지표 계산
        for code in stock_codes:
            df = load_price_series(self.connection, code)
            if not df.empty:
                df['PRICE_DATE'] = pd.to_datetime(df['PRICE_DATE'])
                df.set_index('PRICE_DATE', inplace=True)
                
                # --- 지표 선계산 ---
                # RSI (14)
                delta = df['CLOSE_PRICE'].diff()
                gain = delta.where(delta > 0, 0)
                loss = -delta.where(delta < 0, 0)
                avg_gain = gain.ewm(com=13, min_periods=14).mean()
                avg_loss = loss.ewm(com=13, min_periods=14).mean()
                rs = avg_gain / avg_loss
                df['RSI'] = 100 - (100 / (1 + rs))
                
                # ATR (14)
                prev_close = df['CLOSE_PRICE'].shift(1)
                tr1 = df['HIGH_PRICE'] - df['LOW_PRICE']
                tr2 = (df['HIGH_PRICE'] - prev_close).abs()
                tr3 = (df['LOW_PRICE'] - prev_close).abs()
                tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
                df['ATR'] = tr.ewm(com=13, min_periods=14).mean()
                
                # Bollinger Bands (20, 2)
                ma20 = df['CLOSE_PRICE'].rolling(window=20).mean()
                std20 = df['CLOSE_PRICE'].rolling(window=20).std()
                df['BB_UPPER'] = ma20 + (std20 * 2)
                df['BB_LOWER'] = ma20 - (std20 * 2)
                
                # Moving Averages
                df['MA_5'] = df['CLOSE_PRICE'].rolling(window=5).mean()
                df['MA_20'] = ma20
                df['MA_60'] = df['CLOSE_PRICE'].rolling(window=60).mean()
                df['MA_120'] = df['CLOSE_PRICE'].rolling(window=120).mean()
                
                # Volume MA
                df['VOL_MA_20'] = df['VOLUME'].rolling(window=20).mean()
                
                # Resistance Level (20-day High, shifted by 1 to represent yesterday's high)
                # 당일 고가 돌파 여부를 확인하기 위해, 전날까지의 20일 고점을 저항선으로 사용
                df['RES_20'] = df['HIGH_PRICE'].rolling(window=20).max().shift(1)
                
                self.all_prices_cache[code] = df
        
        # 2. 펀더멘털 데이터 프리로드 (전체 로드)
        self._preload_financial_data(stock_codes)

    def _preload_financial_data(self, stock_codes: List[str]):
        """모든 종목의 재무 데이터를 미리 로드"""
        logger.info("Preloading financial data...")
        cur = None
        try:
            cur = self.connection.cursor()
            # 모든 재무 데이터 로드 (ROE 포함)
            # FINANCIAL_DATA 테이블 컬럼 확인 필요. 일단 ROE가 있다고 가정하거나 없으면 NULL 처리
            # 기존 load_financial_data는 ROE를 조회하지 않았음.
            # 하지만 FactorScorer는 ROE를 사용함.
            # FINANCIAL_DATA 테이블 스키마를 확인하지 않았지만, 보통 있음.
            # 안전하게 조회.
            cur.execute("""
                SELECT STOCK_CODE, REPORT_DATE, SALES_GROWTH, EPS_GROWTH, SALES, NET_INCOME, REPORT_TYPE
                FROM FINANCIAL_DATA
                ORDER BY STOCK_CODE, REPORT_DATE ASC
            """)
            rows = cur.fetchall()
            
            for row in rows:
                code = row[0]
                report_date = row[1]
                data = {
                    'sales_growth': float(row[2]) if row[2] is not None else None,
                    'eps_growth': float(row[3]) if row[3] is not None else None,
                    'sales': float(row[4]) if row[4] is not None else None,
                    'net_income': float(row[5]) if row[5] is not None else None,
                    'roe': None, # 현재 쿼리에서 제외됨 (테이블 컬럼 불확실)
                    'report_type': row[6],
                    'report_date': report_date
                }
                
                if code not in self.all_fundamentals_cache:
                    self.all_fundamentals_cache[code] = []
                self.all_fundamentals_cache[code].append(data)
                
        except Exception as e:
            logger.error(f"재무 데이터 프리로드 실패: {e}")
        finally:
            if cur:
                cur.close()

    def _get_financial_data(self, code, current_date):
        """캐시된 재무 데이터에서 해당 날짜 기준 최신 데이터 조회"""
        if code not in self.all_fundamentals_cache:
            return {}
        
        # 날짜순으로 정렬되어 있다고 가정
        reports = self.all_fundamentals_cache[code]
        # 뒤에서부터 검색 (최신 데이터 우선)
        for report in reversed(reports):
            if report['report_date'] <= current_date:
                return report
        return {}
            
        logger.info(f"Preloading complete. Loaded prices for {len(self.all_prices_cache)} stocks.")

    def _load_historical_watchlists(self):
        """
        [v15.1] WATCHLIST_HISTORY에서 전체 기간의 히스토리를 로드합니다.
        Returns:
            dict: {date(YYYY-MM-DD): [stock_code, ...]}
            set: all_unique_codes
        """
        history_map = {}
        all_codes = set()
        
        cursor = self.connection.cursor()
        try:
            # 전체 히스토리 조회
            cursor.execute("SELECT TO_CHAR(SNAPSHOT_DATE, 'YYYY-MM-DD'), STOCK_CODE FROM WATCHLIST_HISTORY")
            for row in cursor:
                date_str = row[0]
                code = row[1]
                
                # 날짜별 맵핑
                if date_str not in history_map:
                    history_map[date_str] = []
                history_map[date_str].append(code)
                
                # 전체 유니크 코드
                all_codes.add(code)
                
            logger.info(f"✅ Historical Watchlist 로드 완료: {len(history_map)}일치, 총 {len(all_codes)}개 종목")
            return history_map, list(all_codes)
        except Exception as e:
            logger.warning(f"⚠️ Historical Watchlist 로드 실패 (테이블이 비어있거나 없을 수 있음): {e}")
            return {}, []
        finally:
            cursor.close()

    def _precalculate_market_regimes(self, kospi_df_filtered):
        """
        [v16.0] 전체 기간의 Market Regime을 미리 계산
        Returns:
            dict: {date(datetime): regime(str)}
        """
        regime_map = {}
        logger.info("⏳ Market Regime Pre-calculation...")
        
        # KOSPI 데이터 전체가 필요할 수 있으므로 원본 kospi_df 사용 권장
        # 하지만 여기서는 filtered 기준으로 순회
        
        # Regime Detector는 rolling window가 필요하므로, 
        # kospi_df_filtered의 첫 날짜 이전 데이터도 포함된 kospi_df가 필요함.
        # self.all_prices_cache[KOSPI_CODE]를 사용
        
        full_kospi_df = self.all_prices_cache[KOSPI_CODE]
        
        for idx, row in kospi_df_filtered.iterrows():
            current_date = row["PRICE_DATE"] if "PRICE_DATE" in row else idx
            
            # 해당 날짜까지의 윈도우
            # (성능 최적화를 위해 매번 slice하지 않고 인덱스 기반 접근이 좋지만, 
            # Regime Detector 내부 로직상 DF가 필요함)
            # 여기서는 단순화를 위해 slice 사용 (하루 1회라 부담 적음)
            kospi_window = full_kospi_df.loc[:current_date]
            kospi_current = float(kospi_window["CLOSE_PRICE"].iloc[-1])
            
            regime, _ = self.market_regime_detector.detect_regime(
                kospi_window[["CLOSE_PRICE"]].rename(columns={"CLOSE_PRICE": "CLOSE_PRICE"}),
                kospi_current,
                quiet=True
            )
            regime_map[current_date] = regime
            
        logger.info(f"✅ Market Regime Pre-calculation Complete ({len(regime_map)} days)")
        return regime_map

    def run(self):
        # [v16.6] Load stock names for better sector classification
        self._load_stock_names()

        # Load KOSPI and infer common calendar
        kospi_df = load_price_series(self.connection, KOSPI_CODE)
        if kospi_df.empty:
            raise RuntimeError("KOSPI(0001) 데이터가 없습니다. data_collector.py를 먼저 실행하세요.")
        
        # 인덱스 설정
        kospi_df['PRICE_DATE'] = pd.to_datetime(kospi_df['PRICE_DATE'])
        kospi_df.set_index('PRICE_DATE', inplace=True, drop=False)

        # 데이터 범위 확인 및 로깅
        kospi_start_date = kospi_df.index.min()
        kospi_end_date = kospi_df.index.max()
        
        # v14.0: 실제 종목 데이터 시작일 확인하여 백테스트 시작일 조정
        all_codes = load_codes_from_3y(self.connection)
        stock_codes = [c for c in all_codes if c != KOSPI_CODE]
        
        if stock_codes:
            # 첫 번째 종목의 데이터 시작일 확인
            first_stock_df = load_price_series(self.connection, stock_codes[0])
            if not first_stock_df.empty:
                stock_start_date = pd.to_datetime(first_stock_df.iloc[0]["PRICE_DATE"])
                # 종목 데이터 시작일이 KOSPI보다 늦으면 조정
                if stock_start_date > kospi_start_date:
                    logger.warning(f"⚠️ 종목 데이터 시작일({stock_start_date})이 KOSPI 시작일({kospi_start_date})보다 늦습니다.")
                    logger.info(f"📅 백테스트 시작일을 종목 데이터 시작일로 조정: {stock_start_date}")
                    start_date = stock_start_date
                else:
                    start_date = kospi_start_date
            else:
                start_date = kospi_start_date
        else:
            start_date = kospi_start_date
        
        end_date = kospi_end_date
        
        if self.days:
            # 최근 N일로 시작일 조정
            start_date = end_date - pd.Timedelta(days=self.days)
            if start_date < kospi_start_date:
                start_date = kospi_start_date
            logger.info(f"📅 최근 {self.days}일 백테스트: {start_date} ~ {end_date}")
        
        total_days = (end_date - start_date).days
        logger.info(f"📅 백테스트 기간: {start_date} ~ {end_date} (총 {total_days}일)")
        
        # [v15.1] Historical Watchlist 로드 (Point-in-Time Backtest)
        historical_watchlists, historical_codes = self._load_historical_watchlists()
        
        # [v16.3] Smart Universe 로드 (우선순위 1)
        smart_universe_path = os.path.join(PROJECT_ROOT, "smart_universe.json")
        if self.smart_universe and os.path.exists(smart_universe_path):
            import json
            try:
                with open(smart_universe_path, "r", encoding="utf-8") as f:
                    smart_universe = json.load(f)
                    # 상위 50개 종목 코드 추출 (정예화)
                    smart_universe_codes = [item["code"] for item in smart_universe[:50]]
                    logger.info(f"🌌 Smart Universe 모드: {len(smart_universe_codes)}개 종목 로드 완료 (Top 50)")
                    codes_to_test = smart_universe_codes
            except Exception as e:
                logger.error(f"❌ Smart Universe 로드 실패: {e}")
                codes_to_test = []
        elif historical_codes:
            logger.info(f"📜 Point-in-Time Backtest 모드: {len(historical_codes)}개 종목 (History 기반)")
            codes_to_test = historical_codes
        else:
            logger.warning("⚠️ WatchList History가 없습니다. 현재 WatchList를 Fallback으로 사용합니다.")
            watchlist_stocks = database.get_active_watchlist(self.connection)
            codes_to_test = list(watchlist_stocks.keys())
            
        logger.info(f"시뮬레이션 대상 종목 수: {len(codes_to_test)} (Universe)")
        
        self._preload_data([KOSPI_CODE] + codes_to_test)
        
        if KOSPI_CODE not in self.all_prices_cache:
            raise ValueError("KOSPI 데이터 로드 실패")
        
        self._log_params()
        
        # 실제 사용 가능한 데이터 범위 확인
        available_stocks = [code for code, df in self.all_prices_cache.items() 
                           if code != KOSPI_CODE and not df.empty and len(df) >= 20]
        logger.info(f"📊 백테스트 사용 가능 종목: {len(available_stocks)}개 (최소 20일 데이터 보유)")

        # 백테스트 시작일 이후의 KOSPI 데이터만 사용
        kospi_df_filtered = kospi_df[kospi_df.index >= start_date].copy()
        if kospi_df_filtered.empty:
            raise RuntimeError(f"백테스트 시작일({start_date}) 이후 KOSPI 데이터가 없습니다.")
        
        logger.info(f"📊 실제 백테스트 기간: {kospi_df_filtered.index[0]} ~ {kospi_df_filtered.index[-1]} (총 {len(kospi_df_filtered)}일)")
        
        # 시뮬레이션 변수
        self.cash = INITIAL_CAPITAL
        self.portfolio.clear()
        self.equity_curve.clear()
        
        # [v16.0] Event-Driven Architecture Transformation
        
        # 1. Market Regime Pre-calculation
        regime_map = self._precalculate_market_regimes(kospi_df_filtered)
        
        # 2. Parallel Signal Generation
        logger.info("🚀 Generating Buy Signals in Parallel...")
        
        # Config를 dict로 변환 (Pickling을 위해)
        config_dict = {
            'BUY_RSI_OVERSOLD_THRESHOLD': self.config.get_int('BUY_RSI_OVERSOLD_THRESHOLD', 30),
            # 필요한 다른 설정들도 추가 가능
        }
        
        # 작업 준비
        tasks = []
        for code in available_stocks:
            df = self.all_prices_cache[code]
            tasks.append((code, df, regime_map, config_dict, self.scan_intervals_per_day))
            
        all_signals = []
        
        # [v16.1] ProcessPoolExecutor for True Parallelism (Bypass GIL)
        # ThreadPoolExecutor는 GIL 때문에 CPU-bound 작업에서 병렬 효과가 제한적임.
        # ProcessPoolExecutor를 사용하여 멀티코어를 온전히 활용.
        # 단, DataFrame Pickling 오버헤드가 있으나, 연산량이 충분히 많으므로 이득이 더 큼.
        
        # Auto Optimizer 등에서 호출 시 프로세스 폭발 방지를 위해 환경변수 지원
        env_max_workers = os.environ.get('MAX_WORKERS')
        if env_max_workers:
            max_workers = int(env_max_workers)
            logger.info(f"🔥 Using ProcessPoolExecutor with {max_workers} workers (from ENV)")
        else:
            max_workers = min(os.cpu_count() or 4, 16) # 최대 16개 프로세스 제한
            logger.info(f"🔥 Using ProcessPoolExecutor with {max_workers} workers")
        
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            # map 사용 (순서 보장 불필요하지만 간편함)
            # chunksize를 설정하여 IPC 오버헤드 감소
            results = executor.map(generate_signals_for_stock, tasks, chunksize=1)
            
            for signals in results:
                all_signals.extend(signals)
                
        # 3. Sort Signals by Time
        all_signals.sort(key=lambda x: x['time'])
        logger.info(f"✅ Signal Generation Complete: {len(all_signals)} signals found.")
        
        # 4. Event-Driven Simulation Loop
        signal_idx = 0
        total_signals = len(all_signals)
        
        mode_str = "하이브리드 모드 (Event-Driven)" if self.hybrid_mode else "일봉 모드"
        logger.info(f"--- 시뮬레이션 시작 ({mode_str}) ---")
        
        for idx in range(len(kospi_df_filtered)):
            current_date = kospi_df_filtered.index[idx]
            logger.info(f"############### 시뮬레이션 시작: {current_date.date()} ###############")

            regime = regime_map.get(current_date, "SIDEWAYS")
            
            # [Optimization] 하루 시작 시 포트폴리오 가치 갱신 (O(N))
            self._update_portfolio_cache(current_date)
            
            # 1) Process Sells (Daily Open/Close)
            self._process_sells(current_date, regime)
            
            # [Optimization] 매도 후 포트폴리오 가치 재갱신 (현금화 반영) - _process_sells 내부에서 처리하도록 변경 가능하지만 안전하게 호출
            # self._update_portfolio_cache(current_date) # _process_sells에서 처리함
            
            # 2) Process Buy Signals (Intraday)
            # 현재 날짜에 해당하는 신호들 처리
            # 신호는 시간순 정렬되어 있음
            
            # 다음 날짜의 09:00 이전까지의 신호를 처리 (즉, 오늘 장중 신호)
            next_day_limit = current_date + timedelta(days=1)
            
            buys_today = 0
            
            while signal_idx < total_signals:
                signal = all_signals[signal_idx]
                sig_time = signal['time']
                
                # 날짜가 넘어가면 중단
                if sig_time.date() > current_date.date():
                    break
                
                signal_idx += 1
                
                # 매수 조건 체크
                
                # [Optimization] 현금 부족 시 조기 종료 (CASH_KEEP_PCT)
                # Tier 2 매수는 cash_ratio > 0.3 조건이 있으므로, 여기서 막으면 안됨?
                # 아니, CASH_KEEP_PCT는 절대적인 하한선임.
                # Tier 2 조건은 "현금이 많을 때" 사는 것이고, CASH_KEEP_PCT는 "현금이 적을 때" 안 사는 것.
                # 따라서 CASH_KEEP_PCT 미만이면 Tier 1이든 Tier 2든 못 삼.
                
                # 매번 계산하면 느리므로, while 루프 밖에서 계산하고 싶지만 cash가 변함.
                # 하지만 연산이 간단하므로 여기서 수행.
                total_assets_approx = self.cash + self.current_portfolio_value
                cash_keep_pct = self.config.get_float('CASH_KEEP_PCT', 5.0)
                min_required_cash = total_assets_approx * (cash_keep_pct / 100.0)
                
                # [Optimization] 현금 여유가 2% 미만이면 스킵 (buffer)
                if self.cash < min_required_cash * 1.02:
                    # logger.info(f"💰 Cash Limit Skip: Cash {self.cash:,.0f} < Req {min_required_cash:,.0f} (Buffer 2%, PCT {cash_keep_pct}%) | Port: {self.current_portfolio_value:,.0f}")
                    continue

                # 1. Universe Check (Historical Watchlist & Tiered Strategy)
                current_date_str = current_date.strftime('%Y-%m-%d')
                is_tier1 = True
                if historical_watchlists:
                    daily_universe = historical_watchlists.get(current_date_str, [])
                    if signal['code'] not in daily_universe:
                        is_tier1 = False
                        # [Tier 2 조건] Watchlist에 없는 종목은 현금 비중이 30% 이상일 때만 매수 시도
                        # 자산 평가 (성능을 위해 매번 계산하지 않고 self.cash만 체크하거나, 필요시 계산)
                        # 여기서는 정확성을 위해 계산 (캐싱 고려 가능)
                        total_assets = self._compute_equity(current_date)
                        cash_ratio = self.cash / total_assets
                        if cash_ratio < 0.3:
                            continue # 현금 부족 시 Tier 2 스킵
                        
                        # Tier 2 로깅 (너무 많을 수 있으므로 생략하거나 디버그)
                        # logger.debug(f"🔍 Tier 2 Candidate: {signal['code']}")
                
                # 2. Portfolio Limit Check
                if len(self.portfolio) >= self.config.get_int('MAX_HOLDING_STOCKS', 50):
                    continue
                
                # 3. Daily Buy Limit Check
                if buys_today >= self.config.get_int('MAX_BUYS_PER_DAY', 1):
                    continue
                
                # 4. Already in Portfolio Check
                if signal['code'] in self.portfolio:
                    continue
                
                # 5. Regime Filter (Bear Market)
                can_buy = (regime != MarketRegimeDetector.REGIME_BEAR) or (
                    self.config.get_bool('IGNORE_BEAR_ON_STRONG_BULL', True) and regime == MarketRegimeDetector.REGIME_STRONG_BULL
                )
                if not can_buy:
                    continue
                
                # Execute Buy
                is_bought, cost = self._execute_buy_signal(signal, regime)
                if is_bought:
                    buys_today += 1
                    
                    # [v16.4] Tier 2 종목 매수 시 로깅
                    if not is_tier1:
                        logger.info(f"✨ Tier 2 Stock Bought: {signal['code']} (Strategy: {signal.get('type', 'Unknown')})")
                    # self.cash -= cost # [Bug Fix] _execute_buy_signal에서 이미 차감함
            
            # 3) Mark Equity
            equity = self._compute_equity(current_date)
            self.equity_curve.append((current_date, equity))
            
            # Capture equity at rocket start
            if current_date >= self.rocket_start_date and self.equity_at_rocket_start == INITIAL_CAPITAL:
                self.equity_at_rocket_start = equity
            
            # Progress Log
            if (idx + 1) % 100 == 0 or idx == len(kospi_df_filtered) - 1:
                progress_pct = ((idx + 1) / len(kospi_df_filtered)) * 100
                logger.info(f"진행: {idx + 1}/{len(kospi_df_filtered)}일 ({progress_pct:.1f}%) - {current_date.date()} | 현재 자산: {equity:,.0f}원")

        # Report Generation (Existing Logic)
        return self._generate_report()

    def _execute_buy_signal(self, signal, regime) -> Tuple[bool, float]:
        """Event-Driven 매수 실행"""
        code = signal['code']
        virtual_price = signal['price']
        buy_signal_type = signal['type']
        atr_val = signal['atr']
        key_metrics = signal['key_metrics']
        scan_time = signal['time']
        current_date = scan_time.date() # datetime.date
        # current_date를 datetime으로 변환 (시간 00:00) - DB 저장 등 호환성 위해
        current_date_dt = datetime.combine(current_date, time(0,0))
        
        # 슬리피지 적용
        buy_price_with_slippage = virtual_price * 1.00115
        
        # 포지션 사이징
        # [업그레이드] 실시간 로직과 동일한 PositionSizer 사용
        # [Optimization] 캐싱된 포트폴리오 가치 사용 (O(1))
        current_portfolio_value = self.current_portfolio_value
        
        total_assets = self.cash + current_portfolio_value

        sizing_result = self.position_sizer.calculate_quantity(
            stock_code=code,
            stock_price=buy_price_with_slippage,
            atr=atr_val if not pd.isna(atr_val) else buy_price_with_slippage * 0.02, # ATR 없으면 2% 변동성 가정
            account_balance=self.cash,
            portfolio_value=current_portfolio_value
        )
        
        # [업그레이드] 시장 상황에 따른 비중 조절
        risk_setting = self.market_regime_detector.get_dynamic_risk_setting(regime)
        position_size_ratio = risk_setting.get('position_size_ratio', 1.0)
        qty = int(sizing_result.get('quantity', 0) * position_size_ratio) if sizing_result else 0
        
        if qty <= 0:
            return False, 0.0 # 계산된 수량이 0이면 매수 불가
        
        cost = buy_price_with_slippage * qty
        
        # [업그레이드] 분산 투자 원칙 검증 및 동적 포지션 사이징
        # [버그 수정] scan_time 전달하여 미래 데이터 참조 방지
        # [v16.5] Dynamic Sector Limits 적용을 위해 regime 전달
        is_approved, div_result = self._check_diversification(signal, qty, buy_price_with_slippage, total_assets, current_date=scan_time, regime=regime)
        
        original_qty = qty # [Smart Skip] 원래 목표 수량 저장

        if not is_approved:
            # 섹터 비중 초과로 인한 거절인 경우, 남은 룸만큼만 매수 시도
            if "섹터" in div_result.get('reason', '') and "비중 초과" in div_result.get('reason', ''):
                current_sector_exposure = div_result.get('current_sector_exposure', 0.0)
                # [v16.5] Dynamic Limits 적용
                max_sector_pct = self.config.get_float('MAX_SECTOR_PCT', 30.0)
                if regime == MarketRegimeDetector.REGIME_STRONG_BULL:
                    max_sector_pct = 50.0

                remaining_room_pct = max_sector_pct - current_sector_exposure
                
                # 최소한의 룸(예: 0.5%)은 있어야 매수 진행
                if remaining_room_pct > 0.5:
                    # [개선] 안전 마진 0.1% 적용 (부동소수점 오차 방지)
                    safe_room_pct = max(0, remaining_room_pct - 0.1)
                    max_allowed_amount = total_assets * (safe_room_pct / 100.0)
                    new_qty = int(max_allowed_amount / buy_price_with_slippage)
                    
                    # [Smart Skip] 쪼그라든 수량이 원래 목표의 50% 미만이면 과감히 패스
                    if new_qty > 0:
                        resize_ratio = new_qty / original_qty
                        if resize_ratio < 0.5:
                            logger.info(f"⏭️ Smart Skip: 수량이 너무 적어 패스 ({qty} -> {new_qty}, {resize_ratio*100:.1f}%)")
                            return False, 0.0
                        
                        logger.info(f"⚠️ 분산 투자 제한으로 수량 조정: {qty} -> {new_qty} (섹터 여유: {remaining_room_pct:.2f}%, 안전 마진 적용)")
                        qty = new_qty
                        cost = buy_price_with_slippage * qty
                        
                        # 재검증 (혹시 모를 다른 규칙 위반 확인)
                        is_approved_retry, _ = self._check_diversification(signal, qty, buy_price_with_slippage, total_assets, current_date=scan_time, regime=regime)
                        if not is_approved_retry:
                            return False, 0.0
                    else:
                        return False, 0.0
                else:
                    return False, 0.0
            else:
                # 단일 종목 비중 초과로 인한 거절인 경우, 최대 허용 비중만큼만 매수 시도
                if "단일 종목" in div_result.get('reason', '') and "비중 초과" in div_result.get('reason', ''):
                    max_stock_pct = self.config.get_float('MAX_POSITION_VALUE_PCT', 10.0)
                    if regime == MarketRegimeDetector.REGIME_STRONG_BULL:
                        max_stock_pct = 20.0
                    
                    # 현재 자산 대비 최대 허용 금액 계산
                    # [개선] 안전 마진 0.1% 적용
                    safe_stock_pct = max(0, max_stock_pct - 0.1)
                    max_allowed_amount = total_assets * (safe_stock_pct / 100.0)
                    new_qty = int(max_allowed_amount / buy_price_with_slippage)
                    
                    if new_qty > 0 and new_qty < qty:
                        # [Smart Skip]
                        resize_ratio = new_qty / original_qty
                        if resize_ratio < 0.5:
                            logger.info(f"⏭️ Smart Skip: 수량이 너무 적어 패스 ({qty} -> {new_qty}, {resize_ratio*100:.1f}%)")
                            return False, 0.0

                        logger.info(f"⚠️ 단일 종목 제한으로 수량 조정: {qty} -> {new_qty} (제한: {max_stock_pct}%, 안전 마진 적용)")
                        qty = new_qty
                        cost = buy_price_with_slippage * qty
                        
                        # 재검증
                        is_approved_retry, _ = self._check_diversification(signal, qty, buy_price_with_slippage, total_assets, current_date=scan_time, regime=regime)
                        if not is_approved_retry:
                            return False, 0.0
                    else:
                        return False, 0.0
                else:
                    return False, 0.0 # 다른 이유면 매수 취소
        
        if cost > self.cash:
            return False, 0.0

        # 포트폴리오 추가
        self.portfolio[code] = {
            "quantity": qty,
            "avg_price": buy_price_with_slippage,
            "entry_date": current_date_dt,
            "entry_time": scan_time,
            "atr_entry": atr_val,
            "stop_loss_initial": buy_price_with_slippage - (atr_val * self.config.get_float('ATR_MULTIPLIER_INITIAL_STOP', 2.0)) if not pd.isna(atr_val) else buy_price_with_slippage * 0.95,
            "stop_loss_trailing": None,
            "high_price": buy_price_with_slippage,
            "buy_signal": buy_signal_type,
            "sold_ratio": 0.0,
            "original_quantity": qty,
        }
        self.cash -= cost
        
        # [Optimization] 포트폴리오 가치 즉시 업데이트 (매수분 추가)
        # 여기서는 매수 가격(비용 포함 전 가격)으로 가치 증가
        # cost는 수수료 포함이므로, 실제 자산 가치는 qty * buy_price_with_slippage
        position_value = qty * buy_price_with_slippage
        self.current_portfolio_value += position_value
        
        # [Optimization] 캐시 업데이트
        self.portfolio_info_cache[code] = {
            'code': code, 
            'name': self.stock_names.get(code, code), 
            'quantity': qty,
            'avg_price': buy_price_with_slippage, 
            'current_p_price': buy_price_with_slippage
        }
        
        # 로그 기록
        append_backtest_tradelog(
            self.connection, current_date_dt, code, code, "BUY", qty, buy_price_with_slippage,
            f"Event-Driven: {scan_time.strftime('%H:%M')} 신호", buy_signal_type, json.dumps(key_metrics), regime
        )

        return True, cost

    def _generate_report(self):
        """기존 run() 메서드의 리포트 생성 로직 분리"""
        # 진단 모드 결과 저장
        if self.diagnose_mode and self.diagnose_csv_path:
            try:
                if self.diagnose_records:
                    df_diagnose = pd.DataFrame(self.diagnose_records)
                    df_diagnose.to_csv(self.diagnose_csv_path, index=False, encoding="utf-8-sig")
                    logger.info(f"진단 모드 CSV 저장: {self.diagnose_csv_path} ({len(self.diagnose_records)}건)")
                
                # 히트율 리포트
                if self.signal_hit_stats:
                    logger.info("=== 신호별 히트율 리포트 ===")
                    for signal_type, stats in self.signal_hit_stats.items():
                        if stats["total"] > 0:
                            hit_rate = (stats["hits"] / stats["total"]) * 100.0
                            avg_return = stats["total_return"] / stats["hits"] if stats["hits"] > 0 else 0.0
                            avg_days = stats["total_days"] / stats["hits"] if stats["hits"] > 0 else 0.0
                            logger.info(f"{signal_type}: 히트율 {hit_rate:.1f}% ({stats['hits']}/{stats['total']}), 평균 수익률 {avg_return:.2f}%, 평균 보유일 {avg_days:.1f}일")
            except Exception as e:
                logger.error(f"진단 모드 결과 저장 실패: {e}", exc_info=True)

        # Report
        final_equity = self.equity_curve[-1][1] if self.equity_curve else self.cash
        total_return = (final_equity - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100.0
        mdd = self._compute_mdd()

        # 로켓장 수익률 계산
        if self.equity_at_rocket_start > INITIAL_CAPITAL:
            rocket_return = (final_equity - self.equity_at_rocket_start) / self.equity_at_rocket_start * 100.0
        else:
            rocket_return = 0.0
        
        # 월간 수익률 계산 및 목표 달성 여부
        target_monthly_return_min = 1.4  # 목표
        target_monthly_return_max = 1.4  # 목표
        
        if len(self.equity_curve) > 1:
            start_date = self.equity_curve[0][0]
            end_date = self.equity_curve[-1][0]
            days_diff = (end_date - start_date).days
            months = days_diff / 30.0 if days_diff > 0 else 1.0
            monthly_return = ((final_equity / INITIAL_CAPITAL) ** (1.0 / months) - 1) * 100.0 if months > 0 else 0.0
            target_achieved = monthly_return >= target_monthly_return_min
        else:
            monthly_return = 0.0
            target_achieved = False
        
        logger.info(f"=== 백테스트 결과 ===")
        logger.info(f"최종 누적 수익률: {total_return:.2f}%")
        logger.info(f"최대 낙폭(MDD): {mdd:.2f}%")
        if target_monthly_return_min == target_monthly_return_max:
            logger.info(f"월간 수익률: {monthly_return:.2f}% (목표: {target_monthly_return_min}%) {'✅' if target_achieved else '❌'}")
        else:
            logger.info(f"월간 수익률: {monthly_return:.2f}% (목표: {target_monthly_return_min}% ~ {target_monthly_return_max}%) {'✅' if target_achieved else '❌'}")
        logger.info(f"최종 자산: {final_equity:,.0f}원 (초기: {INITIAL_CAPITAL:,.0f}원)")

        # Report Generation (Existing Logic)
        report_dict = {
            "final_equity": final_equity,
            "total_return_pct": total_return,
            "mdd_pct": mdd,
            "rocket_return_pct": rocket_return,
            "monthly_return_pct": monthly_return,  # v14.2
            "target_achieved": target_achieved,  # v14.2
        }
        
        # [v16.2] Quiet 모드에서도 결과 파싱을 위해 표준 출력으로 결과 JSON 출력
        # Auto Optimizer가 이 출력을 캡처하여 파싱함
        print(f"__BACKTEST_RESULT_JSON_START__")
        print(json.dumps(report_dict))
        print(f"__BACKTEST_RESULT_JSON_END__")
        
        # 기존 텍스트 포맷도 유지 (호환성)
        logger.info(f"최종 누적 수익률: {total_return:.2f}%")
        logger.info(f"최대 낙폭(MDD): {mdd:.2f}%")
        logger.info(f"월간 수익률: {monthly_return:.2f}%")
        logger.info(f"최종 자산: {final_equity:,.0f}원")

        return report_dict

    def _compute_equity(self, current_date) -> float:
        # [Optimization] 캐싱된 값 사용 (O(1))
        # current_date 인자는 호환성을 위해 유지하되, 실제로는 self.current_portfolio_value 사용
        return self.cash + self.current_portfolio_value

    def _compute_mdd(self) -> float:
        if not self.equity_curve:
            return 0.0
        peaks = []
        max_drawdown = 0.0
        peak = -math.inf
        for _, eq in self.equity_curve:
            if eq > peak:
                peak = eq
            dd = (eq - peak) / peak * 100.0 if peak > 0 else 0.0
            if dd < max_drawdown:
                max_drawdown = dd
        return abs(max_drawdown)

    def _slice_until_date(self, df: pd.DataFrame, current_date) -> pd.DataFrame:
        return df[df.index <= current_date]
    
    def _generate_virtual_intraday_price(self, df_window: pd.DataFrame, interval_idx: int, total_intervals: int) -> float:
        """
        v13.0: 하이브리드 모드용 가상 실시간 가격 생성 (결정론적 버전)
        
        당일 고가/저가/종가를 이용하여 10분 간격 가상 가격을 생성합니다.
        랜덤 요소를 제거하고 결정론적인 패턴을 사용하여 재현 가능한 결과를 보장합니다.
        
        Args:
            df_window: 현재 날짜까지의 데이터 (마지막 행이 당일)
            interval_idx: 현재 구간 인덱스 (0~38)
            total_intervals: 총 구간 수 (39)
        
        Returns:
            가상 실시간 가격
        """
        if df_window.empty:
            return 0.0
        
        last_row = df_window.iloc[-1]
        day_open = last_row.get("OPEN_PRICE") or last_row["CLOSE_PRICE"]  # 시가가 없으면 종가 사용
        day_high = float(last_row["HIGH_PRICE"])
        day_low = float(last_row["LOW_PRICE"])
        day_close = float(last_row["CLOSE_PRICE"])
        
        # 간단한 시뮬레이션: 구간 인덱스에 따라 고가와 저가 사이를 보간
        # 오전에는 저가 쪽, 오후에는 고가 쪽으로 이동하는 패턴
        progress = interval_idx / (total_intervals - 1)  # 0.0 ~ 1.0
        
        # 결정론적 변동성 추가 (랜덤 요소 제거)
        # interval_idx를 기반으로 한 작은 변동 (±0.5%)
        # sin 함수를 사용하여 부드러운 변동 패턴 생성
        deterministic_factor = math.sin(interval_idx * 0.5) * 0.005  # ±0.5% 변동
        
        # 오전(0.0~0.5): 저가 → 중간가
        # 오후(0.5~1.0): 중간가 → 고가 → 종가
        if progress < 0.5:
            # 오전: 저가에서 중간가로 상승
            base_price = day_low + (day_close - day_low) * (progress * 2)
        else:
            # 오후: 중간가에서 고가로 상승 후 종가로 하락
            afternoon_progress = (progress - 0.5) * 2  # 0.0 ~ 1.0
            if afternoon_progress < 0.7:
                # 고가까지 상승
                base_price = day_close + (day_high - day_close) * (afternoon_progress / 0.7)
            else:
                # 고가에서 종가로 하락
                base_price = day_high - (day_high - day_close) * ((afternoon_progress - 0.7) / 0.3)
        
        virtual_price = base_price * (1 + deterministic_factor)
        
        # 가격 범위 제한 (당일 고가/저가 범위 내)
        virtual_price = max(day_low, min(day_high, virtual_price))
        
        return virtual_price
    
    def _get_scan_intervals(self, current_date: datetime) -> List[datetime]:
        """
        v13.0: 하루를 10분 간격으로 나눈 스캔 시점 리스트 생성
        
        Returns:
            스캔 시점 리스트 (09:00, 09:10, ..., 15:20)
        """
        intervals = []
        base_time = datetime.combine(current_date.date(), time(9, 0))  # 09:00
        for i in range(self.scan_intervals_per_day):
            scan_time = base_time + timedelta(minutes=i * 10)
            intervals.append(scan_time)
        return intervals

    def _calculate_position_size(self, df_window: pd.DataFrame, current_price: float, available_cash: float) -> Tuple[int, float]:
        """v14.0: 포지션 사이징 (PositionSizer 사용)"""
        try:
            atr = strategy.calculate_atr(df_window, period=14)
            if not atr:
                atr = current_price * 0.02 # Fallback
            
            # 포트폴리오 가치 추정 (직전일 Equity - 현재 현금)
            # 정확한 실시간 가치는 아니지만 백테스트 목적상 충분
            last_equity = self.equity_curve[-1][1] if self.equity_curve else self.capital
            portfolio_value = max(0, last_equity - available_cash)
            
            result = self.position_sizer.calculate_quantity(
                stock_code="BACKTEST", 
                stock_price=current_price,
                atr=atr,
                account_balance=available_cash,
                portfolio_value=portfolio_value
            )
            
            qty = result['quantity']
            cost = qty * current_price
            
            return qty, cost
            
        except Exception as e:
            logger.error(f"포지션 사이징 오류: {e}")
            return 0, 0.0

    def _check_diversification(self, signal: dict, quantity: int, price: float, total_assets: float, current_date: datetime = None, regime: str = None) -> Tuple[bool, dict]:
        """[업그레이드] 분산 투자 원칙 검증"""
        try:
            # [v16.5] Dynamic Sector Limits (강세장 대응)
            # 기본 설정
            max_sector_pct = self.config.get_float('MAX_SECTOR_PCT', 30.0)
            max_stock_pct = self.config.get_float('MAX_POSITION_VALUE_PCT', 10.0)
            
            # Strong Bull일 때 한도 상향
            if regime == MarketRegimeDetector.REGIME_STRONG_BULL:
                max_sector_pct = 50.0  # 30% -> 50%
                max_stock_pct = 20.0   # 10% -> 20%
                # logger.debug(f"🔥 Strong Bull: Dynamic Limits Applied (Sector: {max_sector_pct}%, Stock: {max_stock_pct}%)")

            # [Optimization] 캐시된 포트폴리오 정보 사용
            portfolio_cache = self.portfolio_info_cache

            # [v16.6] Stock Name Lookup for Sector Classification
            # backtest.py에서는 signal에 이름이 포함되지 않는 경우가 많으므로 (code만 있음)
            # 미리 로드한 self.stock_names에서 이름을 조회하여 전달
            stock_name = self.stock_names.get(signal['code'], signal['code'])
            
            candidate_stock = {'code': signal['code'], 'name': stock_name, 'quantity': quantity, 'price': price}
            
            # DiversificationChecker에 동적 한도 전달
            # shared/portfolio_diversification.py가 업데이트되어 override 인자를 받는지 확인 필요
            # 일단 kwargs로 전달 (받지 않으면 무시되거나 에러 날 수 있음 - shared 업데이트 필요)
            # 하지만 안전하게 호출하기 위해 try-except 블록 내에 있음
            
            result = self.diversification_checker.check_diversification(
                candidate_stock=candidate_stock,
                portfolio_cache=portfolio_cache,
                account_balance=self.cash,
                override_max_sector_pct=max_sector_pct,
                override_max_stock_pct=max_stock_pct
            )
            logger.info(f"분산 투자 result: {result}")
            return result.get('approved', False), result
        except Exception as e:
            logger.debug(f"분산 투자 검증 중 오류: {e}")
            return False, {} # 오류 시 매수 안함 (보수적)


    def _calculate_ranking_score(self, candidate: dict, regime: str, kospi_window: pd.DataFrame = None) -> float:
        """
        v14.0: 종합 랭킹 스코어 계산 (FactorScorer 사용)
        
        Args:
            candidate: 후보 종목 정보 (signal, df_window, last_close, rsi 등)
            regime: 현재 시장 상황
            kospi_window: KOSPI 가격 데이터 (모멘텀 계산용)
        
        Returns:
            0~100 종합 점수
        """
        try:
            stock_code = candidate.get("code")
            df_window = candidate["df_window"]
            current_date = candidate.get("current_date")
            
            # 재무 데이터 로드 (Historical)
            stock_info = {}
            # [Optimization] Use pre-loaded data
            financial_data = self._get_financial_data(stock_code, current_date)
            stock_info.update(financial_data)
            
            # 팩터 점수 계산
            momentum_score, _ = self.factor_scorer.calculate_momentum_score(df_window, kospi_window)
            
            roe = stock_info.get('roe')
            
            quality_score, _ = self.factor_scorer.calculate_quality_score(
                roe=roe,
                sales_growth=stock_info.get('sales_growth'),
                eps_growth=stock_info.get('eps_growth'),
                daily_prices_df=df_window
            )

            
            value_score, _ = self.factor_scorer.calculate_value_score(
                pbr=None, # 현재 load_financial_data에서 PBR 미제공
                per=None  # 현재 load_financial_data에서 PER 미제공
            )
            
            technical_score, _ = self.factor_scorer.calculate_technical_score(df_window)
            
            # 최종 점수 (시장 상황별 가중치 적용)
            final_score, weight_info = self.factor_scorer.calculate_final_score(
                momentum_score, quality_score, value_score, technical_score, regime
            )
            
            # 1000점 만점을 100점 만점으로 변환
            return final_score / 10.0
            
        except Exception as e:
            if self.diagnose_mode:
                logger.debug(f"랭킹 점수 계산 오류: {e}")
            return 50.0
    
    def _check_liquidity_filter(self, df_window: pd.DataFrame, min_avg_volume: float = 100_000) -> bool:
        """v12.1: 유동성 필터 (최근 20일 평균 거래대금 기준) - 대폭 완화: 50만원 -> 10만원"""
        try:
            if "VOLUME" not in df_window.columns:
                return False
            # 데이터가 20일 미만이면 사용 가능한 일수만 사용
            lookback_days = min(20, len(df_window))
            if lookback_days < 5:  # 최소 5일은 필요
                return False
            recent_volumes = df_window["VOLUME"].tail(lookback_days)
            recent_prices = df_window["CLOSE_PRICE"].tail(lookback_days)
            avg_turnover = (recent_volumes * recent_prices).mean()

            # v12.1: 디버깅용 로깅 (처음 10개만)
            if self.diagnose_mode and not hasattr(self, '_liquidity_log_count'):
                self._liquidity_log_count = 0
            if self.diagnose_mode and self._liquidity_log_count < 10:
                logger.debug(f"유동성 필터: 평균 거래대금 {avg_turnover:,.0f}원 (최소 {min_avg_volume:,.0f}원 필요)")
                self._liquidity_log_count += 1
            return avg_turnover >= min_avg_volume
        except Exception as e:
            if self.diagnose_mode:
                logger.debug(f"유동성 필터 계산 오류: {e}")
            return False
    
    def _calculate_position_size(self, df_window: pd.DataFrame, current_price: float, available_cash: float) -> Tuple[int, float]:
        """v14.2: 자산 증식 목표 - 동적 포지션 사이징 (가용 현금 최대한 활용)"""
        try:
            # v14.2: 전체 자산 기준으로 계산 (현재 날짜는 run()에서 사용 가능하므로 임시로 현재 시점 사용)
            # 실제로는 current_date를 파라미터로 받아야 하지만, 호출 시점에서 사용 가능한 값 사용
            current_equity = self._compute_equity(datetime.now()) if hasattr(self, 'price_cache') and self.price_cache else INITIAL_CAPITAL
            current_equity = max(current_equity, INITIAL_CAPITAL)  # 최소 초기 자본
            
            atr_val = strategy.calculate_atr(df_window, period=self.config.get_int('ATR_PERIOD', 14))
            if not atr_val or atr_val == 0:
                # ATR 없으면 전체 자산의 2% 할당 (1.5억원 기준 300만원)
                base_amount = current_equity * 0.02
            else:
                # 변동성 기반: ATR의 2배를 리스크로 가정, 전체 자산의 1%를 리스크로 할당
                risk_per_trade = current_equity * 0.01  # 1.5억원의 1% = 150만원
                position_size = risk_per_trade / (atr_val * 2.0)
                base_amount = position_size * current_price
                # 최소 150만원, 최대 포지션 사이즈는 ConfigManager에서 가져옴
                max_position_pct = self.config.get_float('MAX_POSITION_PCT', 5.0)
                base_amount = max(1_500_000, min(current_equity * (max_position_pct / 100.0), base_amount))
            
            # 가용 현금을 최대한 활용 (현금 유지 비율은 ConfigManager에서 가져옴)
            cash_keep_pct = self.config.get_float('CASH_KEEP_PCT', 5.0)
            max_usable_cash = available_cash * (1.0 - cash_keep_pct / 100.0)
            base_amount = min(base_amount, max_usable_cash)
            
            # 가용 현금 비율에 따라 조정
            cash_ratio = available_cash / current_equity if current_equity > 0 else 1.0
            if cash_ratio > 0.3:
                # 현금이 많으면 더 적극적으로 (최대 2.0배)
                base_amount *= min(2.0, 1.0 + (cash_ratio - 0.3) * 2.0)
            elif cash_ratio < 0.05:
                # 현금이 적으면 보수적으로
                base_amount *= 0.8
            
            # 원래대로 복원: 랭킹 점수 기반 포지션 사이징 가중치 제거
            
            qty = max(1, math.floor(base_amount / current_price))
            cost = current_price * qty
            return qty, cost
        except Exception as e:
            # 폴백: 가용 현금의 5% 사용
            base_amount = available_cash * 0.05
    def _process_buys_hybrid(self, current_date, scan_time, interval_idx, active_strategies, kospi_window, regime, daily_universe) -> int:
        """
        v13.0: 하이브리드 모드용 매수 처리 (Optimized)
        
        가상 실시간 가격을 사용하여 매수 신호를 스캔하고, 신호 발생 시 매수합니다.
        [Optimization] Dataframe Slicing 및 Indicator 재계산을 제거하고 Pre-calculated Value를 사용합니다.
        
        Returns:
            매수한 종목 수
        """
        buys_count = 0
        
        # [v16.3] Universe Expansion & Tiered Strategy
        # daily_universe는 이제 'Watchlist'에 있는 종목들만 의미함
        # 전체 종목을 대상으로 스캔하되, Watchlist에 없는 종목(Tier 2)은 현금 여유가 있을 때만 매수
        
        watchlist_set = set(daily_universe)
        
        # 전체 가용 종목에 대해 반복
        # self.all_prices_cache.keys()에는 KOSPI_CODE도 포함되므로 제외 필요
        # available_stocks는 run()에서 정의되지만 여기서는 접근 불가하므로 직접 필터링
        
        for code, df in self.all_prices_cache.items():
            if code == KOSPI_CODE:
                continue
            
            # [Tier 2 조건] Watchlist에 없는 종목은 현금 비중이 30% 이상일 때만 매수 시도
            is_tier1 = code in watchlist_set
            if not is_tier1:
                total_assets = self._compute_equity(current_date)
                cash_ratio = self.cash / total_assets
                if cash_ratio < 0.3:
                    continue # 현금 부족 시 Tier 2 스킵
                # logger.debug(f"🔍 Tier 2 Stock Scan: {code} (Cash Ratio: {cash_ratio:.2f})") # 너무 많을 수 있으므로 주석
            
            if code in self.portfolio:
                continue
            if buys_count >= self.config.get_int('MAX_BUYS_PER_DAY', 100):
                break
            
            # [Optimization] Direct Lookup (No Slicing)
            try:
                if current_date not in df.index:
                    continue
                
                # 현재 행과 이전 행 가져오기 (Golden Cross용)
                # df.index는 datetime 객체여야 함 (Preload에서 처리됨)
                idx = df.index.get_loc(current_date)
                if isinstance(idx, slice): # 중복 날짜 방지
                    idx = idx.stop - 1
                
                row = df.iloc[idx]
                
                # 데이터 부족 체크 (최소 20일)
                if idx < 20:
                    continue
                
                prev_row = df.iloc[idx-1]
                
            except Exception:
                continue
            
            # 가상 실시간 가격 생성 (df_window 대신 row 정보 사용)
            # _generate_virtual_intraday_price는 원래 df_window를 받았으나, 
            # 내부적으로 last_row만 사용하므로 row로 대체 가능하도록 수정하거나,
            # 여기서 직접 로직을 구현하는 것이 빠름.
            
            # Inline _generate_virtual_intraday_price logic for speed
            day_open = row.get("OPEN_PRICE") or row["CLOSE_PRICE"]
            day_high = float(row["HIGH_PRICE"])
            day_low = float(row["LOW_PRICE"])
            day_close = float(row["CLOSE_PRICE"])
            
            progress = interval_idx / (self.scan_intervals_per_day - 1)
            deterministic_factor = math.sin(interval_idx * 0.5) * 0.005
            
            if progress < 0.5:
                base_price = day_low + (day_close - day_low) * (progress * 2)
            else:
                afternoon_progress = (progress - 0.5) * 2
                if afternoon_progress < 0.7:
                    base_price = day_close + (day_high - day_close) * (afternoon_progress / 0.7)
                else:
                    base_price = day_high - (day_high - day_close) * ((afternoon_progress - 0.7) / 0.3)
            
            virtual_price = base_price * (1 + deterministic_factor)
            virtual_price = max(day_low, min(day_high, virtual_price))
            
            if virtual_price <= 0:
                continue
            
            # Pre-calculated Indicators Lookup
            last_close = day_close
            last_volume = float(row["VOLUME"])
            rsi_current = row['RSI']
            atr_val = row['ATR']
            
            buy_signal_type = None
            key_metrics = {}
            
            for stype in active_strategies:
                if stype == StrategySelector.STRATEGY_MEAN_REVERSION:
                    bb_lower = row['BB_LOWER']
                    
                    # 가상 가격이 BB 하단을 터치했는지 확인
                    if not pd.isna(bb_lower) and virtual_price <= bb_lower:
                        buy_signal_type = "BB_LOWER"
                        key_metrics = {"close": last_close, "virtual_price": virtual_price, "bb_lower": bb_lower, "rsi": rsi_current}
                        break
                    
                    # Agent 동기화: BULL 시장에서 BB 하단 2% 이내 근접 신호
                    if not pd.isna(bb_lower) and regime == MarketRegimeDetector.REGIME_BULL:
                        bb_distance_pct = ((virtual_price - bb_lower) / bb_lower) * 100
                        if bb_distance_pct <= 2.0:
                            buy_signal_type = "BB_LOWER_NEAR"
                            key_metrics = {"close": last_close, "virtual_price": virtual_price, "bb_lower": bb_lower, "bb_distance_pct": bb_distance_pct, "rsi": rsi_current}
                            break
                            
                    # Agent 동기화: RSI 과매도 (BULL 시장 대응: 기준 완화)
                    if not pd.isna(rsi_current):
                        rsi_threshold = self.config.get_int('BUY_RSI_OVERSOLD_THRESHOLD', 30)
                        if regime == MarketRegimeDetector.REGIME_BULL:
                            rsi_threshold = 40
                        if rsi_current <= rsi_threshold:
                            buy_signal_type = "RSI_OVERSOLD"
                            key_metrics = {"rsi": rsi_current, "rsi_threshold": rsi_threshold, "virtual_price": virtual_price}
                            break
                
                elif stype == StrategySelector.STRATEGY_TREND_FOLLOWING:
                    # Golden Cross: MA5 > MA20 (Pre-calculated)
                    ma5 = row['MA_5']
                    ma20 = row['MA_20']
                    prev_ma5 = prev_row['MA_5']
                    prev_ma20 = prev_row['MA_20']
                    
                    if not pd.isna(ma5) and not pd.isna(ma20) and not pd.isna(prev_ma5) and not pd.isna(prev_ma20):
                        if ma5 > ma20 and prev_ma5 <= prev_ma20:
                            buy_signal_type = "GOLDEN_CROSS"
                            key_metrics = {"signal": "GOLDEN_CROSS_5_20", "rsi": rsi_current, "virtual_price": virtual_price}
                            break
                    
                    # Resistance Breakout (Pre-calculated RES_20)
                    res_level = row.get('RES_20')
                    if not pd.isna(res_level) and virtual_price > res_level:
                        buy_signal_type = "RESISTANCE_BREAKOUT"
                        key_metrics = {"resistance": res_level, "close": last_close, "virtual_price": virtual_price, "rsi": rsi_current}
                        break
                        
                    # Agent 동기화: BULL 시장 대응 - 상승 추세 지속 확인 (MA 정배열)
                    # 단기 이평선이 장기 이평선 위에 있고, 둘 다 상승 중 (3일 전 대비)
                    if regime == MarketRegimeDetector.REGIME_BULL and idx >= 3:
                        ma5_3ago = df.iloc[idx-3]['MA_5']
                        ma20_3ago = df.iloc[idx-3]['MA_20']
                        
                        if (ma5 > ma20 and 
                            ma5 > ma5_3ago and 
                            ma20 > ma20_3ago):
                            buy_signal_type = "TREND_UPWARD"
                            key_metrics = {
                                "short_ma": ma5,
                                "long_ma": ma20,
                                "rsi": rsi_current,
                                "virtual_price": virtual_price
                            }
                            break
                
                elif stype == StrategySelector.STRATEGY_VOLUME_MOMENTUM:
                    # [v15.0] 듀얼 모멘텀 + 거래량 돌파
                    # 1. 장기 추세 확인 (120일 이평선)
                    ma_120 = row.get("MA_120", 0)
                    if pd.isna(ma_120) or ma_120 == 0 or virtual_price < ma_120:
                        continue 
                    
                    # 2. 거래량 폭발 확인 (20일 평균 대비 2배)
                    vol_ma_20 = row.get("VOL_MA_20", 0)
                    if pd.isna(vol_ma_20) or vol_ma_20 == 0 or last_volume < (vol_ma_20 * 2.0):
                        continue 
                        
                    # 3. 6개월 모멘텀 확인 (120일 전 대비 수익률)
                    momentum_ok = True
                    if idx >= 120:
                        price_120_ago = float(df.iloc[idx-120]["CLOSE_PRICE"])
                        if price_120_ago > 0:
                            momentum_6m = ((virtual_price - price_120_ago) / price_120_ago) * 100
                            if momentum_6m <= 0:
                                momentum_ok = False
                    
                    if momentum_ok:
                        buy_signal_type = "VOLUME_MOMENTUM"
                        key_metrics = {
                            "close": last_close,
                            "virtual_price": virtual_price,
                            "ma_120": ma_120,
                            "vol_current": last_volume,
                            "vol_ma_20": vol_ma_20,
                            "momentum_6m": momentum_6m if 'momentum_6m' in locals() else 0
                        }
                        break
            
            if not buy_signal_type:
                continue
            
            # [Refactor] _execute_buy_signal 사용 (Smart Skip, Dynamic Limits 적용)
            signal = {
                'code': code,
                'name': code,
                'type': buy_signal_type,
                'price': virtual_price,
                'time': scan_time,
                'metrics': key_metrics
            }
            
            # Execute Buy
            is_bought, cost = self._execute_buy_signal(signal, regime)
            if is_bought:
                buys_count += 1
                
                # [v16.4] Tier 2 종목 매수 시 로깅
                if not is_tier1:
                    logger.info(f"✨ Tier 2 Stock Bought: {code} (Strategy: {buy_signal_type})")
                
                # 하루 최대 매수 제한 체크
                if buys_count >= self.config.get_int('MAX_BUYS_PER_DAY', 100):
                    return buys_count
        
        return buys_count
    
    def _process_buys(self, current_date, active_strategies, kospi_window, regime, daily_universe):
        kospi_for_rs = kospi_window[["PRICE_DATE", "CLOSE_PRICE"]].copy()
        kospi_for_rs = kospi_for_rs.rename(columns={"CLOSE_PRICE": "CLOSE_PRICE"})

        # v11.0: 랭킹 기반 매수 + 진단 모드
        buys_today = 0
        candidates: List[dict] = []
        liquidity_filtered_count = 0
        signal_not_found_count = 0
        data_insufficient_count = 0
        already_in_portfolio_count = 0

        # Phase 1 롤백: 최근 매도 종목 필터 완전 제거
        recently_sold_codes = set()

        # [진단 로그] 스캔 시작
        logger.debug(f"   (Buy Scan) {current_date.date()}: {len(daily_universe)}개 종목 스캔 시작...")

        # 1) 후보 스캔 (병렬 처리)
        with ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as executor:
            future_to_code = {executor.submit(self._scan_single_stock_for_buy, code, self.all_prices_cache[code], current_date, active_strategies, regime): code for code in daily_universe if code in self.all_prices_cache}
            for future in as_completed(future_to_code):
                result = future.result()
                if not result:
                    continue
                
                code = future_to_code[future]
                if result.get("is_candidate"):
                    candidate = result["candidate"]
                    candidate["ranking_score"] = self._calculate_ranking_score(candidate, regime, kospi_window)
                    candidates.append(candidate)
                
                # 통계 업데이트
                if result.get("is_already_in_portfolio"):
                    already_in_portfolio_count += 1
                elif result.get("is_data_insufficient"):
                    data_insufficient_count += 1
                elif result.get("is_liquidity_filtered"):
                    liquidity_filtered_count += 1
                elif not result.get("is_candidate"):
                    signal_not_found_count += 1

                # 진단 모드 기록
                if self.diagnose_mode and result.get("diagnose_record"):
                    self.diagnose_records.append(result["diagnose_record"])

        # [진단 로그] 스캔 결과 요약
        logger.debug(f"   (Buy Scan) 스캔 완료: 후보 {len(candidates)}개 발견")
        if len(candidates) == 0:
            logger.debug(f"   (Buy Scan) ⚠️ 매수 후보 없음. 필터링 요약:")
            logger.debug(f"     - 데이터 부족: {data_insufficient_count}개")
            logger.debug(f"     - 유동성 부족: {liquidity_filtered_count}개")
            logger.debug(f"     - 신호 미발견: {signal_not_found_count}개")
            logger.debug(f"     - 기보유 종목: {already_in_portfolio_count}개")
        
        # 2) 랭킹: 랭킹 스코어 내림차순
        if candidates:
            candidates.sort(key=lambda c: c["ranking_score"], reverse=True)
            # [진단 로그] 상위 후보 로깅
            top_3 = candidates[:3]
            logger.debug(f"   (Buy Scan) 상위 3개 후보:")
            for i, c in enumerate(top_3):
                logger.debug(f"     {i+1}. {c['code']}: 점수 {c['ranking_score']:.2f} (신호: {c['signal']})")

        # 3) 상위 N 매수 실행
        for cand in candidates:
            if buys_today >= self.config.get_int('MAX_BUYS_PER_DAY', 1):
                break
            code = cand["code"]
            last_close = cand["last_close"]
            df_window = cand["df_window"]
            buy_signal_type = cand["signal"]
            key_metrics = cand["key_metrics"]

            qty, cost = self._calculate_position_size(df_window, last_close, self.cash)
            if cost > self.cash:
                logger.warning(f"   (Buy Execute) ⚠️ {code} 매수 건너뜀 (현금 부족: 필요 {cost:,.0f} > 보유 {self.cash:,.0f})")
                continue

            last_high = float(df_window["HIGH_PRICE"].iloc[-1])
            buy_price_with_slippage = last_high * 1.00115
            actual_cost = buy_price_with_slippage * qty
            if actual_cost > self.cash:
                logger.warning(f"   (Buy Execute) ⚠️ {code} 매수 건너뜀 (슬리피지 적용 후 현금 부족: 필요 {actual_cost:,.0f} > 보유 {self.cash:,.0f})")
                continue

            atr_val = strategy.calculate_atr(df_window, period=self.config.get_int('ATR_PERIOD', 14))
            if regime == MarketRegimeDetector.REGIME_STRONG_BULL:
                atr_mult = self.config.get_float('STRONG_BULL_ATR_MULTIPLIER_INITIAL', 2.5)
            else:
                atr_mult = self.config.get_float('ATR_MULTIPLIER_INITIAL_STOP', 2.0)
            stop_loss = buy_price_with_slippage - (atr_val * atr_mult) if atr_val else buy_price_with_slippage * 0.93

            logger.info(f"   (Buy Execute) ✅ 최종 매수 결정: {code} {qty}주 @ {buy_price_with_slippage:,.0f}원")

            self.cash -= actual_cost
            self.portfolio[code] = {
                "code": code,
                "name": code,
                "quantity": qty,
                "avg_price": buy_price_with_slippage,
                "high_price": buy_price_with_slippage,
                "sell_state": "INITIAL",
                "stop_loss_price": stop_loss,
                "buy_date": current_date,
                "buy_signal": buy_signal_type,
                "sold_ratio": 0.0,
                "original_quantity": qty,
            }
            
            if self.diagnose_mode:
                if buy_signal_type not in self.signal_hit_stats:
                    self.signal_hit_stats[buy_signal_type] = {"hits": 0, "total": 0, "total_return": 0.0, "total_days": 0}
                self.signal_hit_stats[buy_signal_type]["total"] += 1
            append_backtest_tradelog(
                self.connection, current_date, code, code, "BUY", qty, buy_price_with_slippage,
                f"BUY via {buy_signal_type} (슬리피지 포함)", buy_signal_type, str(key_metrics), regime
            )
            buys_today += 1

    def _scan_single_stock_for_buy(self, code, df, current_date, active_strategies, regime):
        """_process_buys의 for 루프 내부 로직을 병렬 처리를 위해 별도 함수로 분리 (Optimized)"""
        if code in self.portfolio:
            return {"is_already_in_portfolio": True}
        
        # [Optimization] Direct lookup
        try:
            # df는 이미 PRICE_DATE가 인덱스임
            if current_date not in df.index:
                return {"is_data_insufficient": True}
            row = df.loc[current_date]
            idx = df.index.get_loc(current_date)
            if isinstance(idx, slice):
                idx = idx.stop - 1
        except Exception:
            return {"is_data_insufficient": True}
            
        # Liquidity Filter
        vol_ma_20 = row.get('VOL_MA_20', 0)
        current_close = row['CLOSE_PRICE']
        if pd.isna(vol_ma_20) or (vol_ma_20 * current_close) < 100000:
             return {"is_liquidity_filtered": True}

        buy_signal_type = None
        key_metrics = {}
        
        rsi_current = row['RSI']
        atr_val = row['ATR']
        last_close = row['CLOSE_PRICE']
        last_volume = row['VOLUME']
        
        for stype in active_strategies:
            if stype == StrategySelector.STRATEGY_MEAN_REVERSION:
                bb_lower = row['BB_LOWER']
                last_low = row['LOW_PRICE']
                
                if not pd.isna(bb_lower) and last_low <= bb_lower:
                    buy_signal_type = "BB_LOWER"
                    key_metrics = {"close": last_close, "low": last_low, "bb_lower": bb_lower, "rsi": rsi_current}
                    break
                
                if not pd.isna(bb_lower) and regime == MarketRegimeDetector.REGIME_BULL:
                    bb_distance_pct = ((last_close - bb_lower) / bb_lower) * 100
                    if bb_distance_pct <= 2.0:
                        buy_signal_type = "BB_LOWER_NEAR"
                        key_metrics = {"close": last_close, "bb_lower": bb_lower, "bb_distance_pct": bb_distance_pct, "rsi": rsi_current}
                        break
                        
                if not pd.isna(rsi_current):
                    rsi_threshold = self.config.get_int('BUY_RSI_OVERSOLD_THRESHOLD', 30)
                    if regime == MarketRegimeDetector.REGIME_BULL:
                        rsi_threshold = 40
                    if rsi_current <= rsi_threshold:
                        buy_signal_type = "RSI_OVERSOLD"
                        key_metrics = {"rsi": rsi_current, "rsi_threshold": rsi_threshold}
                        break

            elif stype == StrategySelector.STRATEGY_TREND_FOLLOWING:
                # Golden Cross: MA5 > MA20
                ma5 = row['MA_5']
                ma20 = row['MA_20']
                
                if idx > 0:
                    prev_row = df.iloc[idx-1]
                    prev_ma5 = prev_row['MA_5']
                    prev_ma20 = prev_row['MA_20']
                    
                    if not pd.isna(ma5) and not pd.isna(ma20) and not pd.isna(prev_ma5) and not pd.isna(prev_ma20):
                        if ma5 > ma20 and prev_ma5 <= prev_ma20:
                            buy_signal_type = "GOLDEN_CROSS"
                            key_metrics = {"signal": "GOLDEN_CROSS_5_20", "rsi": rsi_current}
                            break
                
                # Resistance Breakout (20일 고점 돌파)
                if idx >= 20:
                    # 최근 20일 (오늘 제외) 고점
                    recent_highs = df['HIGH_PRICE'].iloc[idx-20:idx]
                    res_level = recent_highs.max()
                    last_high = row['HIGH_PRICE']
                    
                    if last_high > res_level:
                        buy_signal_type = "RESISTANCE_BREAKOUT"
                        key_metrics = {"resistance": res_level, "close": last_close, "high": last_high, "rsi": rsi_current}
                        break
                
                # Trend Upward
                if regime == MarketRegimeDetector.REGIME_BULL and idx >= 20:
                    ma5 = row['MA_5']
                    ma20 = row['MA_20']
                    ma5_3 = df.iloc[idx-3]['MA_5'] if idx >= 3 else 0
                    ma20_3 = df.iloc[idx-3]['MA_20'] if idx >= 3 else 0
                    
                    if (ma5 > ma20 and ma5 > ma5_3 and ma20 > ma20_3):
                         buy_signal_type = "TREND_UPWARD"
                         key_metrics = {"short_ma": ma5, "long_ma": ma20, "rsi": rsi_current}
                         break

        if not buy_signal_type:
            return {"is_candidate": False}

        # Candidate found!
        df_window = df.iloc[:idx+1]
        
        candidate = {
            "code": code,
            "last_close": last_close,
            "last_volume": last_volume,
            "rsi": rsi_current if not pd.isna(rsi_current) else -1,
            "df_window": df_window,
            "signal": buy_signal_type,
            "key_metrics": key_metrics,
            "atr": atr_val,
            "current_date": current_date,
        }
        
        diagnose_record = None
        if self.diagnose_mode:
            diagnose_record = {
                "date": current_date, "code": code, "signal": buy_signal_type,
                "price": last_close, "rsi": rsi_current, "volume": last_volume,
                "atr": atr_val, "regime": regime,
            }

        return {"is_candidate": True, "candidate": candidate, "diagnose_record": diagnose_record}

    def _process_sells(self, current_date, regime):
        """
        [Optimization] 순차적 매도 처리 (ThreadPool 제거)
        매도 발생 시 포트폴리오 및 캐시를 즉시 업데이트합니다.
        """
        # 매도 대상 식별 (순차 처리)
        to_sell = []
        for code, pos in list(self.portfolio.items()): # dict 변경 방지를 위해 list로 복사
            result = self._check_single_stock_for_sell(code, pos, current_date, regime)
            if result:
                to_sell.append(result)

        # 매도 실행
        for sell_item in to_sell:
            if len(sell_item) == 5:
                code, price, reason, key, sell_quantity = sell_item
            else:
                code, price, reason, key = sell_item
                sell_quantity = None
            
            pos = self.portfolio.get(code)
            if not pos:
                continue
            
            current_quantity = pos.get("quantity", 0)
            actual_quantity = sell_quantity if sell_quantity is not None and sell_quantity < current_quantity else current_quantity
            if actual_quantity <= 0:
                continue
            
            df = self.all_prices_cache.get(code, pd.DataFrame())
            if df.empty:
                continue
            
            # 매도 가격 결정 (슬리피지 적용)
            # _check_single_stock_for_sell에서 이미 가격을 결정해서 넘겨주면 좋겠지만,
            # 여기서는 로직 유지 (Low Price 기준)
            df_window = self._slice_until_date(df, current_date)
            if not df_window.empty:
                current_low = float(df_window["LOW_PRICE"].iloc[-1])
                sell_price_with_slippage = current_low * 0.99885
            else:
                sell_price_with_slippage = price * 0.99885
            
            proceeds = sell_price_with_slippage * actual_quantity
            self.cash += proceeds
            
            # [Optimization] 포트폴리오 가치 차감
            # 매도 전 가치 (현재가 기준)를 차감해야 함.
            # 하지만 self.current_portfolio_value는 _update_portfolio_cache에서 계산된 값임.
            # 해당 종목의 캐시된 현재가를 가져와서 차감.
            cached_info = self.portfolio_info_cache.get(code)
            if cached_info:
                current_p_price = cached_info.get('current_p_price', sell_price_with_slippage)
                value_reduction = current_p_price * actual_quantity
                self.current_portfolio_value -= value_reduction
            
            if self.diagnose_mode and "buy_signal" in pos:
                buy_signal = pos["buy_signal"]
                buy_price = pos.get("avg_price", sell_price_with_slippage)
                return_pct = ((sell_price_with_slippage - buy_price) / buy_price) * 100.0
                hold_days = (current_date - pos.get("buy_date", current_date)).days if "buy_date" in pos else 0
                
                if buy_signal in self.signal_hit_stats:
                    self.signal_hit_stats[buy_signal]["hits"] += 1
                    self.signal_hit_stats[buy_signal]["total_return"] += return_pct
                    self.signal_hit_stats[buy_signal]["total_days"] += hold_days
            
            if sell_quantity is not None and actual_quantity < current_quantity:
                # 부분 매도
                pos["quantity"] -= actual_quantity
                original_quantity = pos.get("original_quantity", current_quantity)
                if original_quantity <= 0:
                    original_quantity = current_quantity
                sold_ratio_before = pos.get("sold_ratio", 0.0)
                new_sold_ratio = sold_ratio_before + (actual_quantity / original_quantity)
                pos["sold_ratio"] = min(1.0, new_sold_ratio)
                
                # [Optimization] 캐시 업데이트 (수량 변경)
                if code in self.portfolio_info_cache:
                    self.portfolio_info_cache[code]['quantity'] = pos["quantity"]
            else:
                # 전량 매도
                self.portfolio.pop(code, None)
                # [Optimization] 캐시 제거
                self.portfolio_info_cache.pop(code, None)

            append_backtest_tradelog(
                self.connection, current_date, code, code, "SELL", actual_quantity, sell_price_with_slippage,
                f"{reason} (슬리피지 포함)", reason, json.dumps(key), ""
            )
            
            logger.info(f"   (Sell Execute) 📉 매도 실행: {code} {actual_quantity}주 @ {sell_price_with_slippage:,.0f}원 ({reason})")

    def _check_single_stock_for_sell(self, code, pos, current_date, regime):
        """_process_sells의 for 루프 내부 로직을 병렬 처리를 위해 별도 함수로 분리 (Optimized)"""
        df = self.all_prices_cache.get(code, pd.DataFrame())
        if df.empty:
            return None
            
        # [Optimization] Direct lookup
        try:
            if current_date not in df.index:
                return None
            row = df.loc[current_date]
            idx = df.index.get_loc(current_date)
            if isinstance(idx, slice):
                idx = idx.stop - 1
        except Exception:
            return None
            
        if idx < 14: 
             return None

        current_close = row['CLOSE_PRICE']
        atr_val = row['ATR']
        avg_price = pos.get("avg_price", current_close)
        return_pct = ((current_close - avg_price) / avg_price) * 100.0 if avg_price > 0 else 0.0
        current_quantity = pos.get("quantity", 0)
        sold_ratio = pos.get("sold_ratio", 0.0)
        remaining_ratio = 1.0 - sold_ratio

        # [v3.1] 동적 리스크 설정 가져오기
        risk_setting = self.market_regime_detector.get_dynamic_risk_setting(regime)
        
        # [v14.7] 오버라이드 설정 확인 (로그 스팸 방지를 위해 기본값 사용)
        override_stop_loss = self.config.get('OVERRIDE_STOP_LOSS_PCT', 'NO_OVERRIDE')
        override_target_profit = self.config.get('OVERRIDE_TARGET_PROFIT_PCT', 'NO_OVERRIDE')
        
        if override_stop_loss != 'NO_OVERRIDE' and override_stop_loss is not None:
            dynamic_stop_loss_pct = float(override_stop_loss) * 100.0
        else:
            dynamic_stop_loss_pct = risk_setting.get('stop_loss_pct', -0.05) * 100.0
            
        if override_target_profit != 'NO_OVERRIDE' and override_target_profit is not None:
            dynamic_target_profit_pct = float(override_target_profit) * 100.0
        else:
            dynamic_target_profit_pct = risk_setting.get('target_profit_pct', 0.10) * 100.0 

        # [v16.2] Update High Price for Trailing Stop
        high_price = pos.get("high_price", avg_price)
        if current_close > high_price:
            pos["high_price"] = current_close
            high_price = current_close
            
        # 1. ATR 기반 Trailing Stop (우선순위 1)
        stop_loss_price = None
        if "stop_loss_price" in pos:
            stop_loss_price = pos["stop_loss_price"]
        elif "stop_loss_initial" in pos:
            stop_loss_price = pos.get("stop_loss_trailing") or pos["stop_loss_initial"]
        
        if stop_loss_price and current_close <= stop_loss_price:
            key = {"signal": "SELL_STOP_LOSS_ATR", "close": current_close, "stop": stop_loss_price, "atr": atr_val}
            return (code, current_close, "SELL_STOP_LOSS_ATR", key, None)

        # 2. Trailing Stop (High - 2%) (우선순위 1.5)
        # 사용자가 설정한 Trailing Stop 비율이 있으면 사용, 없으면 2% (기존 5% -> 2% 원복)
        trailing_stop_pct = self.config.get_float('TRAILING_STOP_PCT', 0.02)
        if current_close <= high_price * (1 - trailing_stop_pct):
             key = {"signal": "SELL_TRAILING_STOP", "close": current_close, "high": high_price, "drop_pct": trailing_stop_pct*100}
             return (code, current_close, "SELL_TRAILING_STOP", key, None)

        # 3. Regime 기반 동적 손절 (우선순위 2)
        if return_pct <= dynamic_stop_loss_pct:
            key = {"signal": "SELL_STOP_LOSS_DYNAMIC", "return_pct": return_pct, "stop_loss_pct": dynamic_stop_loss_pct, "reason": f"동적 손절매 ({dynamic_stop_loss_pct:.1f}%) 발동"}
            return (code, current_close, "SELL_STOP_LOSS_DYNAMIC", key, None)

        # 3. 목표 수익률 달성 (전량 매도) (우선순위 3)
        if return_pct >= dynamic_target_profit_pct and remaining_ratio >= 0.99:
            key = {"signal": "SELL_PROFIT_TARGET", "return_pct": return_pct, "target_pct": dynamic_target_profit_pct, "reason": f"목표 수익률 {dynamic_target_profit_pct:.1f}% 달성"}
            return (code, current_close, "SELL_PROFIT_TARGET", key, None)

        # 4. RSI 과열 시 분할 매도 (Scale-out)
        can_rsi_take_profit = True
        if "sell_state" in pos:
            can_rsi_take_profit = (pos["sell_state"] != "INITIAL")
        
        if can_rsi_take_profit:
            rsi_current = row['RSI']
            
            if not pd.isna(rsi_current):
                rsi_value = float(rsi_current)
                rsi_threshold_1 = self.config.get_float('RSI_THRESHOLD_1', 70.0)
                rsi_threshold_2 = self.config.get_float('RSI_THRESHOLD_2', 75.0)
                rsi_threshold_3 = self.config.get_float('RSI_THRESHOLD_3', 80.0)
                
                if rsi_value >= rsi_threshold_3 and sold_ratio < 0.8:
                    sell_ratio = 0.2
                    total_sell_ratio = sold_ratio + sell_ratio
                    if total_sell_ratio >= 0.99:
                        key = {"signal": "SELL_TAKE_PROFIT_RSI", "rsi": rsi_value, "reason": f"RSI {rsi_value:.1f} 달성, 전체 매도"}
                        return (code, current_close, "SELL_TAKE_PROFIT_RSI", key, None)
                    else:
                        sell_quantity = max(1, int(current_quantity * sell_ratio))
                        key = {"signal": "SELL_TAKE_PROFIT_RSI_PARTIAL", "rsi": rsi_value, "sell_ratio": sell_ratio, "reason": f"RSI {rsi_value:.1f} 달성, 20% 부분 매도"}
                        return (code, current_close, "SELL_TAKE_PROFIT_RSI_PARTIAL", key, sell_quantity)
                elif rsi_value >= rsi_threshold_2 and sold_ratio < 0.5:
                    sell_ratio = 0.5
                    total_sell_ratio = sold_ratio + sell_ratio
                    if total_sell_ratio >= 0.99:
                        key = {"signal": "SELL_TAKE_PROFIT_RSI", "rsi": rsi_value, "reason": f"RSI {rsi_value:.1f} 달성, 전체 매도"}
                        return (code, current_close, "SELL_TAKE_PROFIT_RSI", key, None)
                    else:
                        sell_quantity = max(1, int(current_quantity * sell_ratio))
                        key = {"signal": "SELL_TAKE_PROFIT_RSI_PARTIAL", "rsi": rsi_value, "sell_ratio": sell_ratio, "reason": f"RSI {rsi_value:.1f} 달성, 50% 부분 매도"}
                        return (code, current_close, "SELL_TAKE_PROFIT_RSI_PARTIAL", key, sell_quantity)
                elif rsi_value >= rsi_threshold_1 and sold_ratio < 0.3:
                    sell_ratio = 0.3
                    total_sell_ratio = sold_ratio + sell_ratio
                    if total_sell_ratio >= 0.99:
                        key = {"signal": "SELL_TAKE_PROFIT_RSI", "rsi": rsi_value, "reason": f"RSI {rsi_value:.1f} 달성, 전체 매도"}
                        return (code, current_close, "SELL_TAKE_PROFIT_RSI", key, None)
                    else:
                        sell_quantity = max(1, int(current_quantity * sell_ratio))
                        key = {"signal": "SELL_TAKE_PROFIT_RSI_PARTIAL", "rsi": rsi_value, "sell_ratio": sell_ratio, "reason": f"RSI {rsi_value:.1f} 달성, 30% 부분 매도"}
                        return (code, current_close, "SELL_TAKE_PROFIT_RSI_PARTIAL", key, sell_quantity)

        # 5. 보유 기간 초과 (Time-based)
        if "entry_date" in pos:
            hold_days = (current_date - pos["entry_date"]).days
            time_based_bull = self.config.get_int('TIME_BASED_BULL', 30)
            time_based_sideways = self.config.get_int('TIME_BASED_SIDEWAYS', 30)
            time_based_threshold = time_based_bull if regime == MarketRegimeDetector.REGIME_BULL else time_based_sideways
            if hold_days >= time_based_threshold:
                key = {"signal": "SELL_TIME_BASED", "hold_days": hold_days, "reason": f"{time_based_threshold}일 보유 후 자동 매도"}
                return (code, current_close, "SELL_TIME_BASED", key, None)

        # Trailing Stop 업데이트 (매도 아님)
        atr_mult_initial = self.config.get_float('ATR_MULTIPLIER_INITIAL_STOP', 2.0)
        atr_mult_trailing = self.config.get_float('ATR_MULTIPLIER_TRAILING_STOP', 1.5)
        
        if "stop_loss_initial" in pos:
            if not pd.isna(atr_val):
                if not pos.get("stop_loss_trailing"):
                    breakeven_trigger = pos["avg_price"] + (atr_val * atr_mult_initial)
                    if current_close >= breakeven_trigger:
                        pos["stop_loss_trailing"] = pos["avg_price"]
                else:
                    current_high = pos.get("high_price", pos["avg_price"])
                    if current_close > current_high:
                        new_stop = current_close - (atr_val * atr_mult_trailing)
                        if new_stop > pos["stop_loss_trailing"]:
                            pos["stop_loss_trailing"] = new_stop
                            pos["high_price"] = current_close
                    elif "high_price" not in pos:
                        pos["high_price"] = current_close
        
        return None

def main():
    # .env 파일 명시적 로드 (auto_optimize_backtest.py에서 실행될 때를 위함)
    project_root_for_env = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_path = os.path.join(project_root_for_env, '.env')
    load_dotenv(dotenv_path=env_path)

    parser = argparse.ArgumentParser(description="my-little-jennie v10.x backtester")
    parser.add_argument("--max-buys-per-day", type=int, default=100, help="Maximum number of buys per day (v14.2: 기본값 100, 거의 제한 없음)")
    parser.add_argument("--ignore-bear-on-strong-bull", action="store_true", default=True)
    parser.add_argument("--no-ignore-bear-on-strong-bull", dest="ignore_bear_on_strong_bull", action="store_false")
    parser.add_argument("--sb-mom-th", type=float, default=2.0, help="STRONG_BULL momentum threshold (%%)")
    parser.add_argument("--sb-rs-th", type=float, default=1.0, help="STRONG_BULL relative strength threshold (%%p)")
    parser.add_argument("--sb-rsi", type=int, default=85, help="STRONG_BULL RSI take-profit threshold")
    parser.add_argument("--sb-atr-init", type=float, default=2.5, help="STRONG_BULL initial stop ATR multiplier")
    parser.add_argument("--sb-atr-trail", type=float, default=2.0, help="STRONG_BULL trailing stop ATR multiplier")
    parser.add_argument("--result-file", type=str, default=os.path.join(PROJECT_ROOT, "backtest.result.txt"))
    # v11.0: 진단 모드
    parser.add_argument("--diagnose", action="store_true", help="Enable diagnosis mode (CSV logging + hit rate report)")
    parser.add_argument("--diagnose-csv", type=str, default=os.path.join(PROJECT_ROOT, "backtest.diagnose.csv"), help="Diagnosis CSV output path")
    # v13.0: 하이브리드 모드 (일봉 + 10분 간격 스캔 시뮬레이션)
    parser.add_argument("--hybrid", action="store_true", help="Enable hybrid mode: simulate 10-minute interval scans using daily data")
    # v10.7: optimization mode (RSI 튜닝)
    parser.add_argument("--optimize", action="store_true", help="Run grid search over RSI thresholds")
    parser.add_argument("--opt-buy-rsi-min", type=int, default=15)
    parser.add_argument("--opt-buy-rsi-max", type=int, default=35)
    parser.add_argument("--opt-buy-rsi-step", type=int, default=5)
    parser.add_argument("--opt-sell-rsi-min", type=int, default=70)
    parser.add_argument("--opt-sell-rsi-max", type=int, default=90)
    parser.add_argument("--opt-sell-rsi-step", type=int, default=5)
    # 매도 타이밍 파라미터 (최적화용)
    parser.add_argument("--profit-target-full", type=float, default=10.0, help="전체 매도 수익률 임계값 (%%)")
    parser.add_argument("--profit-target-partial", type=float, default=5.0, help="부분 매도 수익률 임계값 (%%)")
    parser.add_argument("--rsi-threshold-1", type=float, default=70.0, help="RSI 첫 번째 임계값 (30%% 매도)")
    parser.add_argument("--rsi-threshold-2", type=float, default=75.0, help="RSI 두 번째 임계값 (50%% 매도)")
    parser.add_argument("--rsi-threshold-3", type=float, default=80.0, help="RSI 세 번째 임계값 (20%% 매도)")
    parser.add_argument("--time-based-bull", type=int, default=30, help="BULL 시장 시간 기반 매도 (일)")
    parser.add_argument("--time-based-sideways", type=int, default=30, help="SIDEWAYS 시장 시간 기반 매도 (일)")
    parser.add_argument("--max-position-pct", type=int, default=5, help="[Deprecated] 최대 포지션 사이즈 (%%), --max-position-value-pct 사용 권장")
    parser.add_argument("--cash-keep-pct", type=int, default=5, help="현금 유지 비율 (%%)")
    parser.add_argument("--max-quantity", type=int, default=100, help="종목당 최대 매수 수량")
    parser.add_argument("--max-position-value-pct", type=float, default=10.0, help="단일 종목 최대 비중 (%%)")
    parser.add_argument("--stop-loss-pct", type=float, default=None, help="기본 손절 비율 (예: 0.05 = 5%%, None이면 Regime 사용)")
    parser.add_argument("--target-profit-pct", type=float, default=None, help="기본 익절 비율 (예: 0.10 = 10%%, None이면 Regime 사용)")
    parser.add_argument("--smart-universe", action="store_true", help="Use Smart Universe (Top 200 Liquid+Momentum) instead of Watchlist")
    parser.add_argument('--log-mode', type=str, default='stream', choices=['stream', 'buffered', 'quiet'], help='Logging mode: stream (default), buffered (fast file io), quiet (minimal output)')
    parser.add_argument('--log-file', type=str, help='Path to save log file (required for buffered mode)')
    parser.add_argument("--days", type=int, default=None, help="최근 N일간 백테스트 실행")
    args = parser.parse_args()
    
    # 로깅 설정 적용
    setup_logging(args.log_mode, args.log_file)

    logger.info("--- 🤖 백테스트 시작 ---")
    
    db_conn = None
    try:
        # MariaDB 연결 (shared/database.py 사용)
        db_conn = database.get_db_connection()
        
        if not db_conn:
            raise RuntimeError("MariaDB 연결 실패")
        ensure_backtest_log_table(db_conn)

        # Smart Universe 로드
        smart_universe_codes = None
        if args.smart_universe:
            universe_path = os.path.join(PROJECT_ROOT, "smart_universe.json")
            if os.path.exists(universe_path):
                import json
                with open(universe_path, 'r', encoding='utf-8') as f:
                    universe_data = json.load(f)
                    smart_universe_codes = [item['code'] for item in universe_data]
                logger.info(f"🌌 Smart Universe 모드: {len(smart_universe_codes)}개 종목 로드 완료")
            else:
                logger.error(f"❌ Smart Universe 파일({universe_path})이 없습니다. generate_smart_universe.py를 먼저 실행하세요.")
                sys.exit(1)

        if args.optimize:
            logger.info("=== v10.7 최적화 모드: RSI Grid Search 시작 ===")
            buy_rsi_vals = []
            v = args.opt_buy_rsi_min
            while v <= args.opt_buy_rsi_max:
                buy_rsi_vals.append(v)
                v += args.opt_buy_rsi_step
            sell_rsi_vals = []
            v = args.opt_sell_rsi_min
            while v <= args.opt_sell_rsi_max:
                sell_rsi_vals.append(v)
                v += args.opt_sell_rsi_step

            results = []
            total_runs = len(buy_rsi_vals) * len(sell_rsi_vals)
            run_idx = 0
            for buy_rsi in buy_rsi_vals:
                for sell_rsi in sell_rsi_vals:
                    run_idx += 1
                    logger.info(f"[{run_idx}/{total_runs}] BUY_RSI={buy_rsi}, SELL_RSI={sell_rsi} 설정으로 실행")
                    # v14.4: scout-job 호환성을 위해 kwargs로 전달
                    bt = Backtester(db_conn,
                        diagnose_mode=args.diagnose,
                        diagnose_csv_path=args.diagnose_csv if args.diagnose else None,
                        hybrid_mode=args.hybrid,
                    )
                    # Smart Universe 적용
                    if smart_universe_codes:
                        bt.target_codes = smart_universe_codes

                    # 최적화 시에는 ConfigManager를 통해 파라미터 임시 설정
                    bt.config.set('BUY_RSI_OVERSOLD_THRESHOLD', buy_rsi)
                    bt.config.set('SELL_RSI_THRESHOLD', sell_rsi)

                    metrics = bt.run()
                    results.append({
                        "buy_rsi": buy_rsi,
                        "sell_rsi": sell_rsi,
                        "final_equity": metrics["final_equity"],
                        "total_return_pct": metrics["total_return_pct"],
                        "mdd_pct": metrics["mdd_pct"],
                        "rocket_return_pct": metrics["rocket_return_pct"],
                    })

            # 베스트 선택: 로켓장 수익률 내림차순, MDD 오름차순
            # 로켓장 수익률 None인 항목은 최하위로
            def sort_key(r):
                rocket = r["rocket_return_pct"]
                rocket_sort = -1e9 if rocket is None else rocket
                return (-rocket_sort, r["mdd_pct"])
            results_sorted = sorted(results, key=sort_key)
            best = results_sorted[0] if results_sorted else None

            # 결과 저장
            logger.info(f"=== v10.7 Grid Search 결과 (총 {len(results)}회) ===")
            if best:
                logger.info(f"Best Params -> BUY_RSI: {best['buy_rsi']}, SELL_RSI: {best['sell_rsi']}")
                logger.info(f"Best Rocket Return: {best['rocket_return_pct']:.2f}%")
                logger.info(f"Best Total Return: {best['total_return_pct']:.2f}%")
                logger.info(f"Best MDD: {best['mdd_pct']:.2f}%")
            logger.info(" ")
            logger.info("BUY_RSI,SELL_RSI,ROCKET_RET(%),TOTAL_RET(%),MDD(%)")
            for r in results_sorted:
                rr = "" if r["rocket_return_pct"] is None else f"{r['rocket_return_pct']:.2f}"
                logger.info(f"{r['buy_rsi']},{r['sell_rsi']},{rr},{r['total_return_pct']:.2f},{r['mdd_pct']:.2f}")
            logger.info("=== v10.7 최적화 모드 완료 ===")
        else:
            # v14.4: scout-job 호환성을 위해 kwargs로 전달
            # Backtester 인스턴스 생성
            backtester = Backtester(
                db_conn, 
                diagnose_mode=args.diagnose,
                diagnose_csv_path=args.diagnose_csv if args.diagnose else None,
                hybrid_mode=args.hybrid,
                smart_universe=args.smart_universe
            )
            
            # [v14.7] CLI 인자 -> Config 오버라이드
            if args.days:
                backtester.days = args.days
            # Smart Universe 적용
            if smart_universe_codes:
                backtester.target_codes = smart_universe_codes

            # 기본값 설정 (DB Config가 없을 경우 대비)
            # 기본값 설정 (DB Config가 없을 경우 대비)
            backtester.config.set('MAX_BUYS_PER_DAY', 100) # 제한 없음
            backtester.config.set('PROFIT_TARGET_FULL', 10.0) # 10% 도달 시 전량 매도
            backtester.config.set('PROFIT_TARGET_PARTIAL', 5.0) # 5% 도달 시 부분 매도
            backtester.config.set('RSI_THRESHOLD_1', 65.0) # [Restored] 1차 RSI 매도 기준 (기존 70 -> 65)
            backtester.config.set('RSI_THRESHOLD_2', 80.0) # [Restored] 2차 RSI 매도 기준 (기존 75 -> 80)
            backtester.config.set('RSI_THRESHOLD_3', 80.0) # 3차 RSI 매도 기준
            backtester.config.set('TIME_BASED_BULL', 30) # 강세장 보유 기간
            backtester.config.set('TIME_BASED_SIDEWAYS', 30) # 횡보장 보유 기간
            backtester.config.set('MAX_POSITION_PCT', 5) # 종목당 최대 비중 5%
            backtester.config.set('CASH_KEEP_PCT', 5) # 현금 보유 비중 5%
            backtester.config.set('IGNORE_BEAR_ON_STRONG_BULL', True) # 강세장일 때 하락장 무시 여부
            
            # [Restored] Baseline Overrides
            backtester.config.set('OVERRIDE_STOP_LOSS_PCT', -0.05) # -5% 손절
            backtester.config.set('OVERRIDE_TARGET_PROFIT_PCT', 0.1) # 10% 익절

            # v14.5: 로컬 실행 시 CLI 인자를 ConfigManager에 설정
            backtester.config.set('PROFIT_TARGET_FULL', args.profit_target_full)
            backtester.config.set('PROFIT_TARGET_PARTIAL', args.profit_target_partial)
            backtester.config.set('RSI_THRESHOLD_1', args.rsi_threshold_1)
            backtester.config.set('RSI_THRESHOLD_2', args.rsi_threshold_2)
            backtester.config.set('RSI_THRESHOLD_3', args.rsi_threshold_3)
            backtester.config.set('TIME_BASED_BULL', args.time_based_bull)
            backtester.config.set('TIME_BASED_SIDEWAYS', args.time_based_sideways)
            # [수정] PositionSizer가 사용하는 'MAX_POSITION_VALUE_PCT'로 설정
            backtester.config.set('MAX_POSITION_VALUE_PCT', args.max_position_value_pct)
            backtester.config.set('MAX_QUANTITY', args.max_quantity)
            backtester.config.set('CASH_KEEP_PCT', args.cash_keep_pct)
            backtester.config.set('MAX_BUYS_PER_DAY', args.max_buys_per_day)
            backtester.config.set('IGNORE_BEAR_ON_STRONG_BULL', args.ignore_bear_on_strong_bull)
            backtester.config.set('STRONG_BULL_ATR_MULTIPLIER_INITIAL', args.sb_atr_init)
            backtester.config.set('STRONG_BULL_ATR_MULTIPLIER_INITIAL', args.sb_atr_init)
            backtester.config.set('STRONG_BULL_ATR_MULTIPLIER_TRAILING', args.sb_atr_trail)
            
            # v14.7: 손절/익절 오버라이드 설정
            if args.stop_loss_pct is not None:
                backtester.config.set('OVERRIDE_STOP_LOSS_PCT', args.stop_loss_pct)
            if args.target_profit_pct is not None:
                backtester.config.set('OVERRIDE_TARGET_PROFIT_PCT', args.target_profit_pct)

            metrics = backtester.run()
            logger.info("--- ✅ 백테스트 완료 ---")

            # 결과 요약을 로그로 남김(파일 핸들러가 함께 저장)
            logger.info(f"최종 누적 수익률: {metrics['total_return_pct']:.2f}%")
            logger.info(f"최대 낙폭(MDD): {metrics['mdd_pct']:.2f}%")
            if metrics["rocket_return_pct"] is not None:
                logger.info(f"--- 🚀 '로켓장' (2025.05.01~) 성적표 ---")
                logger.info(f"최종 누적 수익률 (로켓장): {metrics['rocket_return_pct']:.2f}%")

    except Exception as e:
        logger.critical(f"❌ 백테스트 중 치명적 오류: {e}", exc_info=True)
        sys.exit(1)
    finally:
        if db_conn:
            # [추가] DB 연결 풀 종료
            database.close_pool()
            db_conn.close()
            logger.info("--- DB 연결 종료 ---")

if __name__ == "__main__":
    main()