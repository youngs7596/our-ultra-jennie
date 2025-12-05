"""
shared/market_regime.py - Ultra Jennie 시장 국면 분석 모듈
=======================================================

이 모듈은 KOSPI 지수를 기반으로 시장 국면을 분석합니다.

시장 국면 분류:
-------------
- STRONG_BULL: 급등장 (MA20 대비 +5% 이상)
- BULL: 상승장 (MA20 대비 +2% 이상)
- SIDEWAYS: 횡보장 (MA20 대비 ±2%)
- BEAR: 하락장 (MA20 대비 -2% 이하)

국면별 전략:
----------
- STRONG_BULL: 추세 추종 (Trend Following)
- BULL: 추세 추종 + 평균 회귀 혼합
- SIDEWAYS: 평균 회귀 (Mean Reversion)
- BEAR: 방어적 전략 (Iron Shield)

사용 예시:
---------
>>> from shared.market_regime import MarketRegimeDetector, StrategySelector
>>>
>>> detector = MarketRegimeDetector()
>>> regime, context = detector.detect_regime(kospi_df, current_price)
>>> print(f"현재 시장: {regime}")
>>>
>>> selector = StrategySelector()
>>> strategies = selector.select_strategies(regime)
>>> print(f"추천 전략: {strategies}")
"""

import logging
import pandas as pd
from typing import Dict, Optional, Tuple
from shared import strategy

logger = logging.getLogger(__name__)

