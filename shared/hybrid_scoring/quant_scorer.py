#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Scout v1.0 QuantScorer - 정량 점수 계산 엔진 (Dual Track)

[v1.0] 3 AI 합의 기반 전면 개편:
- 단기 스나이퍼 (D+5): RSI 과매도 + 외국인 순매수 (승률 55.5%)
- 장기 헌터 (D+60): 수주/실적 뉴스 눌림목 매수 (승률 72.7%)

핵심 발견:
- 뉴스는 단기 역신호 (43.7%), 장기 순신호 (72.7%)
- 모멘텀은 한국 시장에서 역효과 (IC 음수)
- RSI 과매도 + 외국인 복합조건이 유일한 단기 알파

점수 구성 (100점 만점):
[단기 스나이퍼 모드]
- RSI+수급: 40점, 기술적: 20점, 품질: 20점, 기타: 20점

[장기 헌터 모드]  
- ROE: 30점, 뉴스장기효과: 25점, RSI: 20점, 가치: 15점, 기타: 10점
"""

import logging
import pandas as pd
import numpy as np
from enum import Enum
from typing import Dict, Tuple, Optional, List
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field


class StrategyMode(Enum):
    """
    [v1.0] 투자 전략 모드
    
    SHORT_TERM (단기 스나이퍼): D+5 기준, RSI+외인 복합조건 중심
    LONG_TERM (장기 헌터): D+60 기준, 뉴스 눌림목 매수 중심
    DUAL (이중 트랙): 두 전략 동시 평가
    """
    SHORT_TERM = "SHORT_TERM"  # 단기 스나이퍼 (D+5)
    LONG_TERM = "LONG_TERM"    # 장기 헌터 (D+60)
    DUAL = "DUAL"              # 이중 트랙 (둘 다 평가)

from .schema import (
    get_default_factor_weights,
    get_confidence_weight,
    get_confidence_level,
    execute_upsert,
    is_oracle,
)
from .quant_constants import (
    StrategyMode,
    DEFAULT_FILTER_CUTOFF as QC_DEFAULT_FILTER_CUTOFF,
    DEFAULT_HOLDING_DAYS as QC_DEFAULT_HOLDING_DAYS,
    SECTOR_RSI_MULTIPLIER as QC_SECTOR_RSI_MULTIPLIER,
    NEWS_LONG_TERM_POSITIVE as QC_NEWS_LONG_TERM_POSITIVE,
    SHORT_TERM_WEIGHTS as QC_SHORT_TERM_WEIGHTS,
    LONG_TERM_WEIGHTS as QC_LONG_TERM_WEIGHTS,
    GRADE_THRESHOLDS as QC_GRADE_THRESHOLDS,
    RANK_CUTOFF as QC_RANK_CUTOFF,
    NEWS_TIME_EFFECT as QC_NEWS_TIME_EFFECT,
)

logger = logging.getLogger(__name__)


@dataclass
class QuantScoreResult:
    """
    [v1.0] 정량 점수 결과 데이터 클래스 (Dual Track 지원)
    
    단기/장기 전략별 점수를 분리하여 제공
    """
    stock_code: str
    stock_name: str
    
    # 총점 (100점 만점) - 선택된 전략 기준
    total_score: float
    
    # 팩터별 점수
    momentum_score: float
    quality_score: float
    value_score: float
    technical_score: float
    news_stat_score: float
    supply_demand_score: float
    
    # 조건부 승률 정보
    matched_conditions: List[str]
    condition_win_rate: Optional[float]
    condition_sample_count: Optional[int]
    condition_confidence: str
    
    # [v1.0.2] 뉴스 통계 정보
    news_stat_win_rate: Optional[float] = None
    news_stat_sample_count: Optional[int] = None
    news_stat_confidence: str = "LOW"
    
    # 순위 및 필터링
    rank: int = 0
    is_passed_filter: bool = False
    
    # [v1.0.1] 데이터 유효성 플래그
    is_valid: bool = True
    invalid_reason: str = ""
    
    # [v1.0.6] 복합조건 및 섹터 정보
    compound_bonus: float = 0.0
    compound_conditions: List[str] = None
    sector: str = '미분류'
    
    # [v1.0.6] 장기 보유 추천 (D+60 호재 뉴스)
    is_long_term_hold_recommended: bool = False
    
    # ==========================================================
    # [v1.0] Dual Track 전략별 점수 (3 AI 합의)
    # ==========================================================
    
    # 단기 스나이퍼 (D+5) - RSI+외인 중심
    short_term_score: float = 0.0
    short_term_grade: str = "C"  # A/B/C/D/F
    short_term_recommendation: str = "관망"  # 강력매수/매수/관망/주의/회피
    
    # 장기 헌터 (D+60) - ROE+뉴스눌림목 중심
    long_term_score: float = 0.0
    long_term_grade: str = "C"
    long_term_recommendation: str = "관망"
    
    # 뉴스 기반 시간축 판단
    news_timing_signal: str = "NEUTRAL"  # BUY_NOW, WAIT_DIP, SELL_NEWS, NEUTRAL
    news_timing_reason: str = ""
    
    # 예상 보유기간
    recommended_holding_days: int = 5  # 5, 20, 60
    
    # 상세 분석 정보
    details: Dict = field(default_factory=dict)
    
    def __post_init__(self):
        if self.details is None:
            self.details = {}
        if self.compound_conditions is None:
            self.compound_conditions = []
        if self.matched_conditions is None:
            self.matched_conditions = []


class QuantScorer:
    """
    정량 점수 계산 엔진
    
    세 설계의 핵심 아이디어 통합:
    - Claude: 정량 점수를 LLM과 독립적으로 계산하여 검증 가능성 확보
    - Gemini: 하위 50% 조기 탈락으로 비용 절감
    - GPT: 조건부 승률과 Recency Weighting 적용
    
    [v1.0.6] 2025-12-05 팩터 분석 결과 반영:
    - 섹터별 RSI 차별화 (조선운송 60.9%, 금융 60.1% vs 건설기계 49.8%)
    - 복합조건(RSI+외인) 보너스 (55.5% 승률)
    - 장기(D+60) 뉴스 효과 반영 (수주 72.7%, 실적 64.8%)
    """
    
    # 기본 설정/가중치는 quant_constants 모듈로 이동
    DEFAULT_FILTER_CUTOFF = QC_DEFAULT_FILTER_CUTOFF
    DEFAULT_HOLDING_DAYS = QC_DEFAULT_HOLDING_DAYS
    SECTOR_RSI_MULTIPLIER = QC_SECTOR_RSI_MULTIPLIER
    NEWS_LONG_TERM_POSITIVE = QC_NEWS_LONG_TERM_POSITIVE
    SHORT_TERM_WEIGHTS = QC_SHORT_TERM_WEIGHTS
    LONG_TERM_WEIGHTS = QC_LONG_TERM_WEIGHTS
    GRADE_THRESHOLDS = QC_GRADE_THRESHOLDS
    RANK_CUTOFF = QC_RANK_CUTOFF
    NEWS_TIME_EFFECT = QC_NEWS_TIME_EFFECT
    
    def __init__(self, db_conn=None, market_regime: str = 'SIDEWAYS', 
                 strategy_mode: StrategyMode = StrategyMode.DUAL):
        """
        [v1.0] 초기화
        
        Args:
            db_conn: DB 연결 객체 (FACTOR_METADATA, FACTOR_PERFORMANCE 조회용)
            market_regime: 현재 시장 국면 ('STRONG_BULL', 'BULL', 'SIDEWAYS', 'BEAR')
            strategy_mode: 투자 전략 모드 (SHORT_TERM, LONG_TERM, DUAL)
        """
        self.db_conn = db_conn
        self.market_regime = market_regime
        self.strategy_mode = strategy_mode
        
        # 팩터 가중치 로드 (DB 우선, 없으면 기본값)
        self.factor_weights = self._load_factor_weights()
        
        # 조건부 승률 캐시
        self._factor_performance_cache: Dict[str, Dict] = {}
        self._news_stats_cache: Dict[str, Dict] = {}
        
        # [v1.0.6] 섹터 정보 캐시
        self._sector_cache: Dict[str, str] = {}
        
        logger.info(f"✅ QuantScorer 초기화 완료 (시장국면: {market_regime}, 전략: {strategy_mode.value})")
    
    def _load_factor_weights(self) -> Dict[str, float]:
        """
        FACTOR_METADATA에서 가중치 로드 (없으면 기본값 사용)
        """
        weights = get_default_factor_weights()
        
        if self.db_conn is None:
            logger.debug("   (QuantScorer) DB 연결 없음, 기본 가중치 사용")
            return weights
        
        try:
            cursor = self.db_conn.cursor()
            cursor.execute("""
                SELECT FACTOR_KEY, RECOMMENDED_WEIGHT 
                FROM FACTOR_METADATA 
                WHERE MARKET_REGIME IN (%s, 'ALL')
                ORDER BY CASE WHEN MARKET_REGIME = %s THEN 0 ELSE 1 END
            """, (self.market_regime, self.market_regime))
            
            rows = cursor.fetchall()
            cursor.close()
            
            for row in rows:
                if isinstance(row, dict):
                    key, weight = row['FACTOR_KEY'], row['RECOMMENDED_WEIGHT']
                else:
                    key, weight = row[0], row[1]
                
                if key and weight is not None:
                    weights[key] = float(weight)
            
            logger.debug(f"   (QuantScorer) FACTOR_METADATA에서 {len(rows)}개 가중치 로드")
            
        except Exception as e:
            logger.warning(f"   (QuantScorer) 가중치 로드 실패, 기본값 사용: {e}")
        
        return weights
    
    def _load_factor_performance(self, stock_code: str) -> Dict:
        """
        FACTOR_PERFORMANCE에서 종목별 조건부 승률 로드
        
        계층적 조회:
        1. 개별 종목 수준 (표본 충분한 경우)
        2. 섹터 수준 (개별 종목 표본 부족 시)
        3. 전체 시장 수준 (폴백)
        """
        if stock_code in self._factor_performance_cache:
            return self._factor_performance_cache[stock_code]
        
        result = {
            'conditions': [],
            'best_win_rate': None,
            'sample_count': 0,
            'confidence': 'LOW'
        }
        
        if self.db_conn is None:
            return result
        
        try:
            cursor = self.db_conn.cursor()
            
            # 1. 개별 종목 수준 조회
            cursor.execute("""
                SELECT CONDITION_KEY, CONDITION_DESC, WIN_RATE, AVG_RETURN, 
                       SAMPLE_COUNT, CONFIDENCE_LEVEL, RECENT_WIN_RATE
                FROM FACTOR_PERFORMANCE
                WHERE TARGET_TYPE = 'STOCK' AND TARGET_CODE = %s
                AND HOLDING_DAYS = %s
                ORDER BY WIN_RATE DESC
                LIMIT 5
            """, (stock_code, self.DEFAULT_HOLDING_DAYS))
            
            rows = cursor.fetchall()
            
            if rows:
                for row in rows:
                    if isinstance(row, dict):
                        condition = {
                            'key': row['CONDITION_KEY'],
                            'desc': row['CONDITION_DESC'],
                            'win_rate': float(row['WIN_RATE']) if row['WIN_RATE'] else 0,
                            'avg_return': float(row['AVG_RETURN']) if row['AVG_RETURN'] else 0,
                            'sample_count': row['SAMPLE_COUNT'] or 0,
                            'confidence': row['CONFIDENCE_LEVEL'] or 'LOW',
                            'recent_win_rate': float(row['RECENT_WIN_RATE']) if row['RECENT_WIN_RATE'] else None,
                        }
                    else:
                        condition = {
                            'key': row[0],
                            'desc': row[1],
                            'win_rate': float(row[2]) if row[2] else 0,
                            'avg_return': float(row[3]) if row[3] else 0,
                            'sample_count': row[4] or 0,
                            'confidence': row[5] or 'LOW',
                            'recent_win_rate': float(row[6]) if row[6] else None,
                        }
                    result['conditions'].append(condition)
                
                # 가장 높은 승률 조건 선택
                best = max(result['conditions'], key=lambda x: x['win_rate'])
                result['best_win_rate'] = best['win_rate']
                result['sample_count'] = best['sample_count']
                result['confidence'] = best['confidence']
            
            cursor.close()
            
        except Exception as e:
            logger.debug(f"   (QuantScorer) {stock_code} 조건부 승률 로드 실패: {e}")
        
        self._factor_performance_cache[stock_code] = result
        return result
    
    def _load_news_stats(self, stock_code: str, news_category: str = None) -> Dict:
        """
        NEWS_FACTOR_STATS에서 뉴스 영향도 통계 로드
        """
        cache_key = f"{stock_code}:{news_category or 'ALL'}"
        if cache_key in self._news_stats_cache:
            return self._news_stats_cache[cache_key]
        
        result = {
            'win_rate_d5': None,
            'avg_return_d5': None,
            'sample_count': 0,
            'confidence': 'LOW'
        }
        
        if self.db_conn is None:
            return result
        
        try:
            cursor = self.db_conn.cursor()
            
            # 종목별 뉴스 통계 조회
            if news_category:
                cursor.execute("""
                    SELECT WIN_RATE_D5, RETURN_D5, SAMPLE_COUNT, CONFIDENCE_LEVEL
                    FROM NEWS_FACTOR_STATS
                    WHERE TARGET_CODE = %s AND NEWS_CATEGORY = %s
                    AND SENTIMENT = 'POSITIVE'
                """, (stock_code, news_category))
            else:
                cursor.execute("""
                    SELECT AVG(WIN_RATE_D5), AVG(RETURN_D5), SUM(SAMPLE_COUNT), 
                           MAX(CONFIDENCE_LEVEL)
                    FROM NEWS_FACTOR_STATS
                    WHERE TARGET_CODE = %s AND SENTIMENT = 'POSITIVE'
                """, (stock_code,))
            
            row = cursor.fetchone()
            cursor.close()
            
            if row:
                if isinstance(row, dict):
                    result['win_rate_d5'] = float(row.get('WIN_RATE_D5') or row.get('AVG(WIN_RATE_D5)') or 0)
                    result['avg_return_d5'] = float(row.get('RETURN_D5') or row.get('AVG(RETURN_D5)') or 0)
                    result['sample_count'] = row.get('SAMPLE_COUNT') or row.get('SUM(SAMPLE_COUNT)') or 0
                    result['confidence'] = row.get('CONFIDENCE_LEVEL') or row.get('MAX(CONFIDENCE_LEVEL)') or 'LOW'
                else:
                    result['win_rate_d5'] = float(row[0]) if row[0] else None
                    result['avg_return_d5'] = float(row[1]) if row[1] else None
                    result['sample_count'] = row[2] or 0
                    result['confidence'] = row[3] or 'LOW'
        
        except Exception as e:
            logger.debug(f"   (QuantScorer) {stock_code} 뉴스 통계 로드 실패: {e}")
        
        self._news_stats_cache[cache_key] = result
        return result
    
    def _get_stock_sector(self, stock_code: str) -> str:
        """
        [v1.0.6] STOCK_MASTER에서 종목의 섹터 정보 로드
        
        Returns:
            섹터명 (없으면 '미분류')
        """
        if stock_code in self._sector_cache:
            return self._sector_cache[stock_code]
        
        sector = '미분류'
        
        if self.db_conn is None:
            return sector
        
        try:
            cursor = self.db_conn.cursor()
            cursor.execute("""
                SELECT SECTOR_KOSPI200 FROM STOCK_MASTER 
                WHERE STOCK_CODE = %s
            """, (stock_code,))
            
            row = cursor.fetchone()
            cursor.close()
            
            if row:
                if isinstance(row, dict):
                    sector = row.get('SECTOR_KOSPI200') or '미분류'
                else:
                    sector = row[0] or '미분류'
        except Exception as e:
            logger.debug(f"   (QuantScorer) {stock_code} 섹터 조회 실패: {e}")
        
        self._sector_cache[stock_code] = sector
        return sector
    
    def calculate_compound_condition_bonus(self,
                                           rsi: Optional[float],
                                           foreign_net_buy: Optional[int],
                                           avg_volume: Optional[float] = None) -> Tuple[float, Dict]:
        """
        [v1.0.6] 복합 조건 보너스 점수 계산
        
        팩터 분석 결과:
        - RSI 과매도 + 외국인 순매수: 승률 55.5%, 평균수익률 1.10%
        - 거래량 급증 + 외국인 순매수: 승률 51.3%, 평균수익률 1.24%
        
        Returns:
            (보너스 점수, 상세 정보)
        """
        bonus = 0.0
        details = {
            'compound_conditions_met': [],
            'bonus_applied': 0.0,
        }
        
        # 조건 1: RSI 과매도 (RSI < 30)
        is_rsi_oversold = rsi is not None and rsi < 30
        
        # 조건 2: 외국인 순매수
        is_foreign_buying = False
        if foreign_net_buy is not None:
            if avg_volume and avg_volume > 0:
                # 거래량 대비 1% 이상 순매수
                is_foreign_buying = (foreign_net_buy / avg_volume) > 0.01
            else:
                # 절대값 기준 10만주 이상 순매수
                is_foreign_buying = foreign_net_buy > 100_000
        
        # 복합 조건 체크
        if is_rsi_oversold and is_foreign_buying:
            # RSI 과매도 + 외국인 순매수 → 55.5% 승률 → +5점 보너스
            bonus += 5.0
            details['compound_conditions_met'].append('RSI_OVERSOLD_FOREIGN_BUY')
            logger.debug(f"   (QuantScorer) 🎯 복합조건 충족: RSI과매도+외인순매수 → +5점")
        
        details['bonus_applied'] = bonus
        details['is_rsi_oversold'] = is_rsi_oversold
        details['is_foreign_buying'] = is_foreign_buying
        
        return bonus, details
    
    def calculate_momentum_score(self, 
                                 daily_prices_df: pd.DataFrame,
                                 kospi_prices_df: Optional[pd.DataFrame] = None) -> Tuple[float, Dict]:
        """
        모멘텀 점수 계산 (25점 만점)
        
        세부 구성:
        - 6개월 상대/절대 모멘텀: 15점
        - 1개월 단기 모멘텀: 5점
        - 모멘텀 안정성: 5점
        
        [v1.0.3] Claude Opus 4.5 피드백: KOSPI 벤치마크 폴백 로직 추가
        - KOSPI 데이터 없으면 절대 모멘텀으로 계산 (중립 대신)
        """
        try:
            factors = {}
            total_score = 0.0
            
            # 1. 6개월 모멘텀 (15점)
            # [v1.0.3] KOSPI 데이터 없으면 절대 모멘텀으로 폴백
            if len(daily_prices_df) >= 120:
                stock_start = float(daily_prices_df['CLOSE_PRICE'].iloc[-120])
                stock_end = float(daily_prices_df['CLOSE_PRICE'].iloc[-1])
                stock_return = (stock_end / stock_start - 1) * 100
                
                if kospi_prices_df is not None and len(kospi_prices_df) >= 120:
                    # 상대 모멘텀 (KOSPI 대비)
                    kospi_start = float(kospi_prices_df['CLOSE_PRICE'].iloc[-120])
                    kospi_end = float(kospi_prices_df['CLOSE_PRICE'].iloc[-1])
                    kospi_return = (kospi_end / kospi_start - 1) * 100
                    
                    relative_momentum_6m = stock_return - kospi_return
                    
                    # -30% ~ +30%를 0~15점으로 연속 매핑
                    momentum_6m_score = max(0, min(15, 7.5 + relative_momentum_6m * 0.25))
                    
                    factors['relative_momentum_6m'] = round(relative_momentum_6m, 2)
                    factors['momentum_type'] = 'relative'
                else:
                    # [v1.0.3] 폴백: 절대 모멘텀 사용
                    absolute_momentum_6m = stock_return
                    
                    # -20% ~ +40%를 0~15점으로 연속 매핑 (상승에 더 긍정적)
                    momentum_6m_score = max(0, min(15, 5 + absolute_momentum_6m * 0.25))
                    
                    factors['absolute_momentum_6m'] = round(absolute_momentum_6m, 2)
                    factors['momentum_type'] = 'absolute (KOSPI 없음)'
                
                total_score += momentum_6m_score
                factors['momentum_6m_score'] = round(momentum_6m_score, 2)
            else:
                total_score += 7.5  # 데이터 부족시만 중립
                factors['momentum_6m_score'] = 7.5
                factors['momentum_6m_note'] = '데이터 부족 (120일 미만)'
            
            # 2. 1개월 단기 모멘텀 (5점)
            # [v1.0.3] KOSPI 없어도 절대 모멘텀으로 계산
            if len(daily_prices_df) >= 20:
                stock_return_1m = (daily_prices_df['CLOSE_PRICE'].iloc[-1] / daily_prices_df['CLOSE_PRICE'].iloc[-20] - 1) * 100
                
                if kospi_prices_df is not None and len(kospi_prices_df) >= 20:
                    kospi_return_1m = (kospi_prices_df['CLOSE_PRICE'].iloc[-1] / kospi_prices_df['CLOSE_PRICE'].iloc[-20] - 1) * 100
                    relative_momentum_1m = stock_return_1m - kospi_return_1m
                    
                    # -10% ~ +10%를 0~5점으로 연속 매핑
                    momentum_1m_score = max(0, min(5, 2.5 + relative_momentum_1m * 0.25))
                    factors['relative_momentum_1m'] = round(relative_momentum_1m, 2)
                else:
                    # [v1.0.3] 폴백: 절대 모멘텀
                    momentum_1m_score = max(0, min(5, 2.5 + stock_return_1m * 0.25))
                    factors['absolute_momentum_1m'] = round(stock_return_1m, 2)
                
                total_score += momentum_1m_score
                factors['momentum_1m_score'] = round(momentum_1m_score, 2)
            else:
                total_score += 2.5
                factors['momentum_1m_score'] = 2.5
            
            # 3. 모멘텀 안정성 (5점)
            if len(daily_prices_df) >= 120:
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
                    consistency_score = consistency * 5
                    total_score += consistency_score
                    
                    factors['momentum_consistency'] = round(consistency, 2)
                    factors['consistency_score'] = round(consistency_score, 2)
                else:
                    total_score += 2.5
                    factors['consistency_score'] = 2.5
            else:
                total_score += 2.5
                factors['consistency_score'] = 2.5
            
            return total_score, factors
            
        except Exception as e:
            logger.error(f"   (QuantScorer) 모멘텀 점수 계산 오류: {e}", exc_info=True)
            return 12.5, {'error': str(e)}
    
    def calculate_quality_score(self, 
                                roe: Optional[float],
                                sales_growth: Optional[float],
                                eps_growth: Optional[float],
                                daily_prices_df: pd.DataFrame) -> Tuple[float, Dict]:
        """
        품질 점수 계산 (20점 만점)
        
        세부 구성:
        - ROE (수익성): 10점
        - 성장성 (매출+EPS): 7점
        - 이익 안정성: 3점
        """
        try:
            factors = {}
            total_score = 0.0
            
            # 1. ROE (수익성) - 10점
            if roe is not None:
                # ROE: -20% ~ +40%를 0~10점으로 연속 매핑
                roe_score = max(0, min(10, 5 + roe * 0.167))
                total_score += roe_score
                
                factors['roe'] = round(roe, 2)
                factors['roe_score'] = round(roe_score, 2)
            else:
                total_score += 5  # 중립
                factors['roe_score'] = 5
                factors['roe_note'] = '데이터 없음'
            
            # 2. 성장성 (매출 + EPS) - 7점
            growth_score = 0.0
            
            # 2-1. 매출 성장률 (3.5점)
            if sales_growth is not None:
                sales_score = max(0, min(3.5, 1.75 + sales_growth * 0.0875))
                growth_score += sales_score
                factors['sales_growth'] = round(sales_growth, 2)
                factors['sales_score'] = round(sales_score, 2)
            else:
                growth_score += 1.75
                factors['sales_score'] = 1.75
            
            # 2-2. EPS 성장률 (3.5점)
            if eps_growth is not None:
                eps_score = max(0, min(3.5, 1.75 + eps_growth * 0.058))
                growth_score += eps_score
                factors['eps_growth'] = round(eps_growth, 2)
                factors['eps_score'] = round(eps_score, 2)
            else:
                growth_score += 1.75
                factors['eps_score'] = 1.75
            
            total_score += growth_score
            
            # 3. 이익 안정성 (3점) - 가격 변동성으로 대체
            if len(daily_prices_df) >= 60:
                returns = daily_prices_df['CLOSE_PRICE'].pct_change().dropna()
                volatility = returns.std() * 100
                
                # 변동성: 0~5%를 3~0점으로 매핑 (낮을수록 좋음)
                stability_score = max(0, 3 - volatility * 0.6)
                total_score += stability_score
                
                factors['volatility'] = round(volatility, 2)
                factors['stability_score'] = round(stability_score, 2)
            else:
                total_score += 1.5
                factors['stability_score'] = 1.5
            
            return total_score, factors
            
        except Exception as e:
            logger.error(f"   (QuantScorer) 품질 점수 계산 오류: {e}", exc_info=True)
            return 10.0, {'error': str(e)}
    
    def calculate_value_score(self, 
                             pbr: Optional[float],
                             per: Optional[float]) -> Tuple[float, Dict]:
        """
        가치 점수 계산 (15점 만점)
        
        세부 구성:
        - PBR: 7.5점
        - PER: 7.5점
        """
        try:
            factors = {}
            total_score = 0.0
            
            # 1. PBR (7.5점) - 낮을수록 좋음
            if pbr is not None and pbr > 0:
                # PBR: 0.5~3.0을 7.5~0점으로 연속 매핑
                pbr_score = max(0, min(7.5, 7.5 - (pbr - 0.5) * 3))
                total_score += pbr_score
                
                factors['pbr'] = round(pbr, 2)
                factors['pbr_score'] = round(pbr_score, 2)
            else:
                total_score += 3.75  # 중립
                factors['pbr_score'] = 3.75
                factors['pbr_note'] = '데이터 없음'
            
            # 2. PER (7.5점) - 낮을수록 좋음 (적자 기업 제외)
            if per is not None and per > 0:
                # PER: 5~30을 7.5~0점으로 연속 매핑
                per_score = max(0, min(7.5, 7.5 - (per - 5) * 0.3))
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
            logger.error(f"   (QuantScorer) 가치 점수 계산 오류: {e}", exc_info=True)
            return 7.5, {'error': str(e)}
    
    def calculate_technical_score(self, 
                                   daily_prices_df: pd.DataFrame,
                                   sector: str = '미분류') -> Tuple[float, Dict]:
        """
        기술적 점수 계산 (10점 만점)
        
        세부 구성:
        - 거래량 추세: 4점
        - RSI: 3점 (섹터별 가중치 적용)
        - 볼린저 밴드: 3점
        
        [v1.0.6] 섹터별 RSI 가중치 적용:
        - 조선운송: x1.3 (60.9% 적중률)
        - 금융: x1.25 (60.1% 적중률)
        - 건설기계: x0.7 (49.8% 적중률)
        """
        try:
            factors = {}
            total_score = 0.0
            
            # 1. 거래량 추세 (4점)
            if 'VOLUME' in daily_prices_df.columns and len(daily_prices_df) >= 25:
                recent_volume = daily_prices_df['VOLUME'].tail(5).mean()
                past_volume = daily_prices_df['VOLUME'].iloc[-25:-5].mean()
                
                if past_volume > 0:
                    volume_ratio = recent_volume / past_volume
                    # 0.5배~3.0배를 0~4점으로 연속 매핑
                    volume_score = max(0, min(4, (volume_ratio - 0.5) * 1.6))
                    total_score += volume_score
                    
                    factors['volume_ratio'] = round(volume_ratio, 2)
                    factors['volume_score'] = round(volume_score, 2)
                else:
                    total_score += 2
                    factors['volume_score'] = 2
            else:
                total_score += 2
                factors['volume_score'] = 2
            
            # 2. RSI (3점)
            try:
                from shared import strategy
                rsi = strategy.calculate_rsi(daily_prices_df, period=14)
            except:
                rsi = self._calculate_rsi(daily_prices_df, period=14)
            
            if rsi is not None:
                # RSI 과매도 구간(30~40)에 높은 점수
                if rsi <= 30:
                    rsi_score = 3
                elif rsi <= 50:
                    rsi_score = 3 - (rsi - 30) * 0.075
                elif rsi <= 70:
                    rsi_score = 1.5 - (rsi - 50) * 0.05
                else:
                    rsi_score = max(0, 0.5 - (rsi - 70) * 0.025)
                
                # [v1.0.6] 섹터별 RSI 가중치 적용
                sector_multiplier = self.SECTOR_RSI_MULTIPLIER.get(sector, 1.0)
                rsi_score_adjusted = min(3.0, rsi_score * sector_multiplier)  # 최대 3점 유지
                
                total_score += rsi_score_adjusted
                factors['rsi'] = round(rsi, 2)
                factors['rsi_score_raw'] = round(rsi_score, 2)
                factors['rsi_score'] = round(rsi_score_adjusted, 2)
                factors['sector'] = sector
                factors['sector_rsi_multiplier'] = sector_multiplier
                
                if sector_multiplier != 1.0:
                    logger.debug(f"   (QuantScorer) 섹터별 RSI 조정: {sector} x{sector_multiplier}")
            else:
                total_score += 1.5
                factors['rsi_score'] = 1.5
            
            # 3. 볼린저 밴드 (3점)
            if len(daily_prices_df) >= 20:
                close_prices = daily_prices_df['CLOSE_PRICE']
                ma20 = close_prices.rolling(window=20).mean().iloc[-1]
                std20 = close_prices.rolling(window=20).std().iloc[-1]
                
                bb_upper = ma20 + 2 * std20
                bb_lower = ma20 - 2 * std20
                current_price = close_prices.iloc[-1]
                
                if bb_upper > bb_lower:
                    bb_position = (current_price - bb_lower) / (bb_upper - bb_lower)
                    # 하단에 가까울수록 높은 점수
                    bb_score = max(0, 3 - bb_position * 3)
                    total_score += bb_score
                    
                    factors['bb_position'] = round(bb_position, 2)
                    factors['bb_score'] = round(bb_score, 2)
                else:
                    total_score += 1.5
                    factors['bb_score'] = 1.5
            else:
                total_score += 1.5
                factors['bb_score'] = 1.5
            
            return total_score, factors
            
        except Exception as e:
            logger.error(f"   (QuantScorer) 기술적 점수 계산 오류: {e}", exc_info=True)
            return 5.0, {'error': str(e)}
    
    def _calculate_rsi(self, df: pd.DataFrame, period: int = 14) -> Optional[float]:
        """RSI 직접 계산 (strategy 모듈 임포트 실패 시 폴백)"""
        try:
            close = df['CLOSE_PRICE']
            delta = close.diff()
            gain = delta.where(delta > 0, 0).rolling(window=period).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
            
            rs = gain / loss
            rsi = 100 - (100 / (1 + rs))
            return float(rsi.iloc[-1])
        except:
            return None
    
    # [v1.0.5] 뉴스 역신호 카테고리 (팩터 분석 결과)
    # 수주: 43.7% 승률 (역신호)
    # 배당: 37.6% 승률 (강한 역신호)
    NEWS_REVERSE_SIGNAL_CATEGORIES = {'수주', '배당', '자사주', '주주환원'}
    
    def calculate_news_timing_signal(self,
                                      news_category: str,
                                      current_sentiment_score: float) -> Tuple[str, str, int]:
        """
        [v1.0] 뉴스 기반 시간축 판단 (3 AI 합의)
        
        "뉴스 뜨면 단기 역신호, 장기 순신호"
        → 즉시 매수 금지, 눌림목 대기
        
        Args:
            news_category: 뉴스 카테고리
            current_sentiment_score: 현재 감성 점수 (0~100)
            
        Returns:
            (signal, reason, recommended_holding_days)
            - signal: BUY_NOW, WAIT_DIP, SELL_NEWS, NEUTRAL
            - reason: 판단 근거
            - recommended_holding_days: 권장 보유기간
        """
        if news_category not in self.NEWS_TIME_EFFECT:
            return "NEUTRAL", "뉴스 카테고리 통계 없음", 5
        
        effect = self.NEWS_TIME_EFFECT[news_category]
        d5_win = effect['d5_win_rate']
        d60_win = effect['d60_win_rate']
        d60_ret = effect['d60_return']
        
        # 긍정적 뉴스인 경우 (sentiment >= 70)
        if current_sentiment_score >= 70:
            if d5_win < 0.50 and d60_win >= 0.60:
                # 단기 역신호, 장기 순신호 → 눌림목 대기
                return (
                    "WAIT_DIP",
                    f"⚠️ {news_category} 뉴스: 단기 승률 {d5_win*100:.0f}% (역신호) → "
                    f"눌림목 대기 후 매수 권장 (D+60 승률 {d60_win*100:.0f}%, 기대수익 {d60_ret*100:.1f}%)",
                    60
                )
            elif d5_win >= 0.55:
                # 단기에도 유효한 신호
                return (
                    "BUY_NOW",
                    f"✅ {news_category} 뉴스: 단기 승률 {d5_win*100:.0f}% → 즉시 매수 가능",
                    5
                )
            else:
                return (
                    "NEUTRAL",
                    f"📊 {news_category} 뉴스: 단기 승률 {d5_win*100:.0f}%, 장기 승률 {d60_win*100:.0f}%",
                    20
                )
        
        # 부정적 뉴스인 경우 (sentiment < 30)
        elif current_sentiment_score < 30:
            return (
                "SELL_NEWS",
                f"🔴 {news_category} 악재 뉴스 → 손절 고려",
                0
            )
        
        return "NEUTRAL", "중립적 뉴스", 5
    
    def calculate_news_stat_score(self, 
                                  stock_code: str,
                                  current_sentiment_score: float = 50,
                                  news_category: str = None) -> Tuple[float, Dict]:
        """
        뉴스 통계 점수 계산 (15점 만점)
        
        [v1.0.5] 팩터 분석 결과 반영:
        - 전체 뉴스 승률 47.3% (역신호!)
        - 수주: 43.7%, 배당: 37.6% (강한 역신호)
        - "뉴스 보고 매수하면 고점에 물린다"
        
        수정된 로직:
        - 승률 기반 점수: 10점 → 7점 (역신호 반영 축소)
        - 현재 감성 점수: 5점 → 3점 (역신호 반영 축소)
        - 역신호 카테고리: 패널티 적용
        
        Args:
            stock_code: 종목 코드
            current_sentiment_score: 현재 뉴스 감성 점수 (0~100)
            news_category: 뉴스 카테고리 (수주, 실적 등)
        """
        try:
            factors = {}
            total_score = 0.0
            
            # [v1.0.5] 역신호 카테고리 체크
            is_reverse_signal = news_category in self.NEWS_REVERSE_SIGNAL_CATEGORIES
            
            # 1. 뉴스 통계 기반 점수 (7점) - 기존 10점에서 축소
            news_stats = self._load_news_stats(stock_code, news_category)
            
            if news_stats['win_rate_d5'] is not None:
                # 승률을 점수로 변환 (50%=0점, 100%=7점)
                win_rate = news_stats['win_rate_d5']
                
                # 신뢰도 가중치 적용
                confidence_weight = get_confidence_weight(news_stats['sample_count'])
                
                # [v1.0.5] 역신호 반영: 승률 50% 미만이면 패널티
                if win_rate < 0.5:
                    # 역신호: 승률 50% 미만 → 음수 점수 (-3점까지)
                    base_score = max(-3, (win_rate - 0.5) * 14)  # 37.6%면 약 -1.7점
                    factors['reverse_signal_penalty'] = True
                else:
                    # 정상 신호: 승률 50% 이상 → 양수 점수 (최대 7점)
                    base_score = max(0, min(7, (win_rate - 0.5) * 14))
                
                news_stat_score = base_score * confidence_weight
                total_score += news_stat_score
                
                factors['news_win_rate'] = round(win_rate, 4)
                factors['news_sample_count'] = news_stats['sample_count']
                factors['news_confidence'] = news_stats['confidence']
                factors['news_stat_score'] = round(news_stat_score, 2)
                factors['confidence_weight'] = confidence_weight
            else:
                # 통계 없으면 중립 (3.5점) - 기존 5점에서 축소
                total_score += 3.5
                factors['news_stat_score'] = 3.5
                factors['news_stat_note'] = '통계 데이터 없음'
            
            # 2. 현재 감성 점수 보정 (3점) - 기존 5점에서 축소
            # 0~100을 0~3점으로 변환
            sentiment_score = current_sentiment_score / 100 * 3
            
            # [v1.0.5] 역신호 카테고리 패널티
            if is_reverse_signal and current_sentiment_score >= 70:
                # "뉴스 나왔으니 이미 늦었다" - 호재 뉴스에 패널티
                sentiment_score = sentiment_score * 0.5  # 50% 감소
                factors['reverse_signal_category'] = news_category
                factors['reverse_signal_warning'] = '⚠️ 역신호 카테고리: 추격매수 주의'
                logger.debug(f"   (QuantScorer) 역신호 카테고리 패널티 적용: {news_category}")
            
            total_score += sentiment_score
            
            factors['current_sentiment'] = current_sentiment_score
            factors['sentiment_score'] = round(sentiment_score, 2)
            factors['is_reverse_signal'] = is_reverse_signal
            
            # [v1.0.5] 최소 0점 보장 (패널티로 음수 되지 않도록)
            total_score = max(0, total_score)
            
            return total_score, factors
            
        except Exception as e:
            logger.error(f"   (QuantScorer) 뉴스 통계 점수 계산 오류: {e}", exc_info=True)
            return 5.0, {'error': str(e)}  # 중립값 5점
    
    def calculate_supply_demand_score(self,
                                      foreign_net_buy: Optional[int] = None,
                                      institution_net_buy: Optional[int] = None,
                                      foreign_holding_ratio: Optional[float] = None,
                                      avg_volume: Optional[float] = None) -> Tuple[float, Dict]:
        """
        수급 점수 계산 (15점 만점)
        
        세부 구성:
        - 외국인 순매수: 7점
        - 기관 순매수: 5점
        - 외국인 보유비중: 3점
        
        [v1.0.3] Claude Opus 4.5 피드백: 종목별 거래량 대비 정규화 적용
        - 기존: 절대 주수 기준 (삼성전자와 소형주에 동일 기준)
        - 개선: 평균 거래량 대비 비율로 정규화
        """
        try:
            factors = {}
            total_score = 0.0
            
            # [v1.0.3] 거래량 대비 정규화 기준 설정
            # avg_volume이 있으면 거래량 대비 비율로, 없으면 기존 절대값 방식
            use_volume_normalized = avg_volume is not None and avg_volume > 0
            
            # 1. 외국인 순매수 (7점)
            if foreign_net_buy is not None:
                if use_volume_normalized:
                    # [v1.0.3] 거래량 대비 비율로 정규화
                    # 평균 거래량의 -5% ~ +5%를 0~7점으로 매핑
                    foreign_ratio = foreign_net_buy / avg_volume
                    foreign_score = max(0, min(7, 3.5 + foreign_ratio / 0.05 * 3.5))
                    factors['foreign_ratio'] = round(foreign_ratio * 100, 2)
                    factors['normalize_method'] = 'volume_ratio'
                else:
                    # 기존 방식: 절대 주수 기준
                    # 순매수: -100만주 ~ +100만주를 0~7점으로 매핑
                    foreign_score = max(0, min(7, 3.5 + foreign_net_buy / 1_000_000 * 3.5))
                    factors['normalize_method'] = 'absolute'
                
                total_score += foreign_score
                factors['foreign_net_buy'] = foreign_net_buy
                factors['foreign_score'] = round(foreign_score, 2)
            else:
                total_score += 3.5  # 중립
                factors['foreign_score'] = 3.5
            
            # 2. 기관 순매수 (5점)
            if institution_net_buy is not None:
                if use_volume_normalized:
                    # [v1.0.3] 거래량 대비 비율로 정규화
                    # 평균 거래량의 -3% ~ +3%를 0~5점으로 매핑
                    inst_ratio = institution_net_buy / avg_volume
                    institution_score = max(0, min(5, 2.5 + inst_ratio / 0.03 * 2.5))
                    factors['institution_ratio'] = round(inst_ratio * 100, 2)
                else:
                    # 기존 방식
                    institution_score = max(0, min(5, 2.5 + institution_net_buy / 500_000 * 2.5))
                
                total_score += institution_score
                factors['institution_net_buy'] = institution_net_buy
                factors['institution_score'] = round(institution_score, 2)
            else:
                total_score += 2.5  # 중립
                factors['institution_score'] = 2.5
            
            # 3. 외국인 보유비중 (3점)
            if foreign_holding_ratio is not None:
                # 보유비중: 0~50%를 0~3점으로 매핑
                holding_score = min(3, foreign_holding_ratio / 50 * 3)
                total_score += holding_score
                
                factors['foreign_holding_ratio'] = round(foreign_holding_ratio, 2)
                factors['holding_score'] = round(holding_score, 2)
            else:
                total_score += 1.5  # 중립
                factors['holding_score'] = 1.5
            
            if use_volume_normalized:
                factors['avg_volume'] = avg_volume
            
            return total_score, factors
            
        except Exception as e:
            logger.error(f"   (QuantScorer) 수급 점수 계산 오류: {e}", exc_info=True)
            return 7.5, {'error': str(e)}
    
    def calculate_total_quant_score(self,
                                    stock_code: str,
                                    stock_name: str,
                                    daily_prices_df: pd.DataFrame,
                                    kospi_prices_df: Optional[pd.DataFrame] = None,
                                    roe: Optional[float] = None,
                                    sales_growth: Optional[float] = None,
                                    eps_growth: Optional[float] = None,
                                    pbr: Optional[float] = None,
                                    per: Optional[float] = None,
                                    current_sentiment_score: float = 50,
                                    news_category: str = None,
                                    foreign_net_buy: Optional[int] = None,
                                    institution_net_buy: Optional[int] = None,
                                    foreign_holding_ratio: Optional[float] = None) -> QuantScoreResult:
        """
        종합 정량 점수 계산 (100점 만점)
        
        점수 구성:
        - 모멘텀: 25점
        - 품질: 20점
        - 가치: 15점
        - 기술적: 10점
        - 뉴스 통계: 15점
        - 수급: 15점
        
        [v1.0.1] Gemini 피드백 반영:
        - 데이터 부족 시 is_valid=False 설정하여 "묻어가기" 합격 방지
        
        Returns:
            QuantScoreResult 객체
        """
        # [v1.0.1] 필수 데이터 유효성 검사
        MIN_PRICE_DATA_DAYS = 30  # 최소 30일 데이터 필요
        
        if daily_prices_df is None or daily_prices_df.empty:
            logger.debug(f"   ⚠️ [Quant] {stock_name}({stock_code}) 일봉 데이터 없음 → is_valid=False")
            return QuantScoreResult(
                stock_code=stock_code,
                stock_name=stock_name,
                total_score=0.0,  # 데이터 없으면 0점 (중립 50점 아님!)
                momentum_score=0.0,
                quality_score=0.0,
                value_score=0.0,
                technical_score=0.0,
                news_stat_score=0.0,
                supply_demand_score=0.0,
                matched_conditions=[],
                condition_win_rate=None,
                condition_sample_count=0,
                condition_confidence='LOW',
                is_valid=False,
                invalid_reason='일봉 데이터 없음',
                details={'error': '일봉 데이터 없음'},
            )
        
        if len(daily_prices_df) < MIN_PRICE_DATA_DAYS:
            logger.debug(f"   ⚠️ [Quant] {stock_name}({stock_code}) 데이터 부족 ({len(daily_prices_df)}일 < {MIN_PRICE_DATA_DAYS}일) → is_valid=False")
            return QuantScoreResult(
                stock_code=stock_code,
                stock_name=stock_name,
                total_score=0.0,  # 데이터 부족하면 0점
                momentum_score=0.0,
                quality_score=0.0,
                value_score=0.0,
                technical_score=0.0,
                news_stat_score=0.0,
                supply_demand_score=0.0,
                matched_conditions=[],
                condition_win_rate=None,
                condition_sample_count=0,
                condition_confidence='LOW',
                is_valid=False,
                invalid_reason=f'데이터 부족 ({len(daily_prices_df)}일)',
                details={'error': f'데이터 부족 ({len(daily_prices_df)}일 < {MIN_PRICE_DATA_DAYS}일)'},
            )
        
        try:
            all_details = {}
            
            # 1. 모멘텀 점수 (25점)
            momentum_score, momentum_details = self.calculate_momentum_score(
                daily_prices_df, kospi_prices_df
            )
            all_details['momentum'] = momentum_details
            
            # 2. 품질 점수 (20점)
            quality_score, quality_details = self.calculate_quality_score(
                roe, sales_growth, eps_growth, daily_prices_df
            )
            all_details['quality'] = quality_details
            
            # 3. 가치 점수 (15점)
            value_score, value_details = self.calculate_value_score(pbr, per)
            all_details['value'] = value_details
            
            # [v1.0.6] 섹터 정보 조회 (RSI 가중치용)
            sector = self._get_stock_sector(stock_code)
            
            # 4. 기술적 점수 (10점) - 섹터별 RSI 가중치 적용
            technical_score, technical_details = self.calculate_technical_score(daily_prices_df, sector)
            all_details['technical'] = technical_details
            
            # 5. 뉴스 통계 점수 (15점)
            news_stat_score, news_details = self.calculate_news_stat_score(
                stock_code, current_sentiment_score, news_category
            )
            all_details['news'] = news_details
            
            # 6. 수급 점수 (15점)
            # [v1.0.3] 종목별 평균 거래량 계산 (정규화용)
            avg_volume = None
            if 'VOLUME' in daily_prices_df.columns and len(daily_prices_df) >= 20:
                avg_volume = daily_prices_df['VOLUME'].iloc[-20:].mean()
            
            supply_demand_score, supply_details = self.calculate_supply_demand_score(
                foreign_net_buy, institution_net_buy, foreign_holding_ratio, avg_volume
            )
            all_details['supply_demand'] = supply_details
            
            # [v1.0.6] 복합조건 보너스 계산
            rsi = technical_details.get('rsi')
            compound_bonus, compound_details = self.calculate_compound_condition_bonus(
                rsi, foreign_net_buy, avg_volume
            )
            all_details['compound_condition'] = compound_details
            
            # 총점 계산 (100점 만점 + 복합조건 보너스 최대 5점)
            total_score = (
                momentum_score +
                quality_score +
                value_score +
                technical_score +
                news_stat_score +
                supply_demand_score +
                compound_bonus  # [v1.0.6] 복합조건 보너스
            )
            
            # [v1.0.6] 장기 보유 추천 플래그
            # 단기(D+5)에서는 역신호지만 장기(D+60)에서 호재인 뉴스
            is_long_term_hold_recommended = (
                news_category in self.NEWS_LONG_TERM_POSITIVE and
                current_sentiment_score >= 70
            )
            all_details['long_term_hold_recommended'] = is_long_term_hold_recommended
            all_details['sector'] = sector
            
            # 조건부 승률 정보 로드
            factor_perf = self._load_factor_performance(stock_code)
            matched_conditions = [c['key'] for c in factor_perf['conditions']]
            
            # [v1.0.2] 뉴스 통계 정보 추출 (GPT 피드백 반영)
            news_win_rate = news_details.get('news_win_rate')
            news_sample = news_details.get('news_sample_count', 0)
            news_conf = news_details.get('news_confidence', 'LOW')
            
            # ==========================================================
            # [v1.0] Dual Track 점수 계산 (3 AI 합의)
            # ==========================================================
            
            # 뉴스 시간축 판단
            news_timing_signal, news_timing_reason, recommended_holding = self.calculate_news_timing_signal(
                news_category or '기타', current_sentiment_score
            )
            
            # --- 단기 스나이퍼 점수 (D+5) ---
            # RSI+외인 복합조건 중심 (승률 55.5%)
            is_rsi_oversold = rsi is not None and rsi < 30
            is_foreign_buying = compound_details.get('is_foreign_buying', False)
            
            short_term_score = 0.0
            # 복합조건 충족 시 대폭 가산 (35점)
            if is_rsi_oversold and is_foreign_buying:
                short_term_score += 35
            elif is_rsi_oversold:
                short_term_score += 20
            elif is_foreign_buying:
                short_term_score += 15
            
            # 섹터별 RSI 효과 (금융/조선 우대)
            sector_rsi_mult = self.SECTOR_RSI_MULTIPLIER.get(sector, 1.0)
            if sector_rsi_mult >= 1.2:  # 금융, 조선운송
                short_term_score += 10
            elif sector_rsi_mult <= 0.8:  # 건설기계
                short_term_score -= 10
            
            # 수급 (20점)
            short_term_score += supply_demand_score * (20/15)
            
            # ROE (10점)
            short_term_score += quality_score * (10/20)
            
            # 뉴스 단기 패널티 (역신호!)
            if news_category in self.NEWS_REVERSE_SIGNAL_CATEGORIES and current_sentiment_score >= 70:
                short_term_score -= 15  # 단기 추격매수 페널티
            
            short_term_score = max(0, min(100, short_term_score))
            
            # --- 장기 헌터 점수 (D+60) ---
            # ROE + 뉴스 눌림목 중심 (승률 65~72%)
            long_term_score = 0.0
            
            # ROE (30점) - D+60 적중률 65.6%
            roe_val = quality_details.get('roe', 0)
            if roe_val is not None and roe_val > 15:
                long_term_score += 30
            elif roe_val is not None and roe_val > 10:
                long_term_score += 20
            elif roe_val is not None and roe_val > 5:
                long_term_score += 10
            
            # 뉴스 장기효과 (25점) - 수주 72.7%, 실적 64.8%
            if news_category in self.NEWS_TIME_EFFECT:
                effect = self.NEWS_TIME_EFFECT[news_category]
                d60_win = effect['d60_win_rate']
                if d60_win >= 0.70:
                    long_term_score += 25
                elif d60_win >= 0.60:
                    long_term_score += 18
                elif d60_win >= 0.55:
                    long_term_score += 10
            
            # RSI (15점) - D+60 적중률 60.1%
            if is_rsi_oversold:
                long_term_score += 15
            elif rsi is not None and rsi < 40:
                long_term_score += 8
            
            # PER 가치 (10점) - D+60 적중률 59.9%
            per_val = value_details.get('per', 0)
            if per_val is not None and 5 < per_val < 15:
                long_term_score += 10
            elif per_val is not None and per_val < 20:
                long_term_score += 5
            
            # 수급 (10점)
            long_term_score += supply_demand_score * (10/15)
            
            long_term_score = max(0, min(100, long_term_score))
            
            # --- 등급 및 추천 부여 ---
            def get_grade_and_rec(score):
                if score >= 80: return "A", "강력매수"
                elif score >= 65: return "B", "매수"
                elif score >= 50: return "C", "관망"
                elif score >= 35: return "D", "주의"
                else: return "F", "회피"
            
            short_grade, short_rec = get_grade_and_rec(short_term_score)
            long_grade, long_rec = get_grade_and_rec(long_term_score)
            
            # 눌림목 대기 시그널이면 단기 추천 하향
            if news_timing_signal == "WAIT_DIP":
                short_rec = "⚠️ 눌림목 대기"
                recommended_holding = 60
            
            all_details['dual_track'] = {
                'short_term_score': round(short_term_score, 2),
                'short_term_grade': short_grade,
                'long_term_score': round(long_term_score, 2),
                'long_term_grade': long_grade,
                'news_timing_signal': news_timing_signal,
                'recommended_holding_days': recommended_holding,
            }
            
            return QuantScoreResult(
                stock_code=stock_code,
                stock_name=stock_name,
                total_score=round(total_score, 2),
                momentum_score=round(momentum_score, 2),
                quality_score=round(quality_score, 2),
                value_score=round(value_score, 2),
                technical_score=round(technical_score, 2),
                news_stat_score=round(news_stat_score, 2),
                supply_demand_score=round(supply_demand_score, 2),
                matched_conditions=matched_conditions,
                condition_win_rate=factor_perf['best_win_rate'],
                condition_sample_count=factor_perf['sample_count'],
                condition_confidence=factor_perf['confidence'],
                news_stat_win_rate=news_win_rate,
                news_stat_sample_count=news_sample,
                news_stat_confidence=news_conf,
                # [v1.0.6] 복합조건 및 섹터
                compound_bonus=round(compound_bonus, 2),
                compound_conditions=compound_details.get('compound_conditions_met', []),
                sector=sector,
                is_long_term_hold_recommended=is_long_term_hold_recommended,
                # [v1.0] Dual Track 점수 (3 AI 합의)
                short_term_score=round(short_term_score, 2),
                short_term_grade=short_grade,
                short_term_recommendation=short_rec,
                long_term_score=round(long_term_score, 2),
                long_term_grade=long_grade,
                long_term_recommendation=long_rec,
                news_timing_signal=news_timing_signal,
                news_timing_reason=news_timing_reason,
                recommended_holding_days=recommended_holding,
                details=all_details,
            )
            
        except Exception as e:
            logger.error(f"   (QuantScorer) {stock_code} 종합 점수 계산 오류: {e}", exc_info=True)
            # [v1.0.1] 예외 발생 시에도 is_valid=False 설정
            return QuantScoreResult(
                stock_code=stock_code,
                stock_name=stock_name,
                total_score=0.0,  # 오류 시 0점 (중립 50점 아님!)
                momentum_score=0.0,
                quality_score=0.0,
                value_score=0.0,
                technical_score=0.0,
                news_stat_score=0.0,
                supply_demand_score=0.0,
                matched_conditions=[],
                condition_win_rate=None,
                condition_sample_count=0,
                condition_confidence='LOW',
                is_valid=False,
                invalid_reason=f'계산 오류: {str(e)[:50]}',
                details={'error': str(e)},
            )
    
    def filter_candidates(self, 
                          results: List[QuantScoreResult],
                          cutoff_ratio: float = None) -> List[QuantScoreResult]:
        """
        정량 점수 기준 1차 필터링 (하위 N% 탈락)
        
        [v1.0.1] Gemini 피드백 반영:
        - is_valid=False인 종목은 필터링에서 제외 (묻어가기 방지)
        
        Args:
            results: QuantScoreResult 리스트
            cutoff_ratio: 탈락 비율 (기본값: 0.5 = 하위 50%)
        
        Returns:
            통과한 종목 리스트 (순위 포함)
        """
        if not results:
            return []
        
        if cutoff_ratio is None:
            cutoff_ratio = self.DEFAULT_FILTER_CUTOFF
        
        # [v1.0.1] 유효한 결과만 필터링 대상으로 (묻어가기 방지)
        valid_results = [r for r in results if r.is_valid]
        invalid_results = [r for r in results if not r.is_valid]
        
        if invalid_results:
            invalid_reasons = {}
            for r in invalid_results:
                reason = r.invalid_reason or '알 수 없음'
                invalid_reasons[reason] = invalid_reasons.get(reason, 0) + 1
            logger.info(f"   (QuantScorer) ⚠️ 데이터 부족으로 제외: {len(invalid_results)}개 "
                       f"(사유: {invalid_reasons})")
        
        if not valid_results:
            logger.warning("   (QuantScorer) ⚠️ 유효한 종목이 없습니다!")
            return []
        
        # 점수 기준 내림차순 정렬 (유효한 결과만)
        sorted_results = sorted(valid_results, key=lambda x: x.total_score, reverse=True)
        
        # 순위 부여
        for i, result in enumerate(sorted_results):
            result.rank = i + 1
        
        # 상위 N% 통과
        pass_count = int(len(sorted_results) * (1 - cutoff_ratio))
        pass_count = max(1, pass_count)  # 최소 1개
        
        passed_results = sorted_results[:pass_count]
        for result in passed_results:
            result.is_passed_filter = True
        
        logger.info(f"   (QuantScorer) 필터링 완료: {len(passed_results)}/{len(valid_results)}개 통과 "
                   f"(상위 {(1-cutoff_ratio)*100:.0f}%, 전체 {len(results)}개 중 유효 {len(valid_results)}개)")
        
        return passed_results
    
    def save_daily_scores(self, 
                          results: List[QuantScoreResult],
                          market_regime: str = 'ALL',
                          score_date: datetime = None) -> int:
        """
        [v1.0.2] DAILY_QUANT_SCORE 테이블에 일별 점수 저장
        
        GPT 피드백: "최종 선정된 종목을 DAILY_QUANT_SCORE에 저장하는 부분 미구현" 해결
        [v1.0.3] Claude Opus 4.5 피드백: Oracle MERGE INTO 호환성 추가
        
        역추적(Backtrace)을 위한 점수 기록:
        - "Scout가 왜 이 종목을 뽑았지?" 추적 가능
        - 백테스트용 데이터 축적
        
        Args:
            results: QuantScoreResult 리스트
            market_regime: 시장 국면
            score_date: 점수 산출일 (기본값: 오늘)
        
        Returns:
            저장된 레코드 수
        """
        if score_date is None:
            score_date = datetime.now(timezone.utc).date()
        
        saved_count = 0
        
        try:
            cursor = self.db_connection.cursor()
            
            columns = [
                'SCORE_DATE', 'STOCK_CODE', 'STOCK_NAME',
                'TOTAL_QUANT_SCORE',
                'MOMENTUM_SCORE', 'QUALITY_SCORE', 'VALUE_SCORE',
                'TECHNICAL_SCORE', 'NEWS_STAT_SCORE', 'SUPPLY_DEMAND_SCORE',
                'MATCHED_CONDITION', 'CONDITION_WIN_RATE', 'CONDITION_SAMPLE_COUNT',
                'IS_PASSED_FILTER', 'FILTER_RANK',
                'MARKET_REGIME'
            ]
            unique_keys = ['SCORE_DATE', 'STOCK_CODE']
            
            for result in results:
                try:
                    # 매칭된 조건을 문자열로 변환 (최대 200자)
                    matched_condition = ','.join(result.matched_conditions[:5])[:200] if result.matched_conditions else ''
                    
                    values = (
                        score_date,
                        result.stock_code,
                        result.stock_name,
                        result.total_score,
                        result.momentum_score,
                        result.quality_score,
                        result.value_score,
                        result.technical_score,
                        result.news_stat_score,
                        result.supply_demand_score,
                        matched_condition,
                        result.condition_win_rate,
                        result.condition_sample_count,
                        1 if result.is_passed_filter else 0,
                        result.rank,
                        market_regime,
                    )
                    
                    execute_upsert(cursor, 'DAILY_QUANT_SCORE', columns, values, unique_keys)
                    saved_count += 1
                    
                except Exception as e:
                    logger.debug(f"   {result.stock_code} 저장 실패: {e}")
            
            self.db_connection.commit()
            cursor.close()
            
            logger.info(f"   (QuantScorer) 📊 DAILY_QUANT_SCORE 저장 완료: {saved_count}/{len(results)}개")
            
        except Exception as e:
            logger.error(f"   (QuantScorer) DAILY_QUANT_SCORE 저장 실패: {e}")
        
        return saved_count
    
    def update_hybrid_scores(self,
                             hybrid_results: List,  # HybridScoreResult
                             score_date: datetime = None) -> int:
        """
        [v1.0.2] DAILY_QUANT_SCORE에 하이브리드 점수 업데이트
        
        최종 하이브리드 스코어링 결과를 기존 레코드에 업데이트
        
        Args:
            hybrid_results: HybridScoreResult 리스트
            score_date: 점수 산출일
        
        Returns:
            업데이트된 레코드 수
        """
        if score_date is None:
            score_date = datetime.now(timezone.utc).date()
        
        updated_count = 0
        
        try:
            cursor = self.db_connection.cursor()
            
            for result in hybrid_results:
                try:
                    cursor.execute("""
                        UPDATE DAILY_QUANT_SCORE
                        SET LLM_SCORE = %s,
                            HYBRID_SCORE = %s,
                            IS_FINAL_SELECTED = %s
                        WHERE SCORE_DATE = %s AND STOCK_CODE = %s
                    """, (
                        result.llm_score,
                        result.hybrid_score,
                        1 if result.is_selected else 0,
                        score_date,
                        result.stock_code,
                    ))
                    updated_count += 1
                    
                except Exception as e:
                    logger.debug(f"   {result.stock_code} 업데이트 실패: {e}")
            
            self.db_connection.commit()
            cursor.close()
            
            logger.info(f"   (QuantScorer) 📊 하이브리드 점수 업데이트 완료: {updated_count}개")
            
        except Exception as e:
            logger.error(f"   (QuantScorer) 하이브리드 점수 업데이트 실패: {e}")
        
        return updated_count


# =============================================================================
# 유틸리티 함수
# =============================================================================

def format_quant_score_for_prompt(result: QuantScoreResult) -> str:
    """
    [v1.0] LLM 프롬프트용 정량 점수 요약 포맷팅 (Dual Track)
    
    3 AI 합의 기반:
    - 단기/장기 전략별 점수와 추천 분리 표시
    - 뉴스 시간축 신호 명시 (WAIT_DIP, BUY_NOW 등)
    - LLM이 "지금 사면 안 된다"는 것을 명확히 인지하도록
    """
    # [v1.0.1] 데이터 부족 경고
    if not result.is_valid:
        return f"""
