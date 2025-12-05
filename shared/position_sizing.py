# youngs75_jennie/position_sizing.py
# Version: v3.5
# 동적 포지션 사이징 모듈
# 작업 LLM: Claude Sonnet 4.5

import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)

class PositionSizer:
    """
    ATR 기반 Risk-Parity 포지션 사이징
    
    핵심 원칙:
    1. 모든 포지션이 동일한 위험도를 가짐
    2. 변동성이 높은 종목 = 적은 수량
    3. 변동성이 낮은 종목 = 많은 수량
    """
    
    def __init__(self, config):
        """
        Args:
            config: AgentConfig 객체
        """
        self.config = config
        
        # 설정값 (CONFIG 테이블에서 관리)
        self.risk_per_trade_pct = config.get_float('RISK_PER_TRADE_PCT', default=2.0)  # 거래당 위험 2%
        self.atr_multiplier = config.get_float('ATR_MULTIPLIER', default=2.0)  # ATR 배수
        self.min_quantity = config.get_int('MIN_QUANTITY', default=1)  # 최소 수량
        self.max_quantity = config.get_int('MAX_QUANTITY', default=1000)  # 최대 수량
        self.max_position_value_pct = config.get_float('MAX_POSITION_VALUE_PCT', default=15.0)  # 단일 포지션 최대 15% (Backtest Optimized)

    def refresh_from_config(self):
        """ConfigManager 값이 변경되었을 때 내부 한도를 갱신"""
        self.risk_per_trade_pct = self.config.get_float('RISK_PER_TRADE_PCT', default=self.risk_per_trade_pct)
        self.atr_multiplier = self.config.get_float('ATR_MULTIPLIER', default=self.atr_multiplier)
        self.min_quantity = self.config.get_int('MIN_QUANTITY', default=self.min_quantity)
        self.max_quantity = self.config.get_int('MAX_QUANTITY', default=self.max_quantity)
        self.max_position_value_pct = self.config.get_float('MAX_POSITION_VALUE_PCT', default=self.max_position_value_pct)
    
    def calculate_quantity(
        self,
        stock_code: str,
        stock_price: float,
        atr: float,
        account_balance: float,
        portfolio_value: float = 0
    ) -> Dict:
        """
        ATR 기반 최적 매수 수량 계산
        
        Args:
            stock_code: 종목 코드
            stock_price: 현재 주가
            atr: 평균 진폭 (Average True Range)
            account_balance: 계좌 잔고 (현금)
            portfolio_value: 현재 포트폴리오 총 가치
        
        Returns:
            {
                'quantity': 매수 수량,
                'risk_amount': 위험 금액,
                'position_value': 포지션 가치,
                'risk_pct': 위험 비율,
                'reason': 계산 근거
            }
        """
        try:
            logger.info(f"   [Position Sizing] {stock_code} 수량 계산 시작...")
            logger.info(f"   - 주가: {stock_price:,.0f}원, ATR: {atr:,.0f}원")
            logger.info(f"   - 계좌 잔고: {account_balance:,.0f}원, 포트폴리오: {portfolio_value:,.0f}원")
            
            # 1. 총 자산 계산
            total_assets = account_balance + portfolio_value
            
            if total_assets <= 0:
                return self._create_result(0, "총 자산이 0 이하")
            
            # 2. 거래당 위험 금액 계산
            risk_amount = total_assets * (self.risk_per_trade_pct / 100.0)
            logger.info(f"   - 거래당 위험 금액: {risk_amount:,.0f}원 ({self.risk_per_trade_pct}%)")
            
            # 3. ATR 기반 수량 계산
            # 공식: quantity = risk_amount / (ATR * multiplier)
            import math
            if atr is None or (isinstance(atr, float) and math.isnan(atr)) or atr <= 0:
                logger.warning(f"   [Position Sizing] ATR이 유효하지 않음({atr}), 기본값 사용")
                atr = stock_price * 0.02  # 주가의 2%를 기본 ATR로 사용
            
            risk_per_share = atr * self.atr_multiplier
            quantity_raw = risk_amount / risk_per_share
            
            logger.info(f"   - 주당 위험: {risk_per_share:,.0f}원 (ATR × {self.atr_multiplier})")
            logger.info(f"   - 계산된 수량: {quantity_raw:.2f}주")
            
            # 4. 수량 제약 조건 적용
            quantity = int(quantity_raw)
            
            # 4-1. 최소/최대 수량 제한
            if quantity < self.min_quantity:
                quantity = self.min_quantity
                logger.info(f"   - 최소 수량 적용: {quantity}주")
            elif quantity > self.max_quantity:
                quantity = self.max_quantity
                logger.info(f"   - 최대 수량 적용: {quantity}주")
            
            # 4-2. 단일 포지션 최대 비중 제한 (총 자산 기준)
            position_value = quantity * stock_price
            max_position_by_total_assets = total_assets * (self.max_position_value_pct / 100.0)
            
            # 4-3. 현금 잔고 제한 (현금 유지 비율 적용)
            cash_keep_pct = self.config.get_float('CASH_KEEP_PCT', default=10.0)
            cash_to_keep = total_assets * (cash_keep_pct / 100.0)
            max_position_by_cash = max(0, account_balance - cash_to_keep)
            
            # 4-4. 여러 제약 조건 중 가장 작은 값 사용
            max_position_value = min(max_position_by_total_assets, max_position_by_cash)
            
            if position_value > max_position_value:
                quantity = int(max_position_value / stock_price)
                position_value = quantity * stock_price
                
                if max_position_value == max_position_by_cash:
                    logger.info(f"   - 현금 잔고 제한 적용: {quantity}주 (잔고: {account_balance:,.0f}원)")
                    
                    # [v3.5] Smart Skip: 목표 수량의 50% 미만으로 줄어들면 매수 포기 (현금 부족 시)
                    target_quantity = int(quantity_raw)
                    if target_quantity > 0 and quantity < target_quantity * 0.5:
                        logger.info(f"   ⚠️ Smart Skip: 목표 수량({target_quantity}주)의 50% 미만({quantity}주)이므로 매수 포기")
                        return self._create_result(0, f"Smart Skip: 현금 부족으로 인한 수량 과소 ({quantity}/{target_quantity}주)")
                else:
                    logger.info(f"   - 최대 비중 제한 적용: {quantity}주 ({self.max_position_value_pct}%)")
            
            # 5. 최종 결과
            if quantity < self.min_quantity:
                return self._create_result(0, "최소 수량 미달 또는 잔고 부족")
            
            actual_risk_pct = (quantity * risk_per_share / total_assets) * 100
            
            result = {
                'quantity': quantity,
                'risk_amount': quantity * risk_per_share,
                'position_value': position_value,
                'risk_pct': actual_risk_pct,
                'position_pct': (position_value / total_assets) * 100,
                'reason': f"ATR 기반 Risk-Parity (목표 위험: {self.risk_per_trade_pct}%, 실제: {actual_risk_pct:.2f}%)"
            }
            
            logger.info(f"   ✅ [Position Sizing] 최종 수량: {quantity}주")
            logger.info(f"   - 포지션 가치: {position_value:,.0f}원 ({result['position_pct']:.2f}%)")
            logger.info(f"   - 위험 금액: {result['risk_amount']:,.0f}원 ({actual_risk_pct:.2f}%)")
            
            return result
            
        except Exception as e:
            logger.error(f"   ❌ [Position Sizing] 수량 계산 실패: {e}", exc_info=True)
            return self._create_result(0, f"계산 오류: {str(e)}")
    
    def _create_result(self, quantity: int, reason: str) -> Dict:
        """결과 딕셔너리 생성"""
        return {
            'quantity': quantity,
            'risk_amount': 0,
            'position_value': 0,
            'risk_pct': 0,
            'position_pct': 0,
            'reason': reason
        }