class MarketRegimeDetector:
    """
    시장 상황을 분석하여 적절한 매수 전략을 제안하는 클래스
    """
    
    # 시장 상황 분류
    REGIME_STRONG_BULL = "STRONG_BULL"  # 급등장 (강한 상승)
    REGIME_BULL = "BULL"                # 상승장 (완만한 상승)
    REGIME_SIDEWAYS = "SIDEWAYS"        # 횡보장
    REGIME_BEAR = "BEAR"                # 하락장
    
    def __init__(self):
        self.regime_cache = {}
    
    def detect_regime(self, kospi_prices_df: pd.DataFrame, kospi_current: float, quiet: bool = False) -> Tuple[str, Dict]:
        """
        KOSPI 데이터를 기반으로 현재 시장 상황을 분석합니다.
        
        Args:
            kospi_prices_df: KOSPI 일봉 데이터 (날짜 오름차순)
            kospi_current: KOSPI 현재가
            quiet: 로깅 억제 여부 (백테스트 등 반복 호출 시 사용)
            
        Returns:
            (regime, context_dict): 시장 상황과 상세 컨텍스트
        """
        try:
            # 최소 데이터 요구사항 완화: 20일 → 10일
            if kospi_prices_df.empty or len(kospi_prices_df) < 10:
                if not quiet:
                    logger.warning("   (Market Regime) KOSPI 데이터 부족 (10일 미만). 기본값 'SIDEWAYS' 반환")
                return self.REGIME_SIDEWAYS, {"error": "데이터 부족"}
            
            prices_list = kospi_prices_df['CLOSE_PRICE'].tolist()
            prices_list_reversed = prices_list[::-1]  # [최신, ..., 과거]
            
            # 1. 이동평균선 계산 (데이터 양에 따라 MA10 또는 MA20 사용)
            data_length = len(kospi_prices_df)
            ma_period = 20 if data_length >= 20 else 10
            ma_value = strategy.calculate_moving_average(prices_list_reversed, period=ma_period)
            if not ma_value:
                if not quiet:
                    logger.warning(f"   (Market Regime) MA{ma_period} 계산 실패. 기본값 'SIDEWAYS' 반환")
                return self.REGIME_SIDEWAYS, {"error": f"MA{ma_period} 계산 실패"}
            
            # MA10 사용 시 보수적으로 판단하기 위한 플래그
            using_ma10 = (ma_period == 10)
            
            # 2. 최근 5일 수익률 계산
            if len(prices_list) >= 5:
                price_5days_ago = prices_list[-5]
                return_5d = ((kospi_current - price_5days_ago) / price_5days_ago) * 100
            else:
                return_5d = 0.0
            
            # 3. 최근 1일 수익률 계산 (전일 대비)
            if len(prices_list) >= 2:
                price_yesterday = prices_list[-1]
                return_1d = ((kospi_current - price_yesterday) / price_yesterday) * 100
            else:
                return_1d = 0.0
            
            # 4. 이동평균선 대비 위치 (MA10 또는 MA20)
            ma_distance = ((kospi_current - ma_value) / ma_value) * 100
            
            # 5. 시장 상황 판단 (점수 기반 병렬 평가)
            # MA10 사용 시 보수적으로 판단하기 위한 임계값 조정
            ma_threshold = 1.5 if using_ma10 else 2.0  # MA10 사용 시 더 좁은 범위
            ma_name = f"MA{ma_period}"
            
            context = {
                "kospi_current": kospi_current,
                f"kospi_{ma_name.lower()}": ma_value,
                f"{ma_name.lower()}_distance_pct": ma_distance,
                "return_1d_pct": return_1d,
                "return_5d_pct": return_5d,
                "using_ma10": using_ma10
            }
            
            # 모든 시장 상황에 대한 점수를 병렬로 계산
            regime_scores = {}
            
            # STRONG_BULL 점수: 급등 강도 기반
            strong_bull_score = 0.0
            if kospi_current >= ma_value:
                # MA10 사용 시 더 보수적으로 (임계값 상향)
                threshold_1d = 2.5 if using_ma10 else 2.0
                threshold_5d = 5.0 if using_ma10 else 4.0
                # 1일 수익률 점수 (0~50점)
                strong_bull_score += max(0, min(50, (return_1d / threshold_1d) * 50)) if return_1d > 0 else 0
                # 5일 수익률 점수 (0~50점)
                strong_bull_score += max(0, min(50, (return_5d / threshold_5d) * 50)) if return_5d > 0 else 0
                # MA10 사용 시 점수 감소 (보수적)
                if using_ma10:
                    strong_bull_score *= 0.8
            regime_scores[self.REGIME_STRONG_BULL] = strong_bull_score
            
            # BULL 점수: 완만한 상승 기반
            bull_score = 0.0
            if kospi_current >= ma_value:
                # 이동평균선 위에 있으면 기본 점수 (MA10 사용 시 감소)
                base_score = 25 if using_ma10 else 30
                bull_score += base_score
                # 5일 수익률이 양수면 추가 점수 (최대 40점)
                if return_5d > 0:
                    bull_score += min(40, return_5d * 10)  # 4%면 40점
                # 이동평균선 거리가 멀수록 추가 점수 (최대 30점)
                if ma_distance > 0:
                    bull_score += min(30, ma_distance * 2)
                # MA10 사용 시 점수 감소 (보수적)
                if using_ma10:
                    bull_score *= 0.85
            regime_scores[self.REGIME_BULL] = bull_score
            
            # BEAR 점수: 하락 강도 기반
            bear_score = 0.0
            if kospi_current < ma_value:
                # 이동평균선 아래에 있으면 기본 점수
                bear_score += 40
                # 5일 수익률이 음수면 추가 점수 (최대 40점)
                if return_5d < 0:
                    bear_score += min(40, abs(return_5d) * 10)  # -4%면 40점
                # 이동평균선 거리가 멀수록 추가 점수 (최대 20점)
                if ma_distance < 0:
                    bear_score += min(20, abs(ma_distance) * 2)
            regime_scores[self.REGIME_BEAR] = bear_score
            
            # SIDEWAYS 점수: 횡보 특성 기반
            sideways_score = 0.0
            # 이동평균선 근처에 있으면 기본 점수
            if abs(ma_distance) < ma_threshold:  # MA ±임계값 이내
                sideways_score += 30
            # 5일 수익률이 작으면 추가 점수
            if abs(return_5d) < 1.0:  # ±1% 이내
                sideways_score += 30
            # 1일 수익률도 작으면 추가 점수
            if abs(return_1d) < 0.5:  # ±0.5% 이내
                sideways_score += 40
            regime_scores[self.REGIME_SIDEWAYS] = sideways_score
            
            # 가장 높은 점수를 가진 시장 상황 선택
            regime = max(regime_scores, key=regime_scores.get)
            max_score = regime_scores[regime]
            
            # 로깅
            context["regime_scores"] = regime_scores
            context["regime"] = regime
            
            # 로깅 (MA10 사용 시 표시)
            ma_info = f"{ma_name} 거리: {ma_distance:.2f}%"
            if using_ma10:
                ma_info += " (MA10 사용, 보수적 판단)"
            
            if not quiet:
                if regime == self.REGIME_STRONG_BULL:
                    logger.info(f"   (Market Regime) 🚀 급등장 감지! (점수: {max_score:.1f}, 1일: {return_1d:.2f}%, 5일: {return_5d:.2f}%, {ma_info})")
                elif regime == self.REGIME_BULL:
                    logger.info(f"   (Market Regime) 📈 상승장 감지 (점수: {max_score:.1f}, 5일: {return_5d:.2f}%, {ma_info})")
                elif regime == self.REGIME_BEAR:
                    logger.warning(f"   (Market Regime) 📉 하락장 감지 (점수: {max_score:.1f}, {ma_info})")
                else:
                    logger.info(f"   (Market Regime) ➡️ 횡보장 감지 (점수: {max_score:.1f}, 5일: {return_5d:.2f}%, {ma_info})")
            
            return regime, context
            
        except Exception as e:
            logger.error(f"❌ (Market Regime) 시장 상황 분석 중 오류: {e}", exc_info=True)
            return self.REGIME_SIDEWAYS, {"error": str(e)}

    def get_dynamic_risk_setting(self, regime: str) -> dict:
        """
        시장 상황(Regime)에 따라 동적 리스크 관리 설정(손절가, 익절가, 비중)을 반환합니다.
        [v3.5] 하락장 방어력 강화 및 상승장 수익 극대화 전략 적용
        
        Args:
            regime: 시장 상황 (STRONG_BULL, BULL, SIDEWAYS, BEAR)
            
        Returns:
            dict: {
                "stop_loss_pct": float,      # 손절 기준 (예: -0.05 = -5%)
                "target_profit_pct": float,  # 익절 기준 (예: 0.10 = 10%)
                "position_size_ratio": float # 비중 조절 비율 (0.0 ~ 1.0)
            }
        """
        if regime == self.REGIME_STRONG_BULL:
            return {
                "stop_loss_pct": -0.05,      # 급등장: 변동성 허용 (여유 있게)
                "target_profit_pct": 0.15,   # 급등장: 길게 먹기 (추세 추종)
                "position_size_ratio": 1.0   # 비중 100% (풀시드)
            }
        elif regime == self.REGIME_BULL:
            return {
                "stop_loss_pct": -0.05,      # [Updated] 상승장: -5% (Backtest 4.36% 기준)
                "target_profit_pct": 0.10,
                "position_size_ratio": 1.0
            }
        elif regime == self.REGIME_SIDEWAYS:
            return {
                "stop_loss_pct": -0.05,      # [Updated] 횡보장: -5% (Backtest 4.36% 기준)
                "target_profit_pct": 0.10,    # [Updated] 횡보장: 10% (Backtest 4.36% 기준)
                "position_size_ratio": 0.5   # 비중 50% 축소
            }
        elif regime == self.REGIME_BEAR:
            return {
                "stop_loss_pct": -0.02,      # 하락장: 칼손절 (스치면 사망)
                "target_profit_pct": 0.03,    # 기술적 반등만 먹고 튀기
                "position_size_ratio": 0.3   # 비중 30% 축소 (정찰병 수준)
            }
        else:
            # 기본값 (보수적)
            return {
                "stop_loss_pct": -0.03,
                "target_profit_pct": 0.05,
                "position_size_ratio": 0.5
            }


