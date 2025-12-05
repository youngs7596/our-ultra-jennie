#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Version: v3.5
"""
[v3.5] 팩터 스코어링 모듈
작업 LLM: Claude Sonnet 4.5

목적:
- 4대 팩터 (모멘텀, 품질, 가치, 기술적) 점수 계산
- 시장 상황별 동적 가중치 적용
- 최종 1000점 만점 점수 산출
"""

import logging
import pandas as pd
import numpy as np
from typing import Dict, Tuple, Optional
from shared import strategy

logger = logging.getLogger(__name__)

class FactorScorer:
    """v3.5 팩터 스코어링 엔진"""
    
    def __init__(self):
        """초기화"""
        pass
    
    def calculate_momentum_score(self, 
                                 daily_prices_df: pd.DataFrame,
                                 kospi_prices_df: Optional[pd.DataFrame] = None) -> Tuple[float, Dict]:
        """
        모멘텀 팩터 점수 계산 (100점 만점)
        
        세부 구성:
        - 6개월 상대 모멘텀: 60점
        - 1개월 단기 모멘텀: 20점
        - 모멘텀 안정성: 20점
        
        Args:
            daily_prices_df: 종목 일봉 데이터
            kospi_prices_df: KOSPI 일봉 데이터
        
        Returns:
            (점수, 상세 정보)
        """
        try:
            factors = {}
            total_score = 0.0
            
            # 1. 6개월 상대 모멘텀 (60점)
            if kospi_prices_df is not None and len(kospi_prices_df) >= 120 and len(daily_prices_df) >= 120:
                stock_start = float(daily_prices_df['CLOSE_PRICE'].iloc[-120])
                stock_end = float(daily_prices_df['CLOSE_PRICE'].iloc[-1])
                stock_return = (stock_end / stock_start - 1) * 100
                
                kospi_start = float(kospi_prices_df['CLOSE_PRICE'].iloc[-120])
                kospi_end = float(kospi_prices_df['CLOSE_PRICE'].iloc[-1])
                kospi_return = (kospi_end / kospi_start - 1) * 100
                
                relative_momentum_6m = stock_return - kospi_return
                
                # -30% ~ +30%를 0~60점으로 연속 매핑
                momentum_6m_score = max(0, min(60, 30 + relative_momentum_6m))
                total_score += momentum_6m_score
                
                factors['relative_momentum_6m'] = round(relative_momentum_6m, 2)
                factors['momentum_6m_score'] = round(momentum_6m_score, 2)
            else:
                # 데이터 부족 시 중립 (30점)
                total_score += 30
                factors['momentum_6m_score'] = 30
                factors['momentum_6m_note'] = '데이터 부족'
            
            # 2. 1개월 단기 모멘텀 (20점)
            if kospi_prices_df is not None and len(kospi_prices_df) >= 20 and len(daily_prices_df) >= 20:
                stock_return_1m = (daily_prices_df['CLOSE_PRICE'].iloc[-1] / daily_prices_df['CLOSE_PRICE'].iloc[-20] - 1) * 100
                kospi_return_1m = (kospi_prices_df['CLOSE_PRICE'].iloc[-1] / kospi_prices_df['CLOSE_PRICE'].iloc[-20] - 1) * 100
                relative_momentum_1m = stock_return_1m - kospi_return_1m
                
                # -10% ~ +10%를 0~20점으로 연속 매핑
                momentum_1m_score = max(0, min(20, 10 + relative_momentum_1m))
                total_score += momentum_1m_score
                
                factors['relative_momentum_1m'] = round(relative_momentum_1m, 2)
                factors['momentum_1m_score'] = round(momentum_1m_score, 2)
            else:
                total_score += 10
                factors['momentum_1m_score'] = 10
            
            # 3. 모멘텀 안정성 (20점)
            if len(daily_prices_df) >= 120:
                # 최근 6개월을 월별로 나누어 상승한 달의 비율 계산
                monthly_returns = []
                for i in range(6):
                    start_idx = -120 + i * 20
                    end_idx = -120 + (i + 1) * 20 if i < 5 else -1
                    if abs(start_idx) <= len(daily_prices_df) and abs(end_idx) <= len(daily_prices_df):
                        start_price = daily_prices_df['CLOSE_PRICE'].iloc[start_idx]
                        end_price = daily_prices_df['CLOSE_PRICE'].iloc[end_idx]
                        monthly_return = (end_price / start_price - 1) * 100
                        monthly_returns.append(monthly_return)
                
                if monthly_returns:
                    positive_months = sum(1 for r in monthly_returns if r > 0)
                    consistency = positive_months / len(monthly_returns)
                    consistency_score = consistency * 20
                    total_score += consistency_score
                    
                    factors['momentum_consistency'] = round(consistency, 2)
                    factors['consistency_score'] = round(consistency_score, 2)
                else:
                    total_score += 10
                    factors['consistency_score'] = 10
            else:
                total_score += 10
                factors['consistency_score'] = 10
            
            return total_score, factors
            
        except Exception as e:
            logger.error(f"   (Factor) 모멘텀 점수 계산 오류: {e}", exc_info=True)
            return 50.0, {'error': str(e)}
    
    def calculate_quality_score(self, 
                                roe: Optional[float],
                                sales_growth: Optional[float],
                                eps_growth: Optional[float],
                                daily_prices_df: pd.DataFrame) -> Tuple[float, Dict]:
        """
        품질 팩터 점수 계산 (100점 만점)
        
        세부 구성:
        - ROE (수익성): 40점
        - 성장성 (매출+EPS): 40점
        - 이익 안정성: 20점
        
        Args:
            roe: 자기자본이익률 (%)
            sales_growth: 매출 성장률 (%)
            eps_growth: EPS 성장률 (%)
            daily_prices_df: 종목 일봉 데이터
        
        Returns:
            (점수, 상세 정보)
        """
        try:
            factors = {}
            total_score = 0.0
            
            # 1. ROE (수익성) - 40점
            if roe is not None:
                # ROE: -20% ~ +40%를 0~40점으로 연속 매핑
                # 15% = 30점 (우량 기준)
                # 20% = 33점
                # 30% = 40점
                roe_score = max(0, min(40, 20 + roe * 0.67))
                total_score += roe_score
                
                factors['roe'] = round(roe, 2)
                factors['roe_score'] = round(roe_score, 2)
            else:
                # 데이터 없으면 중립 (20점)
                total_score += 20
                factors['roe_score'] = 20
                factors['roe_note'] = '데이터 없음'
            
            # 2. 성장성 (매출 + EPS) - 40점
            growth_score = 0.0
            
            # 2-1. 매출 성장률 (20점)
            if sales_growth is not None:
                # -10% ~ +30%를 0~20점으로 연속 매핑
                sales_score = max(0, min(20, 5 + sales_growth * 0.5))
                growth_score += sales_score
                
                factors['sales_growth'] = round(sales_growth, 2)
                factors['sales_score'] = round(sales_score, 2)
            else:
                growth_score += 10
                factors['sales_score'] = 10
            
            # 2-2. EPS 성장률 (20점)
            if eps_growth is not None:
                # -20% ~ +40%를 0~20점으로 연속 매핑
                eps_score = max(0, min(20, 7 + eps_growth * 0.33))
                growth_score += eps_score
                
                factors['eps_growth'] = round(eps_growth, 2)
                factors['eps_score'] = round(eps_score, 2)
            else:
                growth_score += 10
                factors['eps_score'] = 10
            
            total_score += growth_score
            
            # 3. 이익 안정성 (20점) - 가격 변동성으로 대체
            # 실제로는 분기별 순이익 변동성을 사용해야 하지만, 데이터 부족 시 가격 변동성 사용
            if len(daily_prices_df) >= 60:
                returns = daily_prices_df['CLOSE_PRICE'].pct_change().dropna()
                volatility = returns.std() * 100  # 일간 변동성 (%)
                
                # 변동성: 0~5%를 20~0점으로 매핑 (낮을수록 좋음)
                # 1% = 16점
                # 2% = 12점
                # 3% = 8점
                stability_score = max(0, 20 - volatility * 4)
                total_score += stability_score
                
                factors['volatility'] = round(volatility, 2)
                factors['stability_score'] = round(stability_score, 2)
            else:
                total_score += 10
                factors['stability_score'] = 10
            
            return total_score, factors
            
        except Exception as e:
            logger.error(f"   (Factor) 품질 점수 계산 오류: {e}", exc_info=True)
            return 50.0, {'error': str(e)}
    
    def calculate_value_score(self, 
                             pbr: Optional[float],
                             per: Optional[float]) -> Tuple[float, Dict]:
        """
        가치 팩터 점수 계산 (100점 만점)
        
        세부 구성:
        - PBR: 50점
        - PER: 50점
        
        Args:
            pbr: 주가순자산비율
            per: 주가수익비율
        
        Returns:
            (점수, 상세 정보)
        """
        try:
            factors = {}
            total_score = 0.0
            
            # 1. PBR (50점) - 낮을수록 좋음
            if pbr is not None and pbr > 0:
                # PBR: 0.5~3.0을 50~0점으로 연속 매핑
                # PBR 0.5 = 50점 (매우 저평가)
                # PBR 1.0 = 40점 (적정)
                # PBR 2.0 = 20점
                # PBR 3.0 이상 = 0점
                pbr_score = max(0, min(50, 50 - (pbr - 0.5) * 20))
                total_score += pbr_score
                
                factors['pbr'] = round(pbr, 2)
                factors['pbr_score'] = round(pbr_score, 2)
            else:
                # 데이터 없으면 중립 (25점)
                total_score += 25
                factors['pbr_score'] = 25
                factors['pbr_note'] = '데이터 없음'
            
            # 2. PER (50점) - 낮을수록 좋음 (단, 적자 기업 제외)
            if per is not None and per > 0:
                # PER: 5~30을 50~0점으로 연속 매핑
                # PER 5 = 50점 (매우 저평가)
                # PER 10 = 40점 (저평가)
                # PER 15 = 30점 (적정)
                # PER 30 이상 = 0점
                per_score = max(0, min(50, 50 - (per - 5) * 2))
                total_score += per_score
                
                factors['per'] = round(per, 2)
                factors['per_score'] = round(per_score, 2)
            else:
                # 적자 기업 또는 데이터 없음 (0점)
                total_score += 0
                factors['per_score'] = 0
                factors['per_note'] = '적자 또는 데이터 없음'
            
            return total_score, factors
            
        except Exception as e:
            logger.error(f"   (Factor) 가치 점수 계산 오류: {e}", exc_info=True)
            return 50.0, {'error': str(e)}
    
    def calculate_technical_score(self, daily_prices_df: pd.DataFrame) -> Tuple[float, Dict]:
        """
        기술적 팩터 점수 계산 (100점 만점)
        
        세부 구성:
        - 거래량 추세: 40점
        - RSI: 30점
        - 볼린저 밴드: 30점
        
        Args:
            daily_prices_df: 종목 일봉 데이터
        
        Returns:
            (점수, 상세 정보)
        """
        try:
            factors = {}
            total_score = 0.0
            
            # 1. 거래량 추세 (40점)
            if 'VOLUME' in daily_prices_df.columns and len(daily_prices_df) >= 25:
                recent_volume = daily_prices_df['VOLUME'].tail(5).mean()
                past_volume = daily_prices_df['VOLUME'].iloc[-25:-5].mean()
                
                if past_volume > 0:
                    volume_ratio = recent_volume / past_volume
                    
                    # 0.5배~3.0배를 0~40점으로 연속 매핑
                    # 1.0배 (변화 없음) = 20점
                    # 1.5배 = 28점
                    # 2.0배 = 36점
                    # 3.0배 이상 = 40점
                    volume_score = max(0, min(40, (volume_ratio - 0.5) * 16))
                    total_score += volume_score
                    
                    factors['volume_ratio'] = round(volume_ratio, 2)
                    factors['volume_score'] = round(volume_score, 2)
                else:
                    total_score += 20
                    factors['volume_score'] = 20
            else:
                total_score += 20
                factors['volume_score'] = 20
            
            # 2. RSI (30점)
            rsi = strategy.calculate_rsi(daily_prices_df, period=14)
            if rsi is not None:
                # RSI 과매도 구간(30~40)에 높은 점수
                # RSI 30 = 30점 (매수 기회)
                # RSI 40 = 25점
                # RSI 50 = 15점 (중립)
                # RSI 70 = 5점 (과매수)
                # RSI 80 이상 = 0점
                
                if rsi <= 30:
                    rsi_score = 30
                elif rsi <= 50:
                    rsi_score = 30 - (rsi - 30) * 0.75
                elif rsi <= 70:
                    rsi_score = 15 - (rsi - 50) * 0.5
                else:
                    rsi_score = max(0, 5 - (rsi - 70) * 0.25)
                
                total_score += rsi_score
                
                factors['rsi'] = round(rsi, 2)
                factors['rsi_score'] = round(rsi_score, 2)
            else:
                total_score += 15
                factors['rsi_score'] = 15
            
            # 3. 볼린저 밴드 (30점)
            if len(daily_prices_df) >= 20:
                # 볼린저 밴드 계산
                close_prices = daily_prices_df['CLOSE_PRICE']
                ma20 = close_prices.rolling(window=20).mean().iloc[-1]
                std20 = close_prices.rolling(window=20).std().iloc[-1]
                
                bb_upper = ma20 + 2 * std20
                bb_lower = ma20 - 2 * std20
                current_price = close_prices.iloc[-1]
                
                if bb_upper > bb_lower:
                    # 밴드 내 위치: 0 (하단) ~ 1 (상단)
                    bb_position = (current_price - bb_lower) / (bb_upper - bb_lower)
                    
                    # 하단에 가까울수록 높은 점수
                    # 하단 (0) = 30점 (매수 기회)
                    # 중간 (0.5) = 15점
                    # 상단 (1.0) = 0점
                    bb_score = max(0, 30 - bb_position * 30)
                    total_score += bb_score
                    
                    factors['bb_position'] = round(bb_position, 2)
                    factors['bb_score'] = round(bb_score, 2)
                else:
                    total_score += 15
                    factors['bb_score'] = 15
            else:
                total_score += 15
                factors['bb_score'] = 15
            
            return total_score, factors
            
        except Exception as e:
            logger.error(f"   (Factor) 기술적 점수 계산 오류: {e}", exc_info=True)
            return 50.0, {'error': str(e)}
    
    def calculate_final_score(self,
                             momentum_score: float,
                             quality_score: float,
                             value_score: float,
                             technical_score: float,
                             market_regime: str) -> Tuple[float, Dict]:
        """
        최종 팩터 점수 계산 (1000점 만점)
        
        시장 상황별 동적 가중치 적용:
        - STRONG_BULL: 모멘텀 40%, 품질 25%, 가치 15%, 기술적 20%
        - BEAR: 모멘텀 20%, 품질 35%, 가치 30%, 기술적 15%
        - 기타 (BULL, SIDEWAYS): 모멘텀 30%, 품질 30%, 가치 20%, 기술적 20%
        
        Args:
            momentum_score: 모멘텀 점수 (0~100)
            quality_score: 품질 점수 (0~100)
            value_score: 가치 점수 (0~100)
            technical_score: 기술적 점수 (0~100)
            market_regime: 시장 상황 ('STRONG_BULL', 'BULL', 'SIDEWAYS', 'BEAR')
        
        Returns:
            (최종 점수 0~1000, 가중치 정보)
        """
        try:
            # [핵심] 시장 상황별 가중치 동적 할당
            if market_regime == "STRONG_BULL":
                weights = {
                    'momentum': 0.40,  # 모멘텀 가중치 상향 (30% -> 40%)
                    'quality': 0.25,   # 품질 가중치 하향
                    'value': 0.15,     # 가치 가중치 하향
                    'technical': 0.20
                }
            elif market_regime == "BEAR":
                weights = {
                    'momentum': 0.20,  # 모멘텀 가중치 하향
                    'quality': 0.35,   # 품질 가중치 상향
                    'value': 0.30,     # 가치 가중치 상향
                    'technical': 0.15
                }
            else:  # 'BULL' 또는 'SIDEWAYS' (기본값)
                weights = {
                    'momentum': 0.30,
                    'quality': 0.30,
                    'value': 0.20,
                    'technical': 0.20
                }
            
            # 최종 1000점 만점 점수 계산
            final_score = (
                (momentum_score * weights['momentum'] * 10) +   # (100 * 0.4 * 10 = 400점)
                (quality_score * weights['quality'] * 10) +     # (100 * 0.25 * 10 = 250점)
                (value_score * weights['value'] * 10) +         # (100 * 0.15 * 10 = 150점)
                (technical_score * weights['technical'] * 10)   # (100 * 0.20 * 10 = 200점)
            )  # 총 1000점
            
            weight_info = {
                'applied_weights': weights,
                'market_regime': market_regime,
                'momentum_contribution': round(momentum_score * weights['momentum'] * 10, 2),
                'quality_contribution': round(quality_score * weights['quality'] * 10, 2),
                'value_contribution': round(value_score * weights['value'] * 10, 2),
                'technical_contribution': round(technical_score * weights['technical'] * 10, 2)
            }
            
            return final_score, weight_info
            
        except Exception as e:
            logger.error(f"   (Factor) 최종 점수 계산 오류: {e}", exc_info=True)
            return 500.0, {'error': str(e)}

