"""
shared/db/factor_repository.py - FactorAnalyzer용 SQLAlchemy Repository
========================================================================

factor_analyzer.py의 DB 접근 로직을 SQLAlchemy ORM으로 분리합니다.
테스트 가능하고 DB 독립적인 데이터 접근 레이어를 제공합니다.

사용 예시:
---------
>>> from shared.db.factor_repository import FactorRepository
>>> 
>>> repo = FactorRepository(session)
>>> prices = repo.get_historical_prices('005930', 504)
>>> financials = repo.get_financial_data(['005930', '000660'])
"""

import logging
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Tuple
import pandas as pd
from sqlalchemy import and_, desc, func
from sqlalchemy.orm import Session

from .models import (
    StockDailyPrice,
    StockMaster,
    FinancialMetricsQuarterly,
    StockInvestorTrading,
    StockNewsSentiment,
    StockDisclosures,
    FactorMetadata,
    FactorPerformance,
    NewsFactorStats,
    DailyQuantScore,
)

logger = logging.getLogger(__name__)


class FactorRepository:
    """
    FactorAnalyzer용 SQLAlchemy Repository
    
    DB 접근 로직을 ORM으로 캡슐화하여 테스트 용이성을 높입니다.
    """
    
    def __init__(self, session: Session):
        """
        Args:
            session: SQLAlchemy 세션
        """
        self.session = session
    
    # =========================================================================
    # 주가 데이터 조회
    # =========================================================================
    
    def get_historical_prices(
        self, 
        stock_code: str, 
        days: int = 504
    ) -> pd.DataFrame:
        """
        종목의 과거 주가 데이터 조회
        
        Args:
            stock_code: 종목 코드
            days: 조회할 일수 (기본 504일 = 약 2년)
        
        Returns:
            DataFrame with columns: [PRICE_DATE, CLOSE_PRICE, VOLUME, HIGH_PRICE, LOW_PRICE]
        """
        try:
            results = (
                self.session.query(
                    StockDailyPrice.price_date,
                    StockDailyPrice.close_price,
                    StockDailyPrice.volume,
                    StockDailyPrice.high_price,
                    StockDailyPrice.low_price
                )
                .filter(StockDailyPrice.stock_code == stock_code)
                .order_by(desc(StockDailyPrice.price_date))
                .limit(days)
                .all()
            )
            
            if not results:
                return pd.DataFrame()
            
            df = pd.DataFrame(results, columns=[
                'PRICE_DATE', 'CLOSE_PRICE', 'VOLUME', 'HIGH_PRICE', 'LOW_PRICE'
            ])
            df['PRICE_DATE'] = pd.to_datetime(df['PRICE_DATE'])
            df = df.sort_values('PRICE_DATE').reset_index(drop=True)
            
            return df
            
        except Exception as e:
            logger.error(f"❌ [FactorRepo] 주가 데이터 조회 실패 ({stock_code}): {e}")
            return pd.DataFrame()
    
    def get_historical_prices_bulk(
        self, 
        stock_codes: List[str], 
        days: int = 504
    ) -> Dict[str, pd.DataFrame]:
        """
        여러 종목의 과거 주가 데이터 일괄 조회
        
        Args:
            stock_codes: 종목 코드 리스트
            days: 조회할 일수
        
        Returns:
            {stock_code: DataFrame} 딕셔너리
        """
        result = {}
        for code in stock_codes:
            result[code] = self.get_historical_prices(code, days)
        return result
    
    # =========================================================================
    # 종목 마스터 조회
    # =========================================================================
    
    def get_market_cap(self, stock_code: str) -> Optional[int]:
        """
        종목 시가총액 조회
        
        Args:
            stock_code: 종목 코드
        
        Returns:
            시가총액 (원) 또는 None
        """
        try:
            result = (
                self.session.query(StockMaster.market_cap)
                .filter(StockMaster.stock_code == stock_code)
                .first()
            )
            
            if result and result[0]:
                return int(result[0])
            return None
            
        except Exception as e:
            logger.warning(f"⚠️ [FactorRepo] 시가총액 조회 실패 ({stock_code}): {e}")
            return None
    
    def get_stock_sector(self, stock_code: str) -> Tuple[Optional[str], Optional[str]]:
        """
        종목 섹터 정보 조회
        
        Args:
            stock_code: 종목 코드
        
        Returns:
            (sector_kospi200, industry_code) 튜플
        """
        try:
            result = (
                self.session.query(
                    StockMaster.sector_kospi200,
                    StockMaster.industry_code
                )
                .filter(StockMaster.stock_code == stock_code)
                .first()
            )
            
            if result:
                return result[0], result[1]
            return None, None
            
        except Exception as e:
            logger.warning(f"⚠️ [FactorRepo] 섹터 조회 실패 ({stock_code}): {e}")
            return None, None
    
    # =========================================================================
    # 재무 데이터 조회
    # =========================================================================
    
    def get_financial_data(
        self, 
        stock_codes: List[str]
    ) -> Dict[str, Dict]:
        """
        여러 종목의 재무 데이터 조회 (PER, PBR, ROE)
        
        Args:
            stock_codes: 종목 코드 리스트
        
        Returns:
            {stock_code: {quarter_date: {per, pbr, roe}}} 딕셔너리
        """
        try:
            results = (
                self.session.query(
                    FinancialMetricsQuarterly.stock_code,
                    FinancialMetricsQuarterly.quarter_date,
                    FinancialMetricsQuarterly.per,
                    FinancialMetricsQuarterly.pbr,
                    FinancialMetricsQuarterly.roe
                )
                .filter(FinancialMetricsQuarterly.stock_code.in_(stock_codes))
                .order_by(
                    FinancialMetricsQuarterly.stock_code,
                    desc(FinancialMetricsQuarterly.quarter_date)
                )
                .all()
            )
            
            financial_data = {}
            for row in results:
                code = row[0]
                quarter_date = row[1]
                
                if code not in financial_data:
                    financial_data[code] = {}
                
                # date 객체를 문자열로 변환
                quarter_key = quarter_date.strftime('%Y-%m-%d') if isinstance(quarter_date, date) else str(quarter_date)
                
                financial_data[code][quarter_key] = {
                    'per': row[2],
                    'pbr': row[3],
                    'roe': row[4]
                }
            
            return financial_data
            
        except Exception as e:
            logger.error(f"❌ [FactorRepo] 재무 데이터 조회 실패: {e}")
            return {}
    
    # =========================================================================
    # 수급 데이터 조회
    # =========================================================================
    
    def get_supply_demand_data(
        self, 
        stock_codes: List[str], 
        days: int = 504
    ) -> Dict[str, pd.DataFrame]:
        """
        여러 종목의 투자자별 매매 동향 조회
        
        Args:
            stock_codes: 종목 코드 리스트
            days: 조회할 일수
        
        Returns:
            {stock_code: DataFrame} 딕셔너리
        """
        result = {}
        
        try:
            for code in stock_codes:
                rows = (
                    self.session.query(
                        StockInvestorTrading.trade_date,
                        StockInvestorTrading.foreign_net_buy,
                        StockInvestorTrading.institution_net_buy
                    )
                    .filter(StockInvestorTrading.stock_code == code)
                    .order_by(desc(StockInvestorTrading.trade_date))
                    .limit(days)
                    .all()
                )
                
                if rows:
                    df = pd.DataFrame(rows, columns=[
                        'TRADE_DATE', 'FOREIGN_NET_BUY', 'INSTITUTION_NET_BUY'
                    ])
                    df['TRADE_DATE'] = pd.to_datetime(df['TRADE_DATE'])
                    df = df.sort_values('TRADE_DATE').reset_index(drop=True)
                    result[code] = df
                else:
                    result[code] = pd.DataFrame()
            
            return result
            
        except Exception as e:
            logger.error(f"❌ [FactorRepo] 수급 데이터 조회 실패: {e}")
            return {code: pd.DataFrame() for code in stock_codes}
    
    # =========================================================================
    # 뉴스 감성 데이터 조회
    # =========================================================================
    
    def get_news_sentiment_history(
        self, 
        stock_codes: List[str], 
        days: int = 504
    ) -> Dict[str, pd.DataFrame]:
        """
        여러 종목의 뉴스 감성 히스토리 조회
        
        Args:
            stock_codes: 종목 코드 리스트
            days: 조회할 일수
        
        Returns:
            {stock_code: DataFrame} 딕셔너리
        """
        result = {}
        
        try:
            for code in stock_codes:
                rows = (
                    self.session.query(
                        StockNewsSentiment.news_date,
                        StockNewsSentiment.sentiment_score,
                        StockNewsSentiment.category
                    )
                    .filter(StockNewsSentiment.stock_code == code)
                    .order_by(desc(StockNewsSentiment.news_date))
                    .limit(days)
                    .all()
                )
                
                if rows:
                    df = pd.DataFrame(rows, columns=[
                        'NEWS_DATE', 'SENTIMENT_SCORE', 'CATEGORY'
                    ])
                    df['NEWS_DATE'] = pd.to_datetime(df['NEWS_DATE'])
                    df = df.sort_values('NEWS_DATE').reset_index(drop=True)
                    result[code] = df
                else:
                    result[code] = pd.DataFrame()
            
            return result
            
        except Exception as e:
            logger.error(f"❌ [FactorRepo] 뉴스 감성 데이터 조회 실패: {e}")
            return {code: pd.DataFrame() for code in stock_codes}
    
    # =========================================================================
    # 공시 데이터 조회
    # =========================================================================
    
    def get_disclosures(
        self, 
        stock_codes: List[str], 
        lookback_days: int = 365
    ) -> Dict[str, List[Dict]]:
        """
        여러 종목의 공시 정보 조회
        
        Args:
            stock_codes: 종목 코드 리스트
            lookback_days: 조회할 기간 (일)
        
        Returns:
            {stock_code: [{'date': ..., 'category': ...}]} 딕셔너리
        """
        result = {code: [] for code in stock_codes}
        
        try:
            cutoff_date = datetime.now().date() - timedelta(days=lookback_days)
            
            rows = (
                self.session.query(
                    StockDisclosures.stock_code,
                    StockDisclosures.disclosure_date,
                    StockDisclosures.category
                )
                .filter(
                    and_(
                        StockDisclosures.stock_code.in_(stock_codes),
                        StockDisclosures.disclosure_date >= cutoff_date
                    )
                )
                .order_by(desc(StockDisclosures.disclosure_date))
                .all()
            )
            
            for row in rows:
                code = row[0]
                if code in result:
                    result[code].append({
                        'date': row[1],
                        'category': row[2]
                    })
            
            return result
            
        except Exception as e:
            logger.error(f"❌ [FactorRepo] 공시 데이터 조회 실패: {e}")
            return result
    
    # =========================================================================
    # 팩터 메타데이터 저장/조회
    # =========================================================================
    
    def save_factor_metadata(
        self,
        factor_key: str,
        factor_name: str,
        market_regime: str,
        ic_mean: float,
        ic_std: float,
        ir: float,
        hit_rate: float,
        recommended_weight: float,
        sample_count: int,
        analysis_start_date: date = None,
        analysis_end_date: date = None
    ) -> bool:
        """
        팩터 메타데이터 저장 (UPSERT)
        
        Returns:
            성공 여부
        """
        try:
            # 기존 레코드 조회
            existing = (
                self.session.query(FactorMetadata)
                .filter(
                    and_(
                        FactorMetadata.factor_key == factor_key,
                        FactorMetadata.market_regime == market_regime
                    )
                )
                .first()
            )
            
            if existing:
                # UPDATE
                existing.factor_name = factor_name
                existing.ic_mean = ic_mean
                existing.ic_std = ic_std
                existing.ir = ir
                existing.hit_rate = hit_rate
                existing.recommended_weight = recommended_weight
                existing.sample_count = sample_count
                existing.analysis_start_date = analysis_start_date
                existing.analysis_end_date = analysis_end_date
                existing.updated_at = datetime.now()
            else:
                # INSERT
                new_record = FactorMetadata(
                    factor_key=factor_key,
                    factor_name=factor_name,
                    market_regime=market_regime,
                    ic_mean=ic_mean,
                    ic_std=ic_std,
                    ir=ir,
                    hit_rate=hit_rate,
                    recommended_weight=recommended_weight,
                    sample_count=sample_count,
                    analysis_start_date=analysis_start_date,
                    analysis_end_date=analysis_end_date
                )
                self.session.add(new_record)
            
            self.session.commit()
            logger.debug(f"✅ [FactorRepo] 팩터 메타데이터 저장: {factor_key}")
            return True
            
        except Exception as e:
            self.session.rollback()
            logger.error(f"❌ [FactorRepo] 팩터 메타데이터 저장 실패: {e}")
            return False
    
    def get_factor_metadata(
        self, 
        factor_key: str, 
        market_regime: str = 'ALL'
    ) -> Optional[FactorMetadata]:
        """
        팩터 메타데이터 조회
        """
        try:
            return (
                self.session.query(FactorMetadata)
                .filter(
                    and_(
                        FactorMetadata.factor_key == factor_key,
                        FactorMetadata.market_regime == market_regime
                    )
                )
                .first()
            )
        except Exception as e:
            logger.warning(f"⚠️ [FactorRepo] 팩터 메타데이터 조회 실패: {e}")
            return None
    
    # =========================================================================
    # 팩터 성과 저장/조회
    # =========================================================================
    
    def save_factor_performance(
        self,
        target_type: str,
        target_code: str,
        target_name: str,
        condition_key: str,
        condition_desc: str,
        win_rate: float,
        avg_return: float,
        sample_count: int,
        holding_days: int = 5,
        recent_win_rate: float = None,
        recent_sample_count: int = None,
        analysis_date: date = None
    ) -> bool:
        """
        팩터 성과 저장 (UPSERT)
        
        Returns:
            성공 여부
        """
        try:
            # 기존 레코드 조회
            existing = (
                self.session.query(FactorPerformance)
                .filter(
                    and_(
                        FactorPerformance.target_type == target_type,
                        FactorPerformance.target_code == target_code,
                        FactorPerformance.condition_key == condition_key,
                        FactorPerformance.holding_days == holding_days
                    )
                )
                .first()
            )
            
            # 신뢰도 계산
            confidence_level = 'HIGH' if sample_count >= 30 else ('MID' if sample_count >= 15 else 'LOW')
            
            if existing:
                # UPDATE
                existing.target_name = target_name
                existing.condition_desc = condition_desc
                existing.win_rate = win_rate
                existing.avg_return = avg_return
                existing.sample_count = sample_count
                existing.confidence_level = confidence_level
                existing.recent_win_rate = recent_win_rate
                existing.recent_sample_count = recent_sample_count
                existing.analysis_date = analysis_date or datetime.now().date()
                existing.updated_at = datetime.now()
            else:
                # INSERT
                new_record = FactorPerformance(
                    target_type=target_type,
                    target_code=target_code,
                    target_name=target_name,
                    condition_key=condition_key,
                    condition_desc=condition_desc,
                    win_rate=win_rate,
                    avg_return=avg_return,
                    sample_count=sample_count,
                    holding_days=holding_days,
                    confidence_level=confidence_level,
                    recent_win_rate=recent_win_rate,
                    recent_sample_count=recent_sample_count,
                    analysis_date=analysis_date or datetime.now().date()
                )
                self.session.add(new_record)
            
            self.session.commit()
            logger.debug(f"✅ [FactorRepo] 팩터 성과 저장: {target_code}/{condition_key}")
            return True
            
        except Exception as e:
            self.session.rollback()
            logger.error(f"❌ [FactorRepo] 팩터 성과 저장 실패: {e}")
            return False
    
    def get_factor_performance(
        self, 
        target_type: str, 
        target_code: str, 
        condition_key: str,
        holding_days: int = 5
    ) -> Optional[FactorPerformance]:
        """
        팩터 성과 조회
        """
        try:
            return (
                self.session.query(FactorPerformance)
                .filter(
                    and_(
                        FactorPerformance.target_type == target_type,
                        FactorPerformance.target_code == target_code,
                        FactorPerformance.condition_key == condition_key,
                        FactorPerformance.holding_days == holding_days
                    )
                )
                .first()
            )
        except Exception as e:
            logger.warning(f"⚠️ [FactorRepo] 팩터 성과 조회 실패: {e}")
            return None
    
    # =========================================================================
    # 뉴스 팩터 통계 저장
    # =========================================================================
    
    def save_news_factor_stats(
        self,
        target_type: str,
        target_code: str,
        news_category: str,
        sentiment: str,
        win_rate: float,
        avg_excess_return: float,
        avg_absolute_return: float,
        sample_count: int,
        return_d1: float = None,
        return_d5: float = None,
        return_d20: float = None,
        win_rate_d1: float = None,
        win_rate_d5: float = None,
        win_rate_d20: float = None,
        analysis_date: date = None
    ) -> bool:
        """
        뉴스 팩터 통계 저장 (UPSERT)
        
        Returns:
            성공 여부
        """
        try:
            # 기존 레코드 조회
            existing = (
                self.session.query(NewsFactorStats)
                .filter(
                    and_(
                        NewsFactorStats.target_type == target_type,
                        NewsFactorStats.target_code == target_code,
                        NewsFactorStats.news_category == news_category,
                        NewsFactorStats.sentiment == sentiment
                    )
                )
                .first()
            )
            
            confidence_level = 'HIGH' if sample_count >= 30 else ('MID' if sample_count >= 15 else 'LOW')
            
            if existing:
                # UPDATE
                existing.win_rate = win_rate
                existing.avg_excess_return = avg_excess_return
                existing.avg_absolute_return = avg_absolute_return
                existing.sample_count = sample_count
                existing.confidence_level = confidence_level
                existing.return_d1 = return_d1
                existing.return_d5 = return_d5
                existing.return_d20 = return_d20
                existing.win_rate_d1 = win_rate_d1
                existing.win_rate_d5 = win_rate_d5
                existing.win_rate_d20 = win_rate_d20
                existing.analysis_date = analysis_date or datetime.now().date()
                existing.updated_at = datetime.now()
            else:
                # INSERT
                new_record = NewsFactorStats(
                    target_type=target_type,
                    target_code=target_code,
                    news_category=news_category,
                    sentiment=sentiment,
                    win_rate=win_rate,
                    avg_excess_return=avg_excess_return,
                    avg_absolute_return=avg_absolute_return,
                    sample_count=sample_count,
                    confidence_level=confidence_level,
                    return_d1=return_d1,
                    return_d5=return_d5,
                    return_d20=return_d20,
                    win_rate_d1=win_rate_d1,
                    win_rate_d5=win_rate_d5,
                    win_rate_d20=win_rate_d20,
                    analysis_date=analysis_date or datetime.now().date()
                )
                self.session.add(new_record)
            
            self.session.commit()
            logger.debug(f"✅ [FactorRepo] 뉴스 팩터 통계 저장: {target_code}/{news_category}")
            return True
            
        except Exception as e:
            self.session.rollback()
            logger.error(f"❌ [FactorRepo] 뉴스 팩터 통계 저장 실패: {e}")
            return False