# backtest.py (v3.5 - 제니's 픽!)
class StrategySelector:
    """
    시장 상황에 따라 적절한 매수 전략을 선택하는 클래스
    """
    # ... (전략 타입 정의는 동일) ...
    STRATEGY_MEAN_REVERSION = "MEAN_REVERSION"      # 평균 회귀
    STRATEGY_TREND_FOLLOWING = "TREND_FOLLOWING"    # 추세 추종 (골든 크로스 기반)
    STRATEGY_MOMENTUM = "MOMENTUM"                  # 모멘텀 (Legacy, 미사용)
    STRATEGY_RELATIVE_STRENGTH = "RELATIVE_STRENGTH"  # 상대 강도 (Legacy, 미사용)
    STRATEGY_RESISTANCE_BREAKOUT = "RESISTANCE_BREAKOUT" # 저항선 돌파
    STRATEGY_VOLUME_MOMENTUM = "VOLUME_MOMENTUM"    # 듀얼 모멘텀 + 거래량 돌파
    STRATEGY_BEAR_SNIPE_DIP = "BEAR_SNIPE_DIP"
    STRATEGY_BEAR_MOMENTUM_BREAKOUT = "BEAR_MOMENTUM_BREAKOUT"
    
    def __init__(self):
        self.detector = MarketRegimeDetector()
    
    def select_strategies(self, regime: str) -> list:
        # 제니의 선택 기준 (최근 백테스트 결과 기반)
        # MOMENTUM / RS 전략은 낮은 기대성과로 인해 폐기.
        # STRONG_BULL에서는 저항선 돌파 계열 전략을 최우선으로 사용.
        
        strategy_map = {
            MarketRegimeDetector.REGIME_STRONG_BULL: [
                self.STRATEGY_VOLUME_MOMENTUM,     # 듀얼 모멘텀 우선
                self.STRATEGY_RESISTANCE_BREAKOUT, # 로켓장 서브 전략
                self.STRATEGY_TREND_FOLLOWING      # 골든크로스 기반 추세 추종
            ],
            MarketRegimeDetector.REGIME_BULL: [
                self.STRATEGY_VOLUME_MOMENTUM,
                self.STRATEGY_TREND_FOLLOWING,
                self.STRATEGY_MEAN_REVERSION
            ],
            MarketRegimeDetector.REGIME_SIDEWAYS: [
                self.STRATEGY_MEAN_REVERSION,
                self.STRATEGY_TREND_FOLLOWING
            ],
            MarketRegimeDetector.REGIME_BEAR: [] # 🅿️ (P-Parking)
        }
        
        strategies = strategy_map.get(regime, [self.STRATEGY_MEAN_REVERSION])
        logger.info(f"   (Strategy Selector) v3.5 제니's 픽 - 시장 '{regime}' 전략: {strategies}")
        return strategies

    def map_llm_strategy(self, strategy_type: str) -> str | None:
        """하락장 LLM 전략 문자열을 내부 전략 상수로 매핑."""
        mapping = {
            "SNIPE_DIP": self.STRATEGY_BEAR_SNIPE_DIP,
            "MOMENTUM_BREAKOUT": self.STRATEGY_BEAR_MOMENTUM_BREAKOUT,
        }
        return mapping.get(strategy_type)