[⚠️ 정량 분석 불가 - 데이터 부족]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
종목: {result.stock_name} ({result.stock_code})
상태: 데이터 부족으로 정량 분석 불가
사유: {result.invalid_reason}

⚠️ 이 종목은 정량 분석이 불가능하여 정성적 판단에만 의존해야 합니다.
   뉴스와 펀더멘털을 신중하게 평가하고, 보수적으로 판단하세요.
""".strip()
    
    # [v1.0] Dual Track 전략별 표시
    dual_track_info = f"""
╔══════════════════════════════════════════════════════════╗
║  🎯 Dual Track 전략 분석 (v1.0)                          ║
╠══════════════════════════════════════════════════════════╣
║  [단기 스나이퍼 D+5]          [장기 헌터 D+60]            ║
║  점수: {result.short_term_score:5.1f}점 ({result.short_term_grade})            점수: {result.long_term_score:5.1f}점 ({result.long_term_grade})             ║
║  추천: {result.short_term_recommendation:<10}          추천: {result.long_term_recommendation:<10}           ║
╠══════════════════════════════════════════════════════════╣
║  📊 권장 보유기간: {result.recommended_holding_days}일                                ║
╚══════════════════════════════════════════════════════════╝
"""
    
    # 뉴스 시간축 신호 (핵심!)
    timing_alert = ""
    if result.news_timing_signal == "WAIT_DIP":
        timing_alert = f"""
