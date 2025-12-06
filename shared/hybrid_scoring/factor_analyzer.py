#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Scout v1.0 FactorAnalyzer - 오프라인 팩터 분석 배치 작업

역할: Scout의 '지능'을 업데이트하는 주기적 배치 작업 (주 1회)

주요 기능:
1. 팩터 예측력 분석: IC(Information Coefficient), IR(Information Ratio) 계산
2. 조건부 승률 분석: "외국인 순매수 + 뉴스점수 70↑" → 승률 80%
3. 뉴스 영향도 분석: 카테고리별 D+5 승률, 평균 수익률
4. [v1.0.2] 재무 데이터(PER/PBR/ROE) DB 연동
5. [v1.0.2] 뉴스+수급 복합 조건 분석
6. [v1.0.2] NEWS_FACTOR_STATS 테이블 채우기
7. [v1.0.2] Recency Weighting 가중치 반영

분석 결과는 DB에 저장되어 QuantScorer에서 활용됩니다.

[GPT 피드백 반영]
- 뉴스 카테고리별 승률 분석 추가
- 외국인/기관 수급 조건 분석 추가
- PER/PBR/ROE 재무 데이터 DB 연동
- Recency Weighting 실제 가중치 반영
"""

import logging
import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass

from .schema import (
    get_confidence_level,
    create_hybrid_scoring_tables,
    execute_upsert,
    is_oracle,
)
from shared.database import _is_mariadb

# [v1.1] SQLAlchemy Repository 지원 (테스트 용이성)
if TYPE_CHECKING:
    from shared.db.factor_repository import FactorRepository

logger = logging.getLogger(__name__)


@dataclass
class FactorAnalysisResult:
    """팩터 분석 결과"""
    factor_key: str
    factor_name: str
    ic_mean: float           # Information Coefficient 평균
    ic_std: float            # IC 표준편차
    ir: float                # Information Ratio
    hit_rate: float          # 적중률
    recommended_weight: float # 추천 가중치
    sample_count: int        # 표본 수


@dataclass
class ConditionPerformance:
    """조건부 성과 결과"""
    target_type: str         # STOCK, SECTOR, ALL
    target_code: str         # 종목코드/섹터코드/ALL
    condition_key: str       # 조건 키
    condition_desc: str      # 조건 설명
    win_rate: float          # 승률
    avg_return: float        # 평균 수익률
    sample_count: int        # 표본 수
    recent_win_rate: float   # 최근 3개월 승률
    recent_sample_count: int # 최근 3개월 표본


class FactorAnalyzer:
    """
    오프라인 팩터 분석기
    
    주기적으로 실행되어 팩터별 예측력을 측정하고 DB에 저장합니다.
    """
    
    # 분석 기간 설정
    DEFAULT_LOOKBACK_YEARS = 2     # 기본 분석 기간 (2년)
    RECENT_MONTHS = 3              # 최근성 분석 기간 (3개월)
    FORWARD_DAYS = [5, 10, 20]     # 미래 수익률 측정 기간
    
    # 팩터 정의
    FACTOR_DEFINITIONS = {
        'momentum_6m': {
            'name': '6개월 모멘텀',
            'calc_func': '_calc_momentum_6m',
        },
        'momentum_1m': {
            'name': '1개월 모멘텀',
            'calc_func': '_calc_momentum_1m',
        },
        'value_per': {
            'name': 'PER (저평가)',
            'calc_func': '_calc_per_factor',
        },
        'value_pbr': {
            'name': 'PBR (저평가)',
            'calc_func': '_calc_pbr_factor',
        },
        'quality_roe': {
            'name': 'ROE (수익성)',
            'calc_func': '_calc_roe_factor',
        },
        'technical_rsi_oversold': {
            'name': 'RSI 과매도',
            'calc_func': '_calc_rsi_oversold',
        },
        'supply_foreign_buy': {
            'name': '외국인 순매수',
            'calc_func': '_calc_foreign_buy',
        },
    }
    
    # 뉴스 카테고리 정의
    NEWS_CATEGORIES = [
        '실적',      # 어닝 서프라이즈/쇼크
        '수주',      # 대규모 계약
        '신사업',    # 신규 진출
        'M&A',      # 인수합병
        '배당',      # 배당 발표
        '규제',      # 규제 이슈
        '경영',      # 경영권 분쟁, CEO 이슈
    ]

    DISCLOSURE_TABLE = 'STOCK_DISCLOSURES'
    
    # [v1.0.5] 시장 국면 정의
    MARKET_REGIME_THRESHOLDS = {
        'BULL': 0.10,      # 6개월 수익률 +10% 이상
        'BEAR': -0.10,     # 6개월 수익률 -10% 이하
        'SIDEWAYS': 0.0,   # 그 사이
    }
    
    # [v1.0.5] 종목 그룹 분류 (시가총액 기준)
    STOCK_GROUP_THRESHOLDS = {
        'LARGE': 10_000_000_000_000,   # 10조 이상: 대형주
        'MID': 1_000_000_000_000,      # 1조 이상: 중형주
        'SMALL': 0,                     # 그 외: 소형주
    }
    
    def __init__(self, db_conn=None, *, repository: "FactorRepository" = None):
        """
        초기화
        
        [v1.1] 두 가지 모드 지원:
        1. 레거시 모드: db_conn 직접 전달 (하위 호환성)
        2. Repository 모드: FactorRepository 주입 (테스트 용이)
        
        Args:
            db_conn: DB 연결 객체 (레거시, deprecated)
            repository: FactorRepository 인스턴스 (권장)
        
        Usage:
            # 레거시 방식 (하위 호환성)
            analyzer = FactorAnalyzer(db_conn)
            
            # 권장 방식 (테스트 용이)
            from shared.db.factor_repository import FactorRepository
            repo = FactorRepository(session)
            analyzer = FactorAnalyzer(repository=repo)
        """
        self.db_conn = db_conn
        self._repository = repository
        
        # Repository 모드인 경우 db_conn 필요 없음
        if repository is not None:
            logger.info("   [FactorAnalyzer] Repository 모드로 초기화")
        elif db_conn is not None:
            # 레거시 모드: 테이블 생성 확인
            create_hybrid_scoring_tables(db_conn)
            logger.info("   [FactorAnalyzer] 레거시(db_conn) 모드로 초기화")
        else:
            logger.warning("⚠️ [FactorAnalyzer] DB 연결 없이 초기화됨 (분석 기능 제한)")
        
        # [v1.0.5] 시장 국면 캐시
        self._market_regime_cache = None
        self._stock_group_cache = {}
        
        logger.info("✅ FactorAnalyzer 초기화 완료")
    
    @property
    def repository(self) -> Optional["FactorRepository"]:
        """Repository 인스턴스 반환 (lazy initialization)"""
        if self._repository is not None:
            return self._repository
        
        # 레거시 모드에서 repository가 필요한 경우 자동 생성
        if self.db_conn is not None:
            try:
                from shared.db.factor_repository import FactorRepository
                from shared.db.connection import get_session
                
                session = get_session()
                self._repository = FactorRepository(session)
                logger.debug("   [FactorAnalyzer] Repository 자동 생성")
                return self._repository
            except Exception as e:
                logger.warning(f"⚠️ [FactorAnalyzer] Repository 자동 생성 실패: {e}")
        
        return None
    
    def detect_market_regime(self, lookback_days: int = 120) -> str:
        """
        [v1.0.5] 현재 시장 국면 감지
        
        KOSPI 200 지수의 최근 6개월 수익률로 판단:
        - BULL: +10% 이상
        - BEAR: -10% 이하
        - SIDEWAYS: 그 사이
        
        Returns:
            'BULL', 'BEAR', 'SIDEWAYS'
        """
        if self._market_regime_cache:
            return self._market_regime_cache
        
        try:
            cursor = self.db_conn.cursor()
            
            # KOSPI 200 지수 데이터 조회 (종목코드 '0001' 또는 별도 테이블)
            cursor.execute("""
                SELECT CLOSE_PRICE, PRICE_DATE
                FROM STOCK_DAILY_PRICES_3Y
                WHERE STOCK_CODE = '0001'
                ORDER BY PRICE_DATE DESC
                LIMIT %s
            """, (lookback_days,))
            
            rows = cursor.fetchall()
            cursor.close()
            
            if len(rows) < 30:
                logger.warning("   시장 국면 감지: KOSPI 데이터 부족, SIDEWAYS 기본값 사용")
                return 'SIDEWAYS'
            
            # 수익률 계산
            recent_price = rows[0][0] if not isinstance(rows[0], dict) else rows[0]['CLOSE_PRICE']
            old_price = rows[-1][0] if not isinstance(rows[-1], dict) else rows[-1]['CLOSE_PRICE']
            
            returns = (recent_price / old_price - 1)
            
            if returns >= self.MARKET_REGIME_THRESHOLDS['BULL']:
                regime = 'BULL'
            elif returns <= self.MARKET_REGIME_THRESHOLDS['BEAR']:
                regime = 'BEAR'
            else:
                regime = 'SIDEWAYS'
            
            self._market_regime_cache = regime
            logger.info(f"   📈 시장 국면 감지: {regime} (6개월 수익률: {returns:.1%})")
            
            return regime
            
        except Exception as e:
            logger.warning(f"   시장 국면 감지 실패: {e}, SIDEWAYS 기본값 사용")
            return 'SIDEWAYS'
    
    def classify_stock_group(self, stock_code: str) -> str:
        """
        [v1.0.5] 종목 그룹 분류 (시가총액 기준)
        [v1.1] Repository 모드 지원
        
        Returns:
            'LARGE', 'MID', 'SMALL'
        """
        if stock_code in self._stock_group_cache:
            return self._stock_group_cache[stock_code]
        
        try:
            # [v1.1] Repository 모드 우선
            if self.repository is not None:
                market_cap = self.repository.get_market_cap(stock_code)
            else:
                # 레거시 모드
                cursor = self.db_conn.cursor()
                cursor.execute("""
                    SELECT MARKET_CAP FROM STOCK_MASTER WHERE STOCK_CODE = %s
                """, (stock_code,))
                
                row = cursor.fetchone()
                cursor.close()
                
                if not row:
                    return 'SMALL'
                
                market_cap = row[0] if not isinstance(row, dict) else row.get('MARKET_CAP', 0)
            
            if market_cap is None:
                market_cap = 0
            
            if market_cap >= self.STOCK_GROUP_THRESHOLDS['LARGE']:
                group = 'LARGE'
            elif market_cap >= self.STOCK_GROUP_THRESHOLDS['MID']:
                group = 'MID'
            else:
                group = 'SMALL'
            
            self._stock_group_cache[stock_code] = group
            return group
            
        except Exception as e:
            logger.debug(f"   종목 그룹 분류 실패 ({stock_code}): {e}")
            return 'SMALL'
    
    # [v1.0.6 Phase B] 섹터 분류
    SECTOR_MAPPING = {
        # KOSPI200 섹터 코드 매핑
        '반도체': ['005930', '000660', '034730', '042700'],  # 삼성전자, SK하이닉스 등
        '바이오': ['068270', '207940', '091990', '145020'],  # 셀트리온, 삼바 등
        '금융': ['105560', '055550', '086790', '316140'],    # KB, 신한 등
        '자동차': ['005380', '000270', '012330', '011210'],  # 현대차, 기아 등
        '화학': ['051910', '011170', '009830', '010140'],    # LG화학 등
        '건설': ['000720', '006360', '047050', '034310'],    # 현대건설 등
        '철강': ['005490', '004020', '000880', '001450'],    # POSCO 등
        '유통': ['035420', '035720', '034220', '030200'],    # 네이버, 카카오 등
    }
    
    def get_stock_sector(self, stock_code: str) -> str:
        """
        [v1.0.6 Phase B] 종목의 섹터 분류
        [v1.1] Repository 모드 지원
        
        STOCK_MASTER의 SECTOR_KOSPI200 또는 INDUSTRY_CODE 기반
        """
        # 캐시 확인
        cache_key = f"sector_{stock_code}"
        if hasattr(self, '_sector_cache') and cache_key in self._sector_cache:
            return self._sector_cache[cache_key]
        
        if not hasattr(self, '_sector_cache'):
            self._sector_cache = {}
        
        # 하드코딩된 매핑 먼저 확인
        for sector, codes in self.SECTOR_MAPPING.items():
            if stock_code in codes:
                self._sector_cache[cache_key] = sector
                return sector
        
        # DB에서 조회
        try:
            # [v1.1] Repository 모드 우선
            if self.repository is not None:
                sector_kospi, industry_code = self.repository.get_stock_sector(stock_code)
                sector = sector_kospi or industry_code
            else:
                # 레거시 모드
                cursor = self.db_conn.cursor()
                cursor.execute("""
                    SELECT SECTOR_KOSPI200, INDUSTRY_CODE 
                    FROM STOCK_MASTER 
                    WHERE STOCK_CODE = %s
                """, (stock_code,))
                
                row = cursor.fetchone()
                cursor.close()
                
                if row:
                    sector = row[0] if not isinstance(row, dict) else row.get('SECTOR_KOSPI200')
                else:
                    sector = None
            
            if sector:
                self._sector_cache[cache_key] = sector
                return sector
            
            self._sector_cache[cache_key] = '기타'
            return '기타'
            
        except Exception as e:
            logger.debug(f"   섹터 분류 실패 ({stock_code}): {e}")
            return '기타'
    
    def group_stocks_by_sector(self, stock_codes: List[str]) -> Dict[str, List[str]]:
        """
        [v1.0.6 Phase B] 종목들을 섹터별로 그룹화
        """
        sector_groups = {}
        
        for code in stock_codes:
            sector = self.get_stock_sector(code)
            if sector not in sector_groups:
                sector_groups[sector] = []
            sector_groups[sector].append(code)
        
        return sector_groups
    
    def analyze_by_sector(self, 
                          stock_codes: List[str],
                          factor_key: str = 'technical_rsi_oversold',
                          forward_days: int = 5) -> Dict[str, Dict]:
        """
        [v1.0.6 Phase B] 섹터별 팩터 분석
        
        Returns:
            {sector: {ic_mean, hit_rate, sample_count, ...}}
        """
        logger.info(f"   📊 섹터별 {factor_key} 분석 (D+{forward_days})")
        
        sector_groups = self.group_stocks_by_sector(stock_codes)
        results = {}
        
        for sector, codes in sector_groups.items():
            if len(codes) < 5:  # 최소 5개 종목 이상
                continue
            
            try:
                result = self.analyze_factor(codes, factor_key, forward_days)
                if result and result.sample_count >= 30:
                    results[sector] = {
                        'ic_mean': result.ic_mean,
                        'hit_rate': result.hit_rate,
                        'sample_count': result.sample_count,
                        'stock_count': len(codes),
                    }
                    logger.info(f"      {sector} ({len(codes)}개): "
                               f"IC={result.ic_mean:.4f}, 적중률={result.hit_rate:.1%}")
            except Exception as e:
                logger.debug(f"      {sector} 분석 실패: {e}")
        
        return results
    
    def _is_mariadb(self) -> bool:
        """현재 DB 타입이 MariaDB인지 확인"""
        import os
        return os.getenv("DB_TYPE", "ORACLE").upper() == "MARIADB"
    
    def _get_historical_prices(self, 
                               stock_codes: List[str],
                               days: int = 504) -> Dict[str, pd.DataFrame]:
        """
        종목별 과거 가격 데이터 조회
        
        [v1.1] Repository 모드 지원 (SQLAlchemy ORM)
        
        Args:
            stock_codes: 종목 코드 리스트
            days: 조회 일수 (기본 504일 ≈ 2년)
        
        Returns:
            {stock_code: DataFrame} 딕셔너리
        """
        # [v1.1] Repository 모드 우선
        if self.repository is not None:
            return self.repository.get_historical_prices_bulk(stock_codes, days)
        
        # 레거시 모드 (raw SQL)
        result = {}
        
        try:
            cursor = self.db_conn.cursor()
            
            for code in stock_codes:
                cursor.execute("""
                    SELECT PRICE_DATE, CLOSE_PRICE, VOLUME, HIGH_PRICE, LOW_PRICE
                    FROM STOCK_DAILY_PRICES_3Y
                    WHERE STOCK_CODE = %s
                    ORDER BY PRICE_DATE DESC
                    LIMIT %s
                """, (code, days))
                
                rows = cursor.fetchall()
                
                if rows:
                    if isinstance(rows[0], dict):
                        df = pd.DataFrame(rows)
                    else:
                        df = pd.DataFrame(rows, columns=[
                            'PRICE_DATE', 'CLOSE_PRICE', 'VOLUME', 'HIGH_PRICE', 'LOW_PRICE'
                        ])
                    
                    df = df.sort_values('PRICE_DATE').reset_index(drop=True)
                    result[code] = df
            
            cursor.close()
            
        except Exception as e:
            logger.error(f"   (FactorAnalyzer) 가격 데이터 조회 실패: {e}")
        
        return result
    
    def _calculate_forward_returns(self, 
                                   df: pd.DataFrame,
                                   forward_days: int = 5) -> pd.Series:
        """
        미래 수익률 계산
        
        Args:
            df: 가격 데이터프레임 (CLOSE_PRICE 컬럼 필요)
            forward_days: 미래 일수
        
        Returns:
            미래 수익률 Series
        """
        return df['CLOSE_PRICE'].pct_change(forward_days).shift(-forward_days) * 100
    
    def _calc_momentum_6m(self, df: pd.DataFrame) -> pd.Series:
        """6개월 모멘텀 계산"""
        return df['CLOSE_PRICE'].pct_change(120) * 100
    
    def _calc_momentum_1m(self, df: pd.DataFrame) -> pd.Series:
        """1개월 모멘텀 계산"""
        return df['CLOSE_PRICE'].pct_change(20) * 100
    
    def _calc_per_factor(self, df: pd.DataFrame, per_values: pd.Series = None) -> pd.Series:
        """PER 팩터 (낮을수록 좋음 → 음수로 변환)"""
        if per_values is not None:
            return -per_values  # 낮을수록 좋으므로 음수
        return pd.Series([0] * len(df))
    
    def _calc_pbr_factor(self, df: pd.DataFrame, pbr_values: pd.Series = None) -> pd.Series:
        """PBR 팩터 (낮을수록 좋음 → 음수로 변환)"""
        if pbr_values is not None:
            return -pbr_values
        return pd.Series([0] * len(df))
    
    def _calc_roe_factor(self, df: pd.DataFrame, roe_values: pd.Series = None) -> pd.Series:
        """ROE 팩터 (높을수록 좋음)"""
        if roe_values is not None:
            return roe_values
        return pd.Series([0] * len(df))
    
    def _calc_rsi_oversold(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        """RSI 과매도 팩터 (30 이하면 높은 값)"""
        close = df['CLOSE_PRICE']
        delta = close.diff()
        gain = delta.where(delta > 0, 0).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        
        # RSI가 낮을수록 좋으므로 (100 - RSI) 반환
        return 100 - rsi
    
    def _calc_foreign_buy(self, df: pd.DataFrame, foreign_data: pd.Series = None) -> pd.Series:
        """외국인 순매수 팩터"""
        if foreign_data is not None:
            return foreign_data
        return pd.Series([0] * len(df))
    
    def calculate_ic(self, 
                     factor_values: pd.Series,
                     forward_returns: pd.Series) -> Tuple[float, float, float]:
        """
        Information Coefficient 계산
        
        IC = 팩터 값과 미래 수익률의 상관계수
        
        Args:
            factor_values: 팩터 값 Series
            forward_returns: 미래 수익률 Series
        
        Returns:
            (IC 평균, IC 표준편차, Information Ratio)
        """
        # 결측치 제거
        valid = pd.DataFrame({
            'factor': factor_values,
            'return': forward_returns
        }).dropna()
        
        if len(valid) < 30:
            return 0.0, 1.0, 0.0
        
        # [v1.0.4] 팩터 값이 상수인지 확인 (상수면 상관계수 계산 불가)
        if valid['factor'].nunique() <= 1:
            logger.debug("   (FactorAnalyzer) 팩터 값이 상수입니다. IC=0 반환")
            return 0.0, 1.0, 0.0
        
        # 순위 상관계수 (Spearman) 사용
        ic = valid['factor'].corr(valid['return'], method='spearman')
        
        # [v1.0.4] NaN 처리
        if pd.isna(ic):
            logger.debug("   (FactorAnalyzer) IC가 NaN입니다. IC=0 반환")
            return 0.0, 1.0, 0.0
        
        # 롤링 IC 계산 (60일 윈도우)
        rolling_ic = valid['factor'].rolling(60).corr(valid['return'])
        ic_std = rolling_ic.std()
        
        if pd.isna(ic_std) or ic_std <= 0:
            ir = 0.0
            ic_std = 1.0
        else:
            ir = ic / ic_std
        
        return float(ic), float(ic_std), float(ir)
    
    def analyze_factor(self,
                       stock_codes: List[str],
                       factor_key: str,
                       forward_days: int = 5) -> FactorAnalysisResult:
        """
        단일 팩터의 예측력 분석
        
        Args:
            stock_codes: 분석 대상 종목 코드 리스트
            factor_key: 팩터 키 (예: 'momentum_6m')
            forward_days: 미래 수익률 측정 기간
        
        Returns:
            FactorAnalysisResult 객체
        """
        factor_def = self.FACTOR_DEFINITIONS.get(factor_key)
        if not factor_def:
            raise ValueError(f"Unknown factor: {factor_key}")
        
        logger.info(f"   (FactorAnalyzer) {factor_def['name']} 분석 중...")
        
        # 가격 데이터 조회
        price_data = self._get_historical_prices(stock_codes)
        
        all_factors = []
        all_returns = []
        
        for code, df in price_data.items():
            if len(df) < 150:  # 최소 데이터 요구
                continue
            
            # 팩터 계산
            calc_func = getattr(self, factor_def['calc_func'])
            factor_values = calc_func(df)
            
            # 미래 수익률 계산
            forward_returns = self._calculate_forward_returns(df, forward_days)
            
            # 두 시리즈를 합쳐서 동시에 유효한 인덱스만 추출
            combined = pd.DataFrame({
                'factor': factor_values,
                'return': forward_returns
            }).dropna()
            
            all_factors.extend(combined['factor'].tolist())
            all_returns.extend(combined['return'].tolist())
        
        if len(all_factors) < 100:
            logger.warning(f"   (FactorAnalyzer) {factor_key} 표본 부족 ({len(all_factors)}개)")
            return FactorAnalysisResult(
                factor_key=factor_key,
                factor_name=factor_def['name'],
                ic_mean=0.0,
                ic_std=1.0,
                ir=0.0,
                hit_rate=0.5,
                recommended_weight=0.05,
                sample_count=len(all_factors),
            )
        
        # IC 계산
        factor_series = pd.Series(all_factors)
        return_series = pd.Series(all_returns)
        ic_mean, ic_std, ir = self.calculate_ic(factor_series, return_series)
        
        # 적중률 계산 (팩터 상위 20%의 평균 수익률이 양수인 비율)
        combined = pd.DataFrame({'factor': all_factors, 'return': all_returns}).dropna()
        top_quantile = combined[combined['factor'] >= combined['factor'].quantile(0.8)]
        hit_rate = (top_quantile['return'] > 0).mean() if len(top_quantile) > 0 else 0.5
        
        # 추천 가중치 계산 (IR 기반)
        # IR이 0.5 이상이면 높은 가중치, 0 이하면 낮은 가중치
        if ir >= 0.5:
            recommended_weight = min(0.20, 0.10 + ir * 0.1)
        elif ir >= 0:
            recommended_weight = 0.10
        else:
            recommended_weight = max(0.02, 0.05 + ir * 0.05)
        
        result = FactorAnalysisResult(
            factor_key=factor_key,
            factor_name=factor_def['name'],
            ic_mean=round(ic_mean, 4),
            ic_std=round(ic_std, 4),
            ir=round(ir, 4),
            hit_rate=round(hit_rate, 4),
            recommended_weight=round(recommended_weight, 4),
            sample_count=len(all_factors),
        )
        
        logger.info(f"   ✅ {factor_def['name']}: IC={ic_mean:.4f}, IR={ir:.4f}, "
                   f"적중률={hit_rate:.1%}, 표본={len(all_factors)}")
        
        return result
    
    def analyze_condition_performance(self,
                                      stock_code: str,
                                      condition_key: str,
                                      condition_func,
                                      forward_days: int = 5) -> Optional[ConditionPerformance]:
        """
        조건부 성과 분석
        
        "특정 조건이 발생했을 때의 미래 수익률" 분석
        
        Args:
            stock_code: 종목 코드 (또는 'ALL')
            condition_key: 조건 키 (예: 'news_score_70')
            condition_func: 조건 판단 함수 (df -> bool Series)
            forward_days: 미래 수익률 측정 기간
        
        Returns:
            ConditionPerformance 객체 또는 None
        """
        price_data = self._get_historical_prices([stock_code])
        
        if stock_code not in price_data:
            return None
        
        df = price_data[stock_code]
        
        # 조건 발생 시점 판별
        condition_mask = condition_func(df)
        
        # 미래 수익률 계산
        forward_returns = self._calculate_forward_returns(df, forward_days)
        
        # 조건 발생 시점의 수익률만 추출
        condition_returns = forward_returns[condition_mask].dropna()
        
        if len(condition_returns) < 5:
            return None
        
        # 통계 계산
        win_rate = (condition_returns > 0).mean()
        avg_return = condition_returns.mean()
        
        # 최근 3개월 통계
        recent_cutoff = datetime.now() - timedelta(days=90)
        if 'PRICE_DATE' in df.columns:
            recent_mask = df['PRICE_DATE'] >= recent_cutoff
            recent_returns = forward_returns[condition_mask & recent_mask].dropna()
            recent_win_rate = (recent_returns > 0).mean() if len(recent_returns) > 0 else win_rate
            recent_sample_count = len(recent_returns)
        else:
            recent_win_rate = win_rate
            recent_sample_count = 0
        
        return ConditionPerformance(
            target_type='STOCK',
            target_code=stock_code,
            condition_key=condition_key,
            condition_desc=condition_key,
            win_rate=round(win_rate, 4),
            avg_return=round(avg_return, 4),
            sample_count=len(condition_returns),
            recent_win_rate=round(recent_win_rate, 4),
            recent_sample_count=recent_sample_count,
        )
    
    def _sanitize_for_db(self, value):
        """
        [v1.0.3] DB 저장을 위해 NaN/inf 값을 None으로 변환
        """
        import math
        if value is None:
            return None
        if isinstance(value, float):
            if math.isnan(value) or math.isinf(value):
                return None
        return value
    
    def save_factor_metadata(self, result: FactorAnalysisResult, market_regime: str = 'ALL') -> bool:
        """
        팩터 분석 결과를 FACTOR_METADATA에 저장
        
        [v1.0.3] Claude Opus 4.5 피드백: Oracle MERGE INTO 호환성 추가
        [v1.0.4] NaN/inf 값을 None으로 변환하여 MySQL 호환성 확보
        """
        try:
            cursor = self.db_conn.cursor()
            
            columns = [
                'FACTOR_KEY', 'FACTOR_NAME', 'MARKET_REGIME',
                'IC_MEAN', 'IC_STD', 'IR', 'HIT_RATE',
                'RECOMMENDED_WEIGHT', 'SAMPLE_COUNT',
                'ANALYSIS_START_DATE', 'ANALYSIS_END_DATE'
            ]
            values = (
                result.factor_key,
                result.factor_name,
                market_regime,
                self._sanitize_for_db(result.ic_mean),
                self._sanitize_for_db(result.ic_std),
                self._sanitize_for_db(result.ir),
                self._sanitize_for_db(result.hit_rate),
                self._sanitize_for_db(result.recommended_weight),
                result.sample_count,
                datetime.now() - timedelta(days=self.DEFAULT_LOOKBACK_YEARS * 365),
                datetime.now(),
            )
            unique_keys = ['FACTOR_KEY', 'MARKET_REGIME']
            
            execute_upsert(cursor, 'FACTOR_METADATA', columns, values, unique_keys)
            
            self.db_conn.commit()
            cursor.close()
            return True
            
        except Exception as e:
            logger.error(f"   (FactorAnalyzer) FACTOR_METADATA 저장 실패: {e}")
            return False
    
    def save_factor_performance(self, result: ConditionPerformance, holding_days: int = 5) -> bool:
        """
        조건부 성과 결과를 FACTOR_PERFORMANCE에 저장
        
        [v1.0.3] Claude Opus 4.5 피드백: Oracle MERGE INTO 호환성 추가
        """
        try:
            cursor = self.db_conn.cursor()
            
            confidence_level = get_confidence_level(result.sample_count)
            
            columns = [
                'TARGET_TYPE', 'TARGET_CODE', 'TARGET_NAME',
                'CONDITION_KEY', 'CONDITION_DESC',
                'WIN_RATE', 'AVG_RETURN', 'HOLDING_DAYS',
                'SAMPLE_COUNT', 'CONFIDENCE_LEVEL',
                'RECENT_WIN_RATE', 'RECENT_SAMPLE_COUNT',
                'ANALYSIS_DATE'
            ]
            values = (
                result.target_type,
                result.target_code,
                '',  # TARGET_NAME - 별도 조회 필요시 추가
                result.condition_key,
                result.condition_desc,
                result.win_rate,
                result.avg_return,
                holding_days,
                result.sample_count,
                confidence_level,
                result.recent_win_rate,
                result.recent_sample_count,
                datetime.now().date(),
            )
            unique_keys = ['TARGET_TYPE', 'TARGET_CODE', 'CONDITION_KEY', 'HOLDING_DAYS']
            
            execute_upsert(cursor, 'FACTOR_PERFORMANCE', columns, values, unique_keys)
            
            self.db_conn.commit()
            cursor.close()
            return True
            
        except Exception as e:
            logger.error(f"   (FactorAnalyzer) FACTOR_PERFORMANCE 저장 실패: {e}")
            return False
    
    # =========================================================================
    # [v1.0.2] GPT 피드백 반영 - 신규 기능
    # =========================================================================
    
    def _get_financial_data(self, stock_codes: List[str]) -> Dict[str, Dict]:
        """
        [v1.0.3] 종목별 분기별 재무 데이터(PER/PBR/ROE) DB에서 조회
        
        GPT 피드백: "PER/PBR/ROE 데이터 입력 없음" 해결
        [v1.0.3] FINANCIAL_METRICS_QUARTERLY 테이블에서 분기별 데이터 조회
        
        Returns:
            {stock_code: {quarter_date_str: {'per': float, 'pbr': float, 'roe': float}}}
        """
        result = {}
        
        try:
            cursor = self.db_conn.cursor()
            
            # FINANCIAL_METRICS_QUARTERLY 테이블에서 분기별 재무 데이터 조회
            if self._is_mariadb():
                placeholders = ','.join(['%s'] * len(stock_codes))
                cursor.execute(f"""
                    SELECT STOCK_CODE, QUARTER_DATE, PER, PBR, ROE
                    FROM FINANCIAL_METRICS_QUARTERLY
                    WHERE STOCK_CODE IN ({placeholders})
                    ORDER BY STOCK_CODE, QUARTER_DATE
                """, stock_codes)
            else:
                placeholders = ','.join([f':p{i}' for i in range(len(stock_codes))])
                params = {f'p{i}': code for i, code in enumerate(stock_codes)}
                cursor.execute(f"""
                    SELECT STOCK_CODE, QUARTER_DATE, PER, PBR, ROE
                    FROM FINANCIAL_METRICS_QUARTERLY
                    WHERE STOCK_CODE IN ({placeholders})
                    ORDER BY STOCK_CODE, QUARTER_DATE
                """, params)
            
            rows = cursor.fetchall()
            cursor.close()
            
            for row in rows:
                if isinstance(row, dict):
                    code = row.get('STOCK_CODE')
                    q_date = row.get('QUARTER_DATE')
                    per = row.get('PER')
                    pbr = row.get('PBR')
                    roe = row.get('ROE')
                else:
                    code = row[0]
                    q_date = row[1]
                    per = row[2]
                    pbr = row[3]
                    roe = row[4] if len(row) > 4 else None
                
                # 날짜를 문자열로 변환
                if hasattr(q_date, 'strftime'):
                    q_date_str = q_date.strftime('%Y-%m-%d')
                else:
                    q_date_str = str(q_date)
                
                if code not in result:
                    result[code] = {}
                
                result[code][q_date_str] = {
                    'per': float(per) if per else None,
                    'pbr': float(pbr) if pbr else None,
                    'roe': float(roe) if roe else None,
                }
            
            total_quarters = sum(len(v) for v in result.values())
            logger.debug(f"   (FactorAnalyzer) 분기별 재무 데이터 로드: {len(result)}개 종목, {total_quarters}개 분기")
            
        except Exception as e:
            logger.warning(f"   (FactorAnalyzer) 재무 데이터 조회 실패: {e}")
        
        return result
    
    def _get_financial_at_date(self, financial_data: Dict, stock_code: str, target_date) -> Dict:
        """
        [v1.0.3] 특정 날짜에 해당하는 분기의 재무 데이터 반환
        
        Args:
            financial_data: _get_financial_data()의 반환값
            stock_code: 종목 코드
            target_date: 대상 날짜 (datetime 또는 str)
        
        Returns:
            {'per': float, 'pbr': float, 'roe': float} 또는 빈 딕셔너리
        """
        if stock_code not in financial_data:
            return {}
        
        quarters = financial_data[stock_code]
        if not quarters:
            return {}
        
        # 날짜를 문자열로 변환
        if hasattr(target_date, 'strftime'):
            target_str = target_date.strftime('%Y-%m-%d')
        else:
            target_str = str(target_date)[:10]
        
        # 해당 날짜 이전의 가장 최근 분기 찾기
        sorted_quarters = sorted(quarters.keys())
        applicable_quarter = None
        
        for q_date in sorted_quarters:
            if q_date <= target_str:
                applicable_quarter = q_date
            else:
                break
        
        if applicable_quarter:
            return quarters[applicable_quarter]
        
        # 해당 날짜 이전 분기가 없으면 가장 오래된 분기 반환
        if sorted_quarters:
            return quarters[sorted_quarters[0]]
        
        return {}
    
    def _get_supply_demand_data(self, stock_codes: List[str], days: int = 504) -> Dict[str, pd.DataFrame]:
        """
        [v1.0.2] 종목별 외국인/기관 수급 데이터 조회
        
        GPT 피드백: "외국인/기관 수급 조건 분석" 추가
        
        Returns:
            {stock_code: DataFrame with TRADE_DATE, FOREIGN_NET, INST_NET}
        """
        result = {}
        
        try:
            cursor = self.db_conn.cursor()
            
            for code in stock_codes:
                cursor.execute("""
                    SELECT TRADE_DATE, FOREIGN_NET_BUY, INSTITUTION_NET_BUY
                    FROM STOCK_INVESTOR_TRADING
                    WHERE STOCK_CODE = %s
                    ORDER BY TRADE_DATE DESC
                    LIMIT %s
                """, (code, days))
                
                rows = cursor.fetchall()
                
                if rows:
                    if isinstance(rows[0], dict):
                        df = pd.DataFrame(rows)
                        # 컬럼명 정규화
                        if 'FOREIGN_NET_BUY' in df.columns:
                            df = df.rename(columns={
                                'FOREIGN_NET_BUY': 'FOREIGN_NET',
                                'INSTITUTION_NET_BUY': 'INST_NET'
                            })
                    else:
                        df = pd.DataFrame(rows, columns=[
                            'TRADE_DATE', 'FOREIGN_NET', 'INST_NET'
                        ])
                    df = df.sort_values('TRADE_DATE').reset_index(drop=True)
                    result[code] = df
            
            cursor.close()
            logger.debug(f"   (FactorAnalyzer) 수급 데이터 로드: {len(result)}개 종목")
            
        except Exception as e:
            logger.warning(f"   (FactorAnalyzer) 수급 데이터 조회 실패: {e}")
        
        return result
    
    def _get_news_sentiment_history(self, stock_codes: List[str], days: int = 504) -> Dict[str, pd.DataFrame]:
        """
        [v1.0.2] 종목별 뉴스 감성 점수 히스토리 조회
        
        GPT 피드백: "뉴스 카테고리별 승률" 분석을 위한 데이터 로드
        
        Returns:
            {stock_code: DataFrame with NEWS_DATE, SENTIMENT_SCORE, CATEGORY}
        """
        result = {}
        
        try:
            cursor = self.db_conn.cursor()
            
            for code in stock_codes:
                cursor.execute("""
                    SELECT NEWS_DATE, SENTIMENT_SCORE, CATEGORY
                    FROM STOCK_NEWS_SENTIMENT
                    WHERE STOCK_CODE = %s
                    ORDER BY NEWS_DATE DESC
                    LIMIT %s
                """, (code, days))
                
                rows = cursor.fetchall()
                
                if rows:
                    if isinstance(rows[0], dict):
                        df = pd.DataFrame(rows)
                    else:
                        df = pd.DataFrame(rows, columns=[
                            'NEWS_DATE', 'SENTIMENT_SCORE', 'CATEGORY'
                        ])
                    df = df.sort_values('NEWS_DATE').reset_index(drop=True)
                    result[code] = df
            
            cursor.close()
            logger.debug(f"   (FactorAnalyzer) 뉴스 감성 데이터 로드: {len(result)}개 종목")
            
        except Exception as e:
            logger.warning(f"   (FactorAnalyzer) 뉴스 감성 데이터 조회 실패 (테이블 없을 수 있음): {e}")
        
        return result
    
    def analyze_news_category_impact(self, 
                                     stock_codes: List[str],
                                     forward_days: int = 5) -> List[Dict]:
        """
        [v1.0.2] 뉴스 카테고리별 D+N 승률 및 평균 수익률 분석
        
        GPT 피드백: "뉴스 영향도 분석 미구현" 해결
        NEWS_FACTOR_STATS 테이블에 저장할 데이터 생성
        
        Returns:
            [{'category': str, 'win_rate': float, 'avg_return': float, ...}]
        """
        logger.info(f"   [v1.0.2] 뉴스 카테고리별 영향도 분석 시작 ({len(stock_codes)}개 종목)")
        
        price_data = self._get_historical_prices(stock_codes)
        news_data = self._get_news_sentiment_history(stock_codes)
        
        results = []
        category_stats = {cat: {'returns': [], 'recent_returns': []} for cat in self.NEWS_CATEGORIES}
        category_stats['ALL'] = {'returns': [], 'recent_returns': []}  # 전체 통계
        
        recent_cutoff = datetime.now() - timedelta(days=90)
        
        for code in stock_codes:
            if code not in price_data or code not in news_data:
                continue
            
            prices_df = price_data[code]
            news_df = news_data[code]
            
            if len(prices_df) < 30 or len(news_df) == 0:
                continue
            
            # 미래 수익률 계산
            forward_returns = self._calculate_forward_returns(prices_df, forward_days)
            
            # [v1.0.4] PRICE_DATE를 date로 변환 (datetime → date)
            if 'PRICE_DATE' in prices_df.columns:
                prices_df = prices_df.copy()
                prices_df['PRICE_DATE_ONLY'] = pd.to_datetime(prices_df['PRICE_DATE']).dt.date
            
            # 뉴스 날짜와 가격 데이터 매칭
            for _, news_row in news_df.iterrows():
                news_date_raw = news_row.get('NEWS_DATE')
                category = news_row.get('CATEGORY', 'ALL')
                sentiment = news_row.get('SENTIMENT_SCORE', 50)
                
                if category not in category_stats:
                    category = 'ALL'
                
                # [v1.0.4] datetime을 date로 변환하여 비교
                if hasattr(news_date_raw, 'date'):
                    news_date = news_date_raw.date()
                elif hasattr(news_date_raw, 'strftime'):
                    news_date = news_date_raw
                else:
                    continue
                
                # 해당 날짜의 수익률 찾기 (PRICE_DATE_ONLY 사용)
                if 'PRICE_DATE_ONLY' in prices_df.columns:
                    price_idx = prices_df[prices_df['PRICE_DATE_ONLY'] == news_date].index
                else:
                    price_idx = prices_df[prices_df['PRICE_DATE'] == news_date].index
                    if len(price_idx) == 0:
                        price_idx = prices_df[prices_df['PRICE_DATE'] == news_date_raw].index
                
                if len(price_idx) == 0:
                    continue
                
                idx = price_idx[0]
                if idx >= len(forward_returns):
                    continue
                
                ret = forward_returns.iloc[idx]
                if pd.isna(ret):
                    continue
                
                # 호재 뉴스만 분석 (감성 점수 70 이상)
                if sentiment >= 70:
                    category_stats[category]['returns'].append(ret)
                    category_stats['ALL']['returns'].append(ret)
                    
                    # [v1.0.4] recent_cutoff 비교도 date로 통일
                    if hasattr(news_date_raw, 'date'):
                        compare_date = news_date_raw
                    else:
                        compare_date = datetime.combine(news_date, datetime.min.time())
                    
                    if compare_date >= recent_cutoff:
                        category_stats[category]['recent_returns'].append(ret)
                        category_stats['ALL']['recent_returns'].append(ret)
        
        # 카테고리별 통계 계산 및 저장
        for category, stats in category_stats.items():
            returns = stats['returns']
            recent_returns = stats['recent_returns']
            
            if len(returns) < 5:
                continue
            
            returns_arr = np.array(returns)
            win_rate = (returns_arr > 0).mean()
            avg_return = returns_arr.mean()
            
            recent_win_rate = (np.array(recent_returns) > 0).mean() if len(recent_returns) > 0 else win_rate
            
            # [v1.0.2] Recency Weighting 적용
            recency_weight = self._calculate_recency_weight(len(returns), len(recent_returns), recent_win_rate, win_rate)
            
            result = {
                'category': category,
                'win_rate': round(win_rate, 4),
                'avg_return': round(avg_return, 4),
                'sample_count': len(returns),
                'confidence': get_confidence_level(len(returns)),
                'recent_win_rate': round(recent_win_rate, 4),
                'recent_sample_count': len(recent_returns),
                'recency_weight': round(recency_weight, 4),
                'holding_days': forward_days,
            }
            results.append(result)
            
            # NEWS_FACTOR_STATS에 저장
            self._save_news_factor_stats(result)
            
            logger.info(f"   📰 {category}: 승률={win_rate:.1%}, 평균수익률={avg_return:.2f}%, "
                       f"표본={len(returns)}, 최근승률={recent_win_rate:.1%}")
        
        return results

    def analyze_disclosure_impact(self,
                                  stock_codes: List[str],
                                  forward_days: int = 5,
                                  lookback_days: int = 365) -> List[Dict]:
        """
        [v1.0.3] DART 공시 기반 영향도 분석

        - STOCK_DISCLOSURES 테이블을 조회하여 카테고리별 승률 계산
        - NEWS_FACTOR_STATS에 '공시:<카테고리>' 형태로 저장
        """
        logger.info(f"   [v1.0.3] 공시 영향도 분석 시작 ({len(stock_codes)}개 종목, lookback={lookback_days}일)")

        disclosures = self._fetch_disclosures(stock_codes, lookback_days)
        price_data = self._get_historical_prices(stock_codes, days=max(lookback_days + 60, 250))

        category_stats = {}
        recent_cutoff = datetime.now() - timedelta(days=90)

        for code in stock_codes:
            if code not in disclosures or code not in price_data:
                continue

            df = price_data[code]
            if df.empty:
                continue

            forward_returns = self._calculate_forward_returns(df, forward_days)

            for disclosure in disclosures[code]:
                disc_date = disclosure['date']
                category = disclosure['category']
                if not category:
                    category = '기타'

                idx = df[df['PRICE_DATE'] >= disc_date].index
                if len(idx) == 0:
                    continue
                price_idx = idx[0]
                if price_idx >= len(forward_returns):
                    continue

                ret = forward_returns.iloc[price_idx]
                if pd.isna(ret):
                    continue

                key = f"공시:{category}"
                if key not in category_stats:
                    category_stats[key] = {'returns': [], 'recent_returns': []}

                category_stats[key]['returns'].append(ret)
                if disc_date >= recent_cutoff:
                    category_stats[key]['recent_returns'].append(ret)

        results = []
        for category, stats in category_stats.items():
            returns = stats['returns']
            recent_returns = stats['recent_returns']
            if len(returns) < 5:
                continue

            arr = pd.Series(returns)
            win_rate = (arr > 0).mean()
            avg_return = arr.mean()
            recent_win_rate = (pd.Series(recent_returns) > 0).mean() if recent_returns else win_rate
            recency_weight = self._calculate_recency_weight(len(returns), len(recent_returns), recent_win_rate, win_rate)

            payload = {
                'category': category,
                'win_rate': round(win_rate, 4),
                'avg_return': round(avg_return, 4),
                'sample_count': len(returns),
                'confidence': get_confidence_level(len(returns)),
                'recent_win_rate': round(recent_win_rate, 4),
                'recent_sample_count': len(recent_returns),
                'recency_weight': round(recency_weight, 4),
                'holding_days': forward_days,
            }
            results.append(payload)
            self._save_news_factor_stats(payload)
            logger.info(f"   📑 {category}: 승률={win_rate:.1%}, 표본={len(returns)}, 평균수익률={avg_return:.2f}%")

        return results

    def _fetch_disclosures(self, stock_codes: List[str], lookback_days: int = 365) -> Dict[str, List[Dict]]:
        """
        [v1.0.3] STOCK_DISCLOSURES 테이블에서 공시 데이터 로드
        """
        logger.info(f"   (FactorAnalyzer) 공시 데이터 로드 ({lookback_days}일)")
        cursor = self.db_conn.cursor()
        result = {code: [] for code in stock_codes}
        start_date = datetime.now() - timedelta(days=lookback_days)

        for code in stock_codes:
            try:
                if _is_mariadb():
                    cursor.execute(f"""
                        SELECT DISCLOSURE_DATE, CATEGORY
                        FROM {self.DISCLOSURE_TABLE}
                        WHERE STOCK_CODE = %s AND DISCLOSURE_DATE >= %s
                        ORDER BY DISCLOSURE_DATE ASC
                    """, (code, start_date))
                else:
                    cursor.execute(f"""
                        SELECT DISCLOSURE_DATE, CATEGORY
                        FROM {self.DISCLOSURE_TABLE}
                        WHERE STOCK_CODE = :1 AND DISCLOSURE_DATE >= :2
                        ORDER BY DISCLOSURE_DATE ASC
                    """, [code, start_date])
                rows = cursor.fetchall()
                entries = []
                for row in rows:
                    disc_date = row[0]
                    if isinstance(disc_date, datetime):
                        pass
                    else:
                        disc_date = datetime.strptime(str(disc_date), "%Y-%m-%d %H:%M:%S")
                    entries.append({
                        'date': disc_date,
                        'category': row[1] or '기타'
                    })
                result[code] = entries
            except Exception as e:
                logger.debug(f"   ⚠️ 공시 로드 실패 ({code}): {e}")
        cursor.close()
        return result
    
    def _calculate_recency_weight(self, 
                                  total_samples: int, 
                                  recent_samples: int,
                                  recent_win_rate: float,
                                  total_win_rate: float) -> float:
        """
        [v1.0.2] Recency Weighting 계산
        
        GPT 피드백: "Recency Weighting 반영 부족" 해결
        
        최근 데이터의 성과가 전체와 다르면 가중치 조정
        
        Returns:
            0.5 ~ 1.5 사이의 가중치 (1.0 = 중립)
        """
        if recent_samples < 5:
            return 1.0  # 샘플 부족시 중립
        
        # 최근 승률이 전체 승률보다 높으면 가중치 상승
        if total_win_rate > 0:
            ratio = recent_win_rate / total_win_rate
            # ratio가 1.2 이상이면 1.2, 0.8 이하면 0.8로 제한
            ratio = max(0.5, min(1.5, ratio))
        else:
            ratio = 1.0
        
        # 최근 샘플 비중도 고려 (최근 샘플이 많을수록 신뢰도 높음)
        sample_ratio = min(recent_samples / max(total_samples, 1), 0.5) * 2
        
        # 최종 가중치 = 승률 비율과 샘플 비중의 가중 평균
        recency_weight = ratio * 0.7 + (1.0 + (sample_ratio - 1.0) * 0.5) * 0.3
        
        return max(0.5, min(1.5, recency_weight))
    
    def _save_news_factor_stats(self, result: Dict) -> bool:
        """
        [v1.0.2] NEWS_FACTOR_STATS 테이블에 뉴스 영향도 저장
        
        GPT 피드백: "NEWS_FACTOR_STATS 테이블을 채우기 위한 분석 로직" 구현
        [v1.0.3] Claude Opus 4.5 피드백: Oracle MERGE INTO 호환성 추가
        """
        try:
            cursor = self.db_conn.cursor()
            
            columns = [
                'NEWS_CATEGORY', 'STOCK_GROUP', 'MARKET_REGIME',
                'WIN_RATE', 'AVG_RETURN', 'SAMPLE_COUNT',
                'CONFIDENCE_LEVEL', 'HOLDING_DAYS',
                'RECENT_WIN_RATE', 'RECENT_SAMPLE_COUNT',
                'RECENCY_WEIGHT', 'ANALYSIS_DATE'
            ]
            values = (
                result['category'],
                'ALL',  # 전체 종목 대상
                'ALL',  # 전체 시장 국면
                result['win_rate'],
                result['avg_return'],
                result['sample_count'],
                result['confidence'],
                result['holding_days'],
                result['recent_win_rate'],
                result['recent_sample_count'],
                result['recency_weight'],
                datetime.now().date(),
            )
            unique_keys = ['NEWS_CATEGORY', 'STOCK_GROUP', 'MARKET_REGIME', 'HOLDING_DAYS']
            
            execute_upsert(cursor, 'NEWS_FACTOR_STATS', columns, values, unique_keys)
            
            self.db_conn.commit()
            cursor.close()
            return True
            
        except Exception as e:
            logger.debug(f"   NEWS_FACTOR_STATS 저장 실패: {e}")
            return False
    
    def analyze_compound_conditions(self, stock_codes: List[str]) -> List[ConditionPerformance]:
        """
        [v1.0.2] 복합 조건 분석 (뉴스점수 70↑ + 외국인 순매수 등)
        
        GPT 피드백: "뉴스 및 수급 조건 분석 미흡" 해결
        
        분석 대상 복합 조건:
        1. 뉴스점수 70↑ + 외국인 순매수 5일 연속
        2. 뉴스점수 70↑ + 기관 순매수
        3. RSI 과매도 + 외국인 순매수
        4. 거래량 급증 + 외국인 순매수
        """
        logger.info(f"   [v1.0.2] 복합 조건 분석 시작 ({len(stock_codes)}개 종목)")
        
        results = []
        
        price_data = self._get_historical_prices(stock_codes)
        supply_data = self._get_supply_demand_data(stock_codes)
        news_data = self._get_news_sentiment_history(stock_codes)
        
        # 복합 조건 정의
        compound_conditions = [
            {
                'key': 'news_70_foreign_buy',
                'desc': '뉴스점수 70↑ + 외국인 순매수',
                'check': lambda p, s, n, d: self._check_news_foreign_condition(p, s, n, d, 70, True),
            },
            {
                'key': 'news_70_inst_buy',
                'desc': '뉴스점수 70↑ + 기관 순매수',
                'check': lambda p, s, n, d: self._check_news_inst_condition(p, s, n, d, 70, True),
            },
            {
                'key': 'rsi_oversold_foreign_buy',
                'desc': 'RSI 과매도 + 외국인 순매수',
                'check': lambda p, s, n, d: self._check_rsi_foreign_condition(p, s, n, d),
            },
            {
                'key': 'volume_surge_foreign_buy',
                'desc': '거래량 급증 + 외국인 순매수',
                'check': lambda p, s, n, d: self._check_volume_foreign_condition(p, s, n, d),
            },
            {
                'key': 'news_80_all_buy',
                'desc': '뉴스점수 80↑ + 외국인+기관 동시 순매수',
                'check': lambda p, s, n, d: self._check_news_all_buy_condition(p, s, n, d, 80),
            },
        ]
        
        for cond in compound_conditions:
            all_returns = []
            recent_returns = []
            recent_cutoff = datetime.now() - timedelta(days=90)
            
            for code in stock_codes:
                if code not in price_data:
                    continue
                
                prices_df = price_data[code]
                supply_df = supply_data.get(code, pd.DataFrame())
                news_df = news_data.get(code, pd.DataFrame())
                
                if len(prices_df) < 30:
                    continue
                
                # 조건 충족 시점 찾기
                condition_dates = cond['check'](prices_df, supply_df, news_df, code)
                
                if not condition_dates:
                    continue
                
                # 미래 수익률 계산
                forward_returns = self._calculate_forward_returns(prices_df, 5)
                
                for date in condition_dates:
                    price_idx = prices_df[prices_df['PRICE_DATE'] == date].index
                    if len(price_idx) == 0:
                        continue
                    
                    idx = price_idx[0]
                    if idx >= len(forward_returns):
                        continue
                    
                    ret = forward_returns.iloc[idx]
                    if pd.isna(ret):
                        continue
                    
                    all_returns.append(ret)
                    if date >= recent_cutoff:
                        recent_returns.append(ret)
            
            if len(all_returns) >= 5:
                returns_arr = np.array(all_returns)
                win_rate = (returns_arr > 0).mean()
                avg_return = returns_arr.mean()
                recent_win_rate = (np.array(recent_returns) > 0).mean() if len(recent_returns) > 0 else win_rate
                
                result = ConditionPerformance(
                    target_type='ALL',
                    target_code='ALL',
                    condition_key=cond['key'],
                    condition_desc=cond['desc'],
                    win_rate=round(win_rate, 4),
                    avg_return=round(avg_return, 4),
                    sample_count=len(all_returns),
                    recent_win_rate=round(recent_win_rate, 4),
                    recent_sample_count=len(recent_returns),
                )
                results.append(result)
                self.save_factor_performance(result)
                
                logger.info(f"   🔗 {cond['desc']}: 승률={win_rate:.1%}, "
                           f"평균수익률={avg_return:.2f}%, 표본={len(all_returns)}")
        
        return results
    
    def _check_news_foreign_condition(self, prices_df, supply_df, news_df, code, 
                                      threshold=70, foreign_buy=True) -> List:
        """뉴스점수 + 외국인 순매수 조건 체크"""
        condition_dates = []
        
        if news_df.empty or supply_df.empty:
            return condition_dates
        
        # [v1.0.5] 날짜 형식 정규화
        if 'TRADE_DATE' not in supply_df.columns:
            return condition_dates
        
        # 수급 데이터 날짜를 date로 변환
        supply_dates = {}
        for _, row in supply_df.iterrows():
            trade_date = row['TRADE_DATE']
            trade_date_only = trade_date.date() if hasattr(trade_date, 'date') else trade_date
            supply_dates[trade_date_only] = row
        
        for _, news_row in news_df.iterrows():
            sentiment = news_row.get('SENTIMENT_SCORE', 0)
            news_date_raw = news_row.get('NEWS_DATE')
            
            if sentiment < threshold:
                continue
            
            # [v1.0.5] 뉴스 날짜도 date로 변환
            news_date = news_date_raw.date() if hasattr(news_date_raw, 'date') else news_date_raw
            
            # 해당 날짜 외국인 순매수 확인
            if news_date not in supply_dates:
                continue
            
            supply_row = supply_dates[news_date]
            foreign_net = supply_row.get('FOREIGN_NET', 0)
            if foreign_net is None:
                foreign_net = 0
            
            if foreign_buy and foreign_net > 0:
                condition_dates.append(news_date_raw)  # 원본 날짜 반환
        
        return condition_dates
    
    def _check_news_inst_condition(self, prices_df, supply_df, news_df, code,
                                   threshold=70, inst_buy=True) -> List:
        """뉴스점수 + 기관 순매수 조건 체크"""
        condition_dates = []
        
        if news_df.empty or supply_df.empty:
            return condition_dates
        
        # [v1.0.5] 날짜 형식 정규화
        if 'TRADE_DATE' not in supply_df.columns:
            return condition_dates
        
        supply_dates = {}
        for _, row in supply_df.iterrows():
            trade_date = row['TRADE_DATE']
            trade_date_only = trade_date.date() if hasattr(trade_date, 'date') else trade_date
            supply_dates[trade_date_only] = row
        
        for _, news_row in news_df.iterrows():
            sentiment = news_row.get('SENTIMENT_SCORE', 0)
            news_date_raw = news_row.get('NEWS_DATE')
            
            if sentiment < threshold:
                continue
            
            news_date = news_date_raw.date() if hasattr(news_date_raw, 'date') else news_date_raw
            
            if news_date not in supply_dates:
                continue
            
            supply_row = supply_dates[news_date]
            inst_net = supply_row.get('INST_NET', 0)
            if inst_net is None:
                inst_net = 0
            
            if inst_buy and inst_net > 0:
                condition_dates.append(news_date_raw)
        
        return condition_dates
    
    def _check_rsi_foreign_condition(self, prices_df, supply_df, news_df, code) -> List:
        """RSI 과매도 + 외국인 순매수 조건 체크"""
        condition_dates = []
        
        if supply_df.empty or len(prices_df) < 20:
            return condition_dates
        
        # [v1.0.5] 수급 데이터 날짜 인덱싱
        if 'TRADE_DATE' not in supply_df.columns:
            return condition_dates
        
        supply_dates = {}
        for _, row in supply_df.iterrows():
            trade_date = row['TRADE_DATE']
            trade_date_only = trade_date.date() if hasattr(trade_date, 'date') else trade_date
            supply_dates[trade_date_only] = row
        
        # RSI 계산
        close = prices_df['CLOSE_PRICE']
        delta = close.diff()
        gain = delta.where(delta > 0, 0).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        
        for i, (_, row) in enumerate(prices_df.iterrows()):
            if i >= len(rsi) or pd.isna(rsi.iloc[i]):
                continue
            
            if rsi.iloc[i] >= 30:  # RSI 30 이상이면 과매도 아님
                continue
            
            price_date_raw = row['PRICE_DATE']
            price_date = price_date_raw.date() if hasattr(price_date_raw, 'date') else price_date_raw
            
            if price_date not in supply_dates:
                continue
            
            supply_row = supply_dates[price_date]
            foreign_net = supply_row.get('FOREIGN_NET', 0)
            if foreign_net is None:
                foreign_net = 0
            
            if foreign_net > 0:
                condition_dates.append(price_date_raw)
        
        return condition_dates
    
    def _check_volume_foreign_condition(self, prices_df, supply_df, news_df, code) -> List:
        """거래량 급증 + 외국인 순매수 조건 체크"""
        condition_dates = []
        
        if supply_df.empty or 'VOLUME' not in prices_df.columns or len(prices_df) < 25:
            return condition_dates
        
        # [v1.0.5] 수급 데이터 날짜 인덱싱
        if 'TRADE_DATE' not in supply_df.columns:
            return condition_dates
        
        supply_dates = {}
        for _, row in supply_df.iterrows():
            trade_date = row['TRADE_DATE']
            trade_date_only = trade_date.date() if hasattr(trade_date, 'date') else trade_date
            supply_dates[trade_date_only] = row
        
        vol_ma = prices_df['VOLUME'].rolling(20).mean()
        
        for i, (_, row) in enumerate(prices_df.iterrows()):
            if i >= len(vol_ma) or pd.isna(vol_ma.iloc[i]):
                continue
            
            if row['VOLUME'] <= vol_ma.iloc[i] * 2:  # 거래량 2배 미만
                continue
            
            price_date_raw = row['PRICE_DATE']
            price_date = price_date_raw.date() if hasattr(price_date_raw, 'date') else price_date_raw
            
            if price_date not in supply_dates:
                continue
            
            supply_row = supply_dates[price_date]
            foreign_net = supply_row.get('FOREIGN_NET', 0)
            if foreign_net is None:
                foreign_net = 0
            
            if foreign_net > 0:
                condition_dates.append(price_date_raw)
        
        return condition_dates
    
    def _check_news_all_buy_condition(self, prices_df, supply_df, news_df, code, threshold=80) -> List:
        """뉴스점수 + 외국인+기관 동시 순매수 조건 체크"""
        condition_dates = []
        
        if news_df.empty or supply_df.empty:
            return condition_dates
        
        # [v1.0.5] 날짜 형식 정규화
        if 'TRADE_DATE' not in supply_df.columns:
            return condition_dates
        
        supply_dates = {}
        for _, row in supply_df.iterrows():
            trade_date = row['TRADE_DATE']
            trade_date_only = trade_date.date() if hasattr(trade_date, 'date') else trade_date
            supply_dates[trade_date_only] = row
        
        for _, news_row in news_df.iterrows():
            sentiment = news_row.get('SENTIMENT_SCORE', 0)
            news_date_raw = news_row.get('NEWS_DATE')
            
            if sentiment < threshold:
                continue
            
            news_date = news_date_raw.date() if hasattr(news_date_raw, 'date') else news_date_raw
            
            if news_date not in supply_dates:
                continue
            
            supply_row = supply_dates[news_date]
            foreign_net = supply_row.get('FOREIGN_NET', 0)
            inst_net = supply_row.get('INST_NET', 0)
            
            if foreign_net is None:
                foreign_net = 0
            if inst_net is None:
                inst_net = 0
            
            if foreign_net > 0 and inst_net > 0:
                condition_dates.append(news_date_raw)
        
        return condition_dates
    
    def analyze_factor_with_financials(self,
                                       stock_codes: List[str],
                                       factor_key: str,
                                       forward_days: int = 5) -> FactorAnalysisResult:
        """
        [v1.0.3] 재무 데이터를 포함한 팩터 분석 (시점별 매칭)
        
        GPT 피드백: "PER/PBR/ROE 데이터 입력 없음" 해결
        [v1.0.3] 각 날짜에 해당하는 분기의 PER/PBR/ROE 사용
        """
        factor_def = self.FACTOR_DEFINITIONS.get(factor_key)
        if not factor_def:
            raise ValueError(f"Unknown factor: {factor_key}")
        
        logger.info(f"   (FactorAnalyzer) {factor_def['name']} 분석 중 (분기별 재무 데이터 매칭)...")
        
        # 가격 데이터 및 분기별 재무 데이터 조회
        price_data = self._get_historical_prices(stock_codes)
        financial_data = self._get_financial_data(stock_codes)
        
        all_factors = []
        all_returns = []
        
        for code, df in price_data.items():
            if len(df) < 150:
                continue
            
            # 재무 팩터의 경우: 각 날짜에 해당하는 분기의 값 사용
            calc_func = getattr(self, factor_def['calc_func'])
            
            if factor_key in ['value_per', 'value_pbr', 'quality_roe']:
                # [v1.0.3] 시점별 재무 데이터 매칭
                factor_values = []
                valid_indices = []
                
                for idx, row in df.iterrows():
                    # 해당 날짜의 재무 데이터 조회
                    fin = self._get_financial_at_date(financial_data, code, idx)
                    
                    if factor_key == 'value_per':
                        val = fin.get('per')
                        if val and val > 0:
                            factor_values.append(-val)  # 낮을수록 좋으므로 음수
                            valid_indices.append(idx)
                    elif factor_key == 'value_pbr':
                        val = fin.get('pbr')
                        if val and val > 0:
                            factor_values.append(-val)  # 낮을수록 좋으므로 음수
                            valid_indices.append(idx)
                    elif factor_key == 'quality_roe':
                        val = fin.get('roe')
                        if val is not None:
                            factor_values.append(val)  # 높을수록 좋음
                            valid_indices.append(idx)
                
                if len(factor_values) < 50:
                    continue
                
                factor_series = pd.Series(factor_values, index=valid_indices)
                forward_returns = self._calculate_forward_returns(df, forward_days)
                
                # 공통 인덱스에서만 값 추출
                common_idx = factor_series.index.intersection(forward_returns.index)
                if len(common_idx) < 50:
                    continue
                
                all_factors.extend(factor_series.loc[common_idx].tolist())
                all_returns.extend(forward_returns.loc[common_idx].tolist())
            else:
                factor_values = calc_func(df)
                forward_returns = self._calculate_forward_returns(df, forward_days)
                
                # 동일 길이 보장
                combined = pd.DataFrame({'factor': factor_values, 'return': forward_returns}).dropna()
                all_factors.extend(combined['factor'].tolist())
                all_returns.extend(combined['return'].tolist())
        
        if len(all_factors) < 100:
            logger.warning(f"   (FactorAnalyzer) {factor_key} 표본 부족 ({len(all_factors)}개)")
            return FactorAnalysisResult(
                factor_key=factor_key,
                factor_name=factor_def['name'],
                ic_mean=0.0,
                ic_std=1.0,
                ir=0.0,
                hit_rate=0.5,
                recommended_weight=0.05,
                sample_count=len(all_factors),
            )
        
        # IC 계산
        factor_series = pd.Series(all_factors)
        return_series = pd.Series(all_returns)
        ic_mean, ic_std, ir = self.calculate_ic(factor_series, return_series)
        
        # 적중률 계산
        combined = pd.DataFrame({'factor': all_factors, 'return': all_returns}).dropna()
        top_quantile = combined[combined['factor'] >= combined['factor'].quantile(0.8)]
        hit_rate = (top_quantile['return'] > 0).mean() if len(top_quantile) > 0 else 0.5
        
        # 추천 가중치 계산
        if ir >= 0.5:
            recommended_weight = min(0.20, 0.10 + ir * 0.1)
        elif ir >= 0:
            recommended_weight = 0.10
        else:
            recommended_weight = max(0.02, 0.05 + ir * 0.05)
        
        result = FactorAnalysisResult(
            factor_key=factor_key,
            factor_name=factor_def['name'],
            ic_mean=round(ic_mean, 4),
            ic_std=round(ic_std, 4),
            ir=round(ir, 4),
            hit_rate=round(hit_rate, 4),
            recommended_weight=round(recommended_weight, 4),
            sample_count=len(all_factors),
        )
        
        logger.info(f"   ✅ {factor_def['name']}: IC={ic_mean:.4f}, IR={ir:.4f}, "
                   f"적중률={hit_rate:.1%}, 표본={len(all_factors)}")
        
        return result
    
    def run_full_analysis(self, 
                          stock_codes: List[str] = None,
                          market_regime: str = 'ALL',
                          lookback_days: int = 730,
                          force_refresh: bool = False) -> Dict:
        """
        전체 팩터 분석 실행 (배치 작업)
        
        [v1.0.2] GPT 피드백 반영:
        - 재무 데이터(PER/PBR/ROE) 연동 팩터 분석
        - 뉴스 카테고리별 영향도 분석
        - 복합 조건 분석 (뉴스+수급)
        
        Args:
            stock_codes: 분석 대상 종목 (None이면 전체)
            market_regime: 시장 국면
            lookback_days: 분석 기간 (일)
            force_refresh: True면 캐시 무시하고 전체 재분석
        
        Returns:
            분석 결과 요약
        """
        # lookback_days와 force_refresh는 향후 캐시 로직에서 활용
        self.lookback_days = lookback_days
        self.force_refresh = force_refresh
        logger.info("=" * 60)
        logger.info("   🔬 FactorAnalyzer 전체 분석 시작 (v1.0.2)")
        logger.info("=" * 60)
        
        start_time = datetime.now()
        
        # 종목 목록 조회
        if stock_codes is None:
            stock_codes = self._get_all_stock_codes()
        
        logger.info(f"   대상 종목: {len(stock_codes)}개")
        
        results = {
            'factor_analysis': [],
            'condition_analysis': [],
            'news_category_analysis': [],
            'compound_condition_analysis': [],
            'errors': [],
        }
        
        # 1. 팩터별 예측력 분석 (재무 데이터 포함)
        logger.info("\n   [Step 1] 팩터 예측력 분석 (재무 데이터 연동)")
        
        # 재무 관련 팩터는 재무 데이터를 포함하여 분석
        financial_factors = ['value_per', 'value_pbr', 'quality_roe']
        
        for factor_key in self.FACTOR_DEFINITIONS.keys():
            try:
                if factor_key in financial_factors:
                    # [v1.0.2] 재무 데이터 포함 분석
                    result = self.analyze_factor_with_financials(stock_codes, factor_key)
                else:
                    result = self.analyze_factor(stock_codes, factor_key)
                
                results['factor_analysis'].append(result)
                self.save_factor_metadata(result, market_regime)
            except Exception as e:
                logger.error(f"   ❌ {factor_key} 분석 실패: {e}")
                results['errors'].append({'factor': factor_key, 'error': str(e)})
        
        # 2. 기본 조건부 성과 분석
        logger.info("\n   [Step 2] 기본 조건부 성과 분석")
        
        # RSI 과매도 조건
        def rsi_oversold_condition(df):
            close = df['CLOSE_PRICE']
            delta = close.diff()
            gain = delta.where(delta > 0, 0).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss
            rsi = 100 - (100 / (1 + rs))
            return rsi < 30
        
        # 거래량 급증 조건
        def volume_surge_condition(df):
            if 'VOLUME' not in df.columns:
                return pd.Series([False] * len(df))
            vol_ma = df['VOLUME'].rolling(20).mean()
            return df['VOLUME'] > vol_ma * 2
        
        conditions = {
            'rsi_oversold_30': rsi_oversold_condition,
            'volume_surge_2x': volume_surge_condition,
        }
        
        for code in stock_codes[:50]:  # 상위 50개 종목만 분석
            for cond_key, cond_func in conditions.items():
                try:
                    result = self.analyze_condition_performance(
                        code, cond_key, cond_func
                    )
                    if result:
                        results['condition_analysis'].append(result)
                        self.save_factor_performance(result)
                except Exception as e:
                    logger.debug(f"   {code}/{cond_key} 분석 실패: {e}")
        
        # 3. [v1.0.2] 뉴스 카테고리별 영향도 분석
        logger.info("\n   [Step 3] 뉴스 카테고리별 영향도 분석")
        try:
            news_results = self.analyze_news_category_impact(stock_codes)
            results['news_category_analysis'] = news_results
        except Exception as e:
            logger.error(f"   ❌ 뉴스 카테고리 분석 실패: {e}")
            results['errors'].append({'step': 'news_category', 'error': str(e)})
        
        # 4. [v1.0.2] 복합 조건 분석 (뉴스+수급)
        logger.info("\n   [Step 4] 복합 조건 분석 (뉴스+수급)")
        try:
            compound_results = self.analyze_compound_conditions(stock_codes)
            results['compound_condition_analysis'] = compound_results
        except Exception as e:
            logger.error(f"   ❌ 복합 조건 분석 실패: {e}")
            results['errors'].append({'step': 'compound_conditions', 'error': str(e)})

        # 5. [v1.0.3] 공시 영향도 분석
        logger.info("\n   [Step 5] 공시 영향도 분석 (DART)")
        try:
            disclosure_results = self.analyze_disclosure_impact(stock_codes)
            results['disclosure_analysis'] = disclosure_results
        except Exception as e:
            logger.error(f"   ❌ 공시 영향도 분석 실패: {e}")
            results['errors'].append({'step': 'disclosure', 'error': str(e)})
        
        # 6. [v1.0.6 Phase B] 장기 수익률 분석 (D+20, D+60)
        logger.info("\n   [Step 6] 장기 수익률 분석 (D+20, D+60)")
        results['long_term_analysis'] = {
            'D+20': [],
            'D+60': [],
        }
        
        # D+20 분석
        try:
            logger.info("   📈 D+20 수익률 분석 중...")
            for factor_key in ['technical_rsi_oversold', 'quality_roe', 'value_per']:
                if factor_key in financial_factors:
                    result = self.analyze_factor_with_financials(stock_codes, factor_key, forward_days=20)
                else:
                    result = self.analyze_factor(stock_codes, factor_key, forward_days=20)
                
                if result:
                    results['long_term_analysis']['D+20'].append({
                        'factor': factor_key,
                        'ic_mean': result.ic_mean,
                        'hit_rate': result.hit_rate,
                        'sample_count': result.sample_count,
                    })
                    logger.info(f"      {factor_key} (D+20): IC={result.ic_mean:.4f}, 적중률={result.hit_rate:.1%}")
        except Exception as e:
            logger.error(f"   ❌ D+20 분석 실패: {e}")
            results['errors'].append({'step': 'long_term_d20', 'error': str(e)})
        
        # D+60 분석
        try:
            logger.info("   📈 D+60 수익률 분석 중...")
            for factor_key in ['technical_rsi_oversold', 'quality_roe', 'value_per']:
                if factor_key in financial_factors:
                    result = self.analyze_factor_with_financials(stock_codes, factor_key, forward_days=60)
                else:
                    result = self.analyze_factor(stock_codes, factor_key, forward_days=60)
                
                if result:
                    results['long_term_analysis']['D+60'].append({
                        'factor': factor_key,
                        'ic_mean': result.ic_mean,
                        'hit_rate': result.hit_rate,
                        'sample_count': result.sample_count,
                    })
                    logger.info(f"      {factor_key} (D+60): IC={result.ic_mean:.4f}, 적중률={result.hit_rate:.1%}")
        except Exception as e:
            logger.error(f"   ❌ D+60 분석 실패: {e}")
            results['errors'].append({'step': 'long_term_d60', 'error': str(e)})
        
        # 7. [v1.0.6 Phase B] 뉴스 카테고리 장기 수익률 분석
        logger.info("\n   [Step 7] 뉴스 카테고리 장기 수익률 분석")
        results['news_long_term'] = {}
        
        for forward_days in [20, 60]:
            try:
                logger.info(f"   📰 뉴스 카테고리별 D+{forward_days} 분석 중...")
                news_results = self.analyze_news_category_impact(stock_codes, forward_days=forward_days)
                results['news_long_term'][f'D+{forward_days}'] = news_results
                
                for nr in news_results[:3]:  # 상위 3개만 로그
                    logger.info(f"      {nr.get('NEWS_CATEGORY', nr.get('category', 'N/A'))} (D+{forward_days}): "
                               f"승률={nr.get('WIN_RATE', nr.get('win_rate', 0)):.1%}, "
                               f"평균수익률={nr.get('AVG_RETURN', nr.get('avg_return', 0)):.2f}%")
            except Exception as e:
                logger.error(f"   ❌ 뉴스 D+{forward_days} 분석 실패: {e}")
                results['errors'].append({'step': f'news_d{forward_days}', 'error': str(e)})
        
        # 8. [v1.0.6 Phase B] 섹터별 분리 분석
        logger.info("\n   [Step 8] 섹터별 분리 분석")
        results['sector_analysis'] = {}
        
        try:
            # RSI 과매도 전략의 섹터별 효과
            logger.info("   🏭 RSI 과매도 전략 섹터별 분석...")
            rsi_by_sector = self.analyze_by_sector(stock_codes, 'technical_rsi_oversold', forward_days=5)
            results['sector_analysis']['rsi_oversold'] = rsi_by_sector
            
            # ROE 전략의 섹터별 효과
            logger.info("   🏭 ROE 전략 섹터별 분석...")
            roe_by_sector = self.analyze_by_sector(stock_codes, 'quality_roe', forward_days=5)
            results['sector_analysis']['quality_roe'] = roe_by_sector
            
            # 모멘텀 전략의 섹터별 효과
            logger.info("   🏭 모멘텀 전략 섹터별 분석...")
            momentum_by_sector = self.analyze_by_sector(stock_codes, 'momentum_1m', forward_days=5)
            results['sector_analysis']['momentum_1m'] = momentum_by_sector
            
        except Exception as e:
            logger.error(f"   ❌ 섹터별 분석 실패: {e}")
            results['errors'].append({'step': 'sector_analysis', 'error': str(e)})
        
        elapsed = (datetime.now() - start_time).total_seconds()
        
        logger.info("\n" + "=" * 60)
        logger.info(f"   🏁 FactorAnalyzer 분석 완료 (v1.0.6 Phase B, {elapsed:.1f}초)")
        logger.info(f"   - 팩터 분석 (D+5): {len(results['factor_analysis'])}개")
        logger.info(f"   - 기본 조건 분석: {len(results['condition_analysis'])}개")
        logger.info(f"   - 뉴스 카테고리 분석 (D+5): {len(results['news_category_analysis'])}개")
        logger.info(f"   - 복합 조건 분석: {len(results['compound_condition_analysis'])}개")
        logger.info(f"   - 공시 영향도 분석: {len(results.get('disclosure_analysis', []))}개")
        logger.info(f"   - 장기 수익률 (D+20): {len(results.get('long_term_analysis', {}).get('D+20', []))}개")
        logger.info(f"   - 장기 수익률 (D+60): {len(results.get('long_term_analysis', {}).get('D+60', []))}개")
        logger.info(f"   - 섹터별 분석: {len(results.get('sector_analysis', {}))}개 팩터")
        logger.info(f"   - 오류: {len(results['errors'])}개")
        logger.info("=" * 60)
        
        # [v1.0.5] 상세 요약 출력
        if results['factor_analysis']:
            logger.info("\n📊 [팩터별 요약]")
            for factor in results['factor_analysis']:
                if hasattr(factor, 'ic_mean'):
                    logger.info(f"   {factor.factor_key}: IC={factor.ic_mean:.4f}, "
                               f"적중률={factor.hit_rate:.1%}, 표본={factor.sample_count}")
        
        if results['compound_condition_analysis']:
            logger.info("\n🔗 [복합 조건 요약]")
            for cond in results['compound_condition_analysis']:
                logger.info(f"   {cond.condition_desc}: 승률={cond.win_rate:.1%}, "
                           f"평균수익률={cond.avg_return:.2f}%, 표본={cond.sample_count}")
        
        if results.get('disclosure_analysis'):
            logger.info("\n📑 [공시 영향도 요약]")
            for disc in results['disclosure_analysis']:
                logger.info(f"   {disc['category']}: 승률={disc['win_rate']:.1%}, "
                           f"평균수익률={disc['avg_return']:.2f}%, 표본={disc['sample_count']}")
        
        return results
    
    def _get_all_stock_codes(self) -> List[str]:
        """DB에서 전체 종목 코드 조회"""
        try:
            cursor = self.db_conn.cursor()
            cursor.execute("""
                SELECT DISTINCT STOCK_CODE 
                FROM STOCK_DAILY_PRICES_3Y
                WHERE STOCK_CODE != '0001'
                LIMIT 200
            """)
            rows = cursor.fetchall()
            cursor.close()
            
            if rows:
                return [row[0] if not isinstance(row, dict) else row['STOCK_CODE'] 
                        for row in rows]
            return []
        except Exception as e:
            logger.error(f"   종목 코드 조회 실패: {e}")
            return []
    
    # =========================================================================
    # [v1.0.5] 백테스트 시뮬레이션
    # =========================================================================
    
    def run_backtest(self, 
                     stock_codes: List[str] = None,
                     start_date: datetime = None,
                     end_date: datetime = None,
                     top_n: int = 15,
                     holding_days: int = 5) -> Dict:
        """
        [v1.0.5] 간단한 백테스트 시뮬레이션
        
        새로운 가중치와 전략으로 과거 데이터에서 시뮬레이션:
        1. 매일 정량 점수 상위 N개 종목 선정
        2. D+5 수익률 측정
        3. 전략별 성과 비교
        
        Args:
            stock_codes: 대상 종목 리스트 (None이면 전체)
            start_date: 백테스트 시작일
            end_date: 백테스트 종료일
            top_n: 매일 선정할 종목 수
            holding_days: 보유 기간
        
        Returns:
            백테스트 결과 딕셔너리
        """
        logger.info("\n" + "=" * 60)
        logger.info("   🧪 백테스트 시뮬레이션 시작 (v1.0.5)")
        logger.info("=" * 60)
        
        if stock_codes is None:
            stock_codes = self._get_all_stock_codes()
        
        if start_date is None:
            start_date = datetime.now() - timedelta(days=180)  # 최근 6개월
        if end_date is None:
            end_date = datetime.now() - timedelta(days=holding_days)  # 수익률 측정 가능한 마지막 날
        
        # 가격 데이터 로드
        price_data = self._get_historical_prices(stock_codes, days=365)
        news_data = self._get_news_sentiment_history(stock_codes)
        supply_data = self._get_supply_demand_data(stock_codes)
        
        # 시뮬레이션 결과
        results = {
            'strategy_rsi_roe': {'returns': [], 'win_count': 0, 'total_count': 0},
            'strategy_momentum': {'returns': [], 'win_count': 0, 'total_count': 0},
            'strategy_news_contrarian': {'returns': [], 'win_count': 0, 'total_count': 0},
        }
        
        # 날짜별 시뮬레이션
        # 실제 백테스트는 더 복잡하지만, 여기서는 간단 버전으로 구현
        test_dates = []
        for code in stock_codes[:5]:  # 대표 종목에서 날짜 추출
            if code in price_data:
                df = price_data[code]
                test_dates = df[
                    (df['PRICE_DATE'] >= start_date) & 
                    (df['PRICE_DATE'] <= end_date)
                ]['PRICE_DATE'].tolist()
                break
        
        if not test_dates:
            logger.warning("   백테스트 가능한 날짜 없음")
            return {'error': 'No test dates available'}
        
        logger.info(f"   테스트 기간: {len(test_dates)}일, 종목 수: {len(stock_codes)}")
        
        # 간단한 전략 시뮬레이션 (RSI 과매도 + 고ROE 전략)
        for i, test_date in enumerate(test_dates[::5]):  # 5일 간격으로 샘플링
            date_scores = []
            
            for code in stock_codes:
                if code not in price_data:
                    continue
                
                df = price_data[code]
                date_idx = df[df['PRICE_DATE'] <= test_date].index
                
                if len(date_idx) < 20:
                    continue
                
                last_idx = date_idx[-1]
                
                # RSI 계산
                close = df['CLOSE_PRICE'].iloc[:last_idx+1]
                if len(close) < 14:
                    continue
                
                delta = close.diff()
                gain = delta.where(delta > 0, 0).rolling(window=14).mean()
                loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
                rs = gain / loss
                rsi = 100 - (100 / (1 + rs))
                
                if pd.isna(rsi.iloc[-1]):
                    continue
                
                current_rsi = rsi.iloc[-1]
                
                # RSI 과매도 + 상승 모멘텀 조합 점수
                rsi_score = max(0, (30 - current_rsi) / 30) if current_rsi < 30 else 0
                
                # 미래 수익률 (백테스트용)
                future_idx = last_idx + holding_days
                if future_idx >= len(df):
                    continue
                
                future_price = df['CLOSE_PRICE'].iloc[future_idx]
                current_price = close.iloc[-1]
                forward_return = (future_price / current_price - 1) * 100
                
                date_scores.append({
                    'code': code,
                    'rsi': current_rsi,
                    'rsi_score': rsi_score,
                    'forward_return': forward_return,
                })
            
            if len(date_scores) < top_n:
                continue
            
            # RSI 과매도 전략 상위 N개 선정
            date_scores_sorted = sorted(date_scores, key=lambda x: x['rsi_score'], reverse=True)
            top_stocks = date_scores_sorted[:top_n]
            
            for stock in top_stocks:
                results['strategy_rsi_roe']['returns'].append(stock['forward_return'])
                if stock['forward_return'] > 0:
                    results['strategy_rsi_roe']['win_count'] += 1
                results['strategy_rsi_roe']['total_count'] += 1
        
        # 결과 요약
        logger.info("\n📊 [백테스트 결과 요약]")
        
        for strategy_name, strategy_result in results.items():
            if strategy_result['total_count'] > 0:
                returns = np.array(strategy_result['returns'])
                win_rate = strategy_result['win_count'] / strategy_result['total_count']
                avg_return = returns.mean()
                std_return = returns.std()
                sharpe = avg_return / std_return if std_return > 0 else 0
                
                logger.info(f"\n   {strategy_name}:")
                logger.info(f"      - 총 거래: {strategy_result['total_count']}건")
                logger.info(f"      - 승률: {win_rate:.1%}")
                logger.info(f"      - 평균 수익률: {avg_return:.2f}%")
                logger.info(f"      - 수익률 표준편차: {std_return:.2f}%")
                logger.info(f"      - 샤프 비율: {sharpe:.2f}")
                
                results[strategy_name]['win_rate'] = win_rate
                results[strategy_name]['avg_return'] = avg_return
                results[strategy_name]['sharpe'] = sharpe
        
        logger.info("\n" + "=" * 60)
        logger.info("   🏁 백테스트 완료")
        logger.info("=" * 60)
        
        return results


# =============================================================================
# 배치 작업 실행 함수
# =============================================================================

def run_weekly_factor_analysis(db_conn, market_regime: str = 'ALL') -> Dict:
    """
    주간 팩터 분석 배치 작업 실행
    
    Scout의 '지능'을 업데이트하는 주기적 작업.
    권장: 매주 일요일 또는 주말에 실행.
    
    Args:
        db_conn: DB 연결 객체
        market_regime: 현재 시장 국면
    
    Returns:
        분석 결과 요약
    """
    analyzer = FactorAnalyzer(db_conn)
    return analyzer.run_full_analysis(market_regime=market_regime)