🚨 [중요 경고] 뉴스 시간축 신호: 눌림목 대기 (WAIT_DIP)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{result.news_timing_reason}

⚠️ 절대 지금 추격 매수하지 마세요!
   데이터가 말합니다: "뉴스 뜨고 바로 사면 43% 확률로 물립니다."
   주가가 눌릴 때까지 기다렸다가 분할 매수하세요.
"""
    elif result.news_timing_signal == "BUY_NOW":
        timing_alert = f"""
✅ [신호] 뉴스 시간축: 즉시 매수 가능 (BUY_NOW)
{result.news_timing_reason}
"""
    elif result.news_timing_signal == "SELL_NEWS":
        timing_alert = f"""
🔴 [경고] 뉴스 시간축: 매도 검토 (SELL_NEWS)
{result.news_timing_reason}
"""
    
    # 복합조건 보너스 표시
    compound_info = ""
    if result.compound_bonus > 0:
        compound_info = f"\n🎯 복합조건 충족 (RSI+외인): +{result.compound_bonus}점 → 단기 스나이퍼 전략 유효!"
    
    summary = f"""
[정량 분석 결과 - Scout v1.0]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
종목: {result.stock_name} ({result.stock_code})
섹터: {result.sector}
기존 총점: {result.total_score}/100점{compound_info}
{dual_track_info}
{timing_alert}
[팩터별 점수 (참고용)]
• 모멘텀: {result.momentum_score}/25점 (⚠️ 한국시장 IC 음수)
• 품질(ROE): {result.quality_score}/20점 ✅
• 가치: {result.value_score}/15점
• 기술적(RSI): {result.technical_score}/10점 ✅
• 뉴스통계: {result.news_stat_score}/15점 (⚠️ 단기 역신호)
• 수급:   {result.supply_demand_score}/15점
"""
    
    # 조건부 승률 정보 추가
    if result.condition_win_rate is not None:
        win_rate_pct = result.condition_win_rate * 100
        confidence_emoji = "🔴" if result.condition_confidence == 'LOW' else (
            "🟡" if result.condition_confidence == 'MID' else "🟢"
        )
        
        summary += f"""
[역사적 패턴 분석]
• 과거 유사 조건 발생 시: {win_rate_pct:.1f}% 확률로 상승
• 표본 수: {result.condition_sample_count}회 {confidence_emoji}
• 매칭 조건: {', '.join(result.matched_conditions[:3]) if result.matched_conditions else '없음'}
"""
    
    summary += """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 LLM 판단 지침:
1. 단기 점수 높고 + 복합조건 충족 → 단기 스윙 OK
2. 장기 점수 높고 + WAIT_DIP 신호 → 눌림목 분할매수
3. 뉴스 호재인데 단기 점수 낮음 → 즉시 매수 금지!
"""
    
    return summary.strip()

