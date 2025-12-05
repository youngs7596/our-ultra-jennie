#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Version: v1.0
# 작업 LLM: Claude Opus 4.5
"""
[v1.0] 경쟁사 수혜 전략 백테스트 모듈

과거 데이터를 기반으로 경쟁사 수혜 전략의 유효성을 검증합니다.

주요 기능:
1. 디커플링 분석: 1등 기업 급락 시 2등 기업 반응 분석
2. 수익률 시뮬레이션: 가상 매매 결과 계산
3. 통계 검증: 승률, 평균 수익률, 샤프 비율 등
4. 결과 저장: SECTOR_RELATION_STATS 테이블 업데이트

사용 예시:
    from shared.strategies.competitor_backtest import CompetitorBacktester
    
    backtester = CompetitorBacktester()
    results = backtester.run_decoupling_analysis('ECOM', lookback_days=730)
    
    print(f"디커플링 승률: {results['decoupling_rate']:.0%}")
    print(f"평균 수익률: {results['avg_benefit_return']:.1%}")
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import pandas as pd
import numpy as np

from sqlalchemy import and_, func

from shared.db.connection import get_session
from shared.db.models import (
    IndustryCompetitors,
    SectorRelationStats,
    StockDailyPrice,
)

logger = logging.getLogger(__name__)


@dataclass
class DecouplingEvent:
    """디커플링 이벤트"""
    date: datetime
    leader_code: str
    leader_name: str
    leader_return: float  # 리더 수익률 (음수 = 급락)
    follower_code: str
    follower_name: str
    follower_return: float  # 팔로워 수익률
    is_decoupled: bool  # 팔로워가 양수 수익 = 디커플링 성공
    forward_return_5d: Optional[float] = None  # D+5 수익률
    forward_return_10d: Optional[float] = None  # D+10 수익률
    forward_return_20d: Optional[float] = None  # D+20 수익률


@dataclass
class BacktestResult:
    """백테스트 결과"""
    sector_code: str
    sector_name: str
    leader_code: str
    leader_name: str
    follower_code: str
    follower_name: str
    
    # 기간
    start_date: datetime
    end_date: datetime
    lookback_days: int
    
    # 통계
    total_events: int  # 총 급락 이벤트 수
    decoupling_count: int  # 디커플링 성공 수
    decoupling_rate: float  # 디커플링 승률
    
    # 수익률
    avg_leader_drop: float  # 리더 평균 급락률
    avg_follower_return: float  # 팔로워 평균 수익률 (당일)
    avg_benefit_return_5d: float  # 팔로워 D+5 평균 수익률
    avg_benefit_return_10d: float  # 팔로워 D+10 평균 수익률
    avg_benefit_return_20d: float  # 팔로워 D+20 평균 수익률
    
    # 리스크 지표
    max_drawdown: float  # 최대 손실
    sharpe_ratio: float  # 샤프 비율
    win_rate_5d: float  # D+5 승률
    win_rate_10d: float  # D+10 승률
    win_rate_20d: float  # D+20 승률
    
    # 신뢰도
    confidence: str  # HIGH, MID, LOW
    
    # 상세 이벤트
    events: List[DecouplingEvent] = field(default_factory=list)


class CompetitorBacktester:
    """
    경쟁사 수혜 전략 백테스터
    
    과거 데이터를 분석하여 디커플링 전략의 유효성을 검증합니다.
    """
    
    # 급락 기준
    CRASH_THRESHOLD = -0.03  # -3% 이상 급락
    
    # 기본 분석 기간
    DEFAULT_LOOKBACK_DAYS = 730  # 2년
    
    # 최소 조건
    MIN_DECOUPLING_RATE = 0.5   # 디커플링 승률 50% 이상
    MIN_SAMPLE_COUNT = 20       # 최소 표본 수
    
    # 신뢰도 기준
    CONFIDENCE_THRESHOLDS = {
        'HIGH': {'min_rate': 0.6, 'min_samples': 50},
        'MID': {'min_rate': 0.5, 'min_samples': 30},
        'LOW': {'min_rate': 0.0, 'min_samples': 0}
    }
    
    def __init__(self, crash_threshold: float = None):
        """
        Args:
            crash_threshold: 급락 기준 (기본값: -3%)
        """
        self.crash_threshold = crash_threshold or self.CRASH_THRESHOLD
    
    def run_decoupling_analysis(
        self,
        sector_code: str,
        lookback_days: int = None
    ) -> List[BacktestResult]:
        """
        특정 섹터의 디커플링 분석을 실행합니다.
        
        Args:
            sector_code: 섹터 코드 (예: 'ECOM', 'SEMI')
            lookback_days: 분석 기간 (일)
        
        Returns:
            List[BacktestResult]: 각 리더-팔로워 쌍의 백테스트 결과
        """
        if lookback_days is None:
            lookback_days = self.DEFAULT_LOOKBACK_DAYS
        
        logger.info(f"📊 [{sector_code}] 섹터 디커플링 분석 시작 (기간: {lookback_days}일)")
        
        session = get_session()
        try:
            # 1. 섹터 내 종목 조회
            stocks = session.query(IndustryCompetitors).filter(
                and_(
                    IndustryCompetitors.sector_code == sector_code,
                    IndustryCompetitors.is_active == 1
                )
            ).order_by(IndustryCompetitors.rank_in_sector).all()
            
            if len(stocks) < 2:
                logger.warning(f"   섹터 {sector_code}에 종목이 2개 미만입니다.")
                return []
            
            sector_name = stocks[0].sector_name
            
            # 2. 리더 종목 식별 (is_leader=1 또는 rank_in_sector=1)
            leaders = [s for s in stocks if s.is_leader == 1 or s.rank_in_sector == 1]
            followers = [s for s in stocks if s.stock_code not in [l.stock_code for l in leaders]]
            
            if not leaders or not followers:
                logger.warning(f"   리더 또는 팔로워가 없습니다.")
                return []
            
            # 3. 각 리더-팔로워 쌍에 대해 분석
            results = []
            for leader in leaders:
                for follower in followers:
                    result = self._analyze_pair(
                        session,
                        leader,
                        follower,
                        sector_code,
                        sector_name,
                        lookback_days
                    )
                    if result:
                        results.append(result)
            
            logger.info(f"✅ [{sector_code}] 분석 완료: {len(results)}개 쌍")
            
            return results
            
        finally:
            session.close()
    
    def run_all_sectors_analysis(self, lookback_days: int = None) -> Dict[str, List[BacktestResult]]:
        """
        모든 섹터의 디커플링 분석을 실행합니다.
        """
        if lookback_days is None:
            lookback_days = self.DEFAULT_LOOKBACK_DAYS
        
        session = get_session()
        try:
            # 모든 섹터 조회
            sectors = session.query(
                IndustryCompetitors.sector_code,
                IndustryCompetitors.sector_name
            ).distinct().all()
            
            all_results = {}
            for sector_code, sector_name in sectors:
                results = self.run_decoupling_analysis(sector_code, lookback_days)
                if results:
                    all_results[sector_code] = results
            
            return all_results
            
        finally:
            session.close()
    
    def update_sector_stats(self, results: List[BacktestResult]) -> int:
        """
        백테스트 결과를 SECTOR_RELATION_STATS 테이블에 업데이트합니다.
        
        Args:
            results: 백테스트 결과 리스트
        
        Returns:
            업데이트된 레코드 수
        """
        session = get_session()
        updated_count = 0
        
        try:
            for result in results:
                # 기존 레코드 조회
                existing = session.query(SectorRelationStats).filter(
                    and_(
                        SectorRelationStats.sector_code == result.sector_code,
                        SectorRelationStats.leader_stock_code == result.leader_code,
                        SectorRelationStats.follower_stock_code == result.follower_code
                    )
                ).first()
                
                if existing:
                    # 업데이트
                    existing.decoupling_rate = result.decoupling_rate
                    existing.avg_benefit_return = result.avg_benefit_return_20d
                    existing.avg_leader_drop = result.avg_leader_drop
                    existing.sample_count = result.total_events
                    existing.lookback_days = result.lookback_days
                    existing.confidence = result.confidence
                    existing.last_calculated = datetime.now(timezone.utc)
                    
                    # 권장 전략 업데이트
                    existing.recommended_holding_days = self._get_best_holding_period(result)
                    existing.stop_loss_pct = result.max_drawdown * 1.5  # 최대 손실의 1.5배
                    existing.take_profit_pct = result.avg_benefit_return_20d * 2  # 평균 수익의 2배
                else:
                    # 신규 생성
                    new_record = SectorRelationStats(
                        sector_code=result.sector_code,
                        sector_name=result.sector_name,
                        leader_stock_code=result.leader_code,
                        leader_stock_name=result.leader_name,
                        follower_stock_code=result.follower_code,
                        follower_stock_name=result.follower_name,
                        decoupling_rate=result.decoupling_rate,
                        avg_benefit_return=result.avg_benefit_return_20d,
                        avg_leader_drop=result.avg_leader_drop,
                        sample_count=result.total_events,
                        lookback_days=result.lookback_days,
                        confidence=result.confidence,
                        recommended_holding_days=self._get_best_holding_period(result),
                        stop_loss_pct=result.max_drawdown * 1.5,
                        take_profit_pct=result.avg_benefit_return_20d * 2
                    )
                    session.add(new_record)
                
                updated_count += 1
            
            session.commit()
            logger.info(f"✅ SECTOR_RELATION_STATS {updated_count}개 레코드 업데이트 완료")
            
        except Exception as e:
            session.rollback()
            logger.error(f"❌ 통계 업데이트 실패: {e}")
            raise
        finally:
            session.close()
        
        return updated_count
    
    def _analyze_pair(
        self,
        session,
        leader: IndustryCompetitors,
        follower: IndustryCompetitors,
        sector_code: str,
        sector_name: str,
        lookback_days: int
    ) -> Optional[BacktestResult]:
        """리더-팔로워 쌍의 디커플링 분석"""
        
        leader_code = leader.stock_code
        follower_code = follower.stock_code
        
        logger.debug(f"   분석 중: {leader.stock_name} → {follower.stock_name}")
        
        # 기간 설정
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=lookback_days)
        
        # 가격 데이터 조회
        leader_prices = self._get_price_data(session, leader_code, start_date, end_date)
        follower_prices = self._get_price_data(session, follower_code, start_date, end_date)
        
        if leader_prices.empty or follower_prices.empty:
            logger.debug(f"   가격 데이터 없음 (Skip)")
            return None
        
        # 수익률 계산
        leader_prices['return'] = leader_prices['close'].pct_change()
        follower_prices['return'] = follower_prices['close'].pct_change()
        
        # 리더 급락 일자 식별
        crash_dates = leader_prices[leader_prices['return'] < self.crash_threshold].index
        
        if len(crash_dates) < 5:
            logger.debug(f"   급락 이벤트 부족: {len(crash_dates)}건")
            return None
        
        # 디커플링 분석
        events = []
        forward_returns_5d = []
        forward_returns_10d = []
        forward_returns_20d = []
        
        for crash_date in crash_dates:
            if crash_date not in follower_prices.index:
                continue
            
            leader_return = leader_prices.loc[crash_date, 'return']
            follower_return = follower_prices.loc[crash_date, 'return']
            is_decoupled = follower_return > 0
            
            # Forward return 계산
            forward_5d = self._calc_forward_return(follower_prices, crash_date, 5)
            forward_10d = self._calc_forward_return(follower_prices, crash_date, 10)
            forward_20d = self._calc_forward_return(follower_prices, crash_date, 20)
            
            event = DecouplingEvent(
                date=crash_date,
                leader_code=leader_code,
                leader_name=leader.stock_name,
                leader_return=leader_return,
                follower_code=follower_code,
                follower_name=follower.stock_name,
                follower_return=follower_return,
                is_decoupled=is_decoupled,
                forward_return_5d=forward_5d,
                forward_return_10d=forward_10d,
                forward_return_20d=forward_20d
            )
            events.append(event)
            
            if forward_5d is not None:
                forward_returns_5d.append(forward_5d)
            if forward_10d is not None:
                forward_returns_10d.append(forward_10d)
            if forward_20d is not None:
                forward_returns_20d.append(forward_20d)
        
        if not events:
            return None
        
        # 통계 계산
        total_events = len(events)
        decoupling_count = sum(1 for e in events if e.is_decoupled)
        decoupling_rate = decoupling_count / total_events if total_events > 0 else 0
        
        avg_leader_drop = np.mean([e.leader_return for e in events])
        avg_follower_return = np.mean([e.follower_return for e in events])
        
        avg_benefit_5d = np.mean(forward_returns_5d) if forward_returns_5d else 0
        avg_benefit_10d = np.mean(forward_returns_10d) if forward_returns_10d else 0
        avg_benefit_20d = np.mean(forward_returns_20d) if forward_returns_20d else 0
        
        # 승률 계산
        win_rate_5d = sum(1 for r in forward_returns_5d if r > 0) / len(forward_returns_5d) if forward_returns_5d else 0
        win_rate_10d = sum(1 for r in forward_returns_10d if r > 0) / len(forward_returns_10d) if forward_returns_10d else 0
        win_rate_20d = sum(1 for r in forward_returns_20d if r > 0) / len(forward_returns_20d) if forward_returns_20d else 0
        
        # MDD 계산
        max_drawdown = min([e.forward_return_20d for e in events if e.forward_return_20d is not None] or [0])
        
        # 샤프 비율 (간이 계산)
        if forward_returns_20d and len(forward_returns_20d) > 1:
            sharpe_ratio = np.mean(forward_returns_20d) / (np.std(forward_returns_20d) + 1e-6) * np.sqrt(252/20)
        else:
            sharpe_ratio = 0
        
        # 신뢰도 결정
        confidence = self._determine_confidence(decoupling_rate, total_events)
        
        return BacktestResult(
            sector_code=sector_code,
            sector_name=sector_name,
            leader_code=leader_code,
            leader_name=leader.stock_name,
            follower_code=follower_code,
            follower_name=follower.stock_name,
            start_date=start_date,
            end_date=end_date,
            lookback_days=lookback_days,
            total_events=total_events,
            decoupling_count=decoupling_count,
            decoupling_rate=decoupling_rate,
            avg_leader_drop=avg_leader_drop,
            avg_follower_return=avg_follower_return,
            avg_benefit_return_5d=avg_benefit_5d,
            avg_benefit_return_10d=avg_benefit_10d,
            avg_benefit_return_20d=avg_benefit_20d,
            max_drawdown=max_drawdown,
            sharpe_ratio=sharpe_ratio,
            win_rate_5d=win_rate_5d,
            win_rate_10d=win_rate_10d,
            win_rate_20d=win_rate_20d,
            confidence=confidence,
            events=events
        )
    
    def _get_price_data(self, session, stock_code: str, start_date, end_date) -> pd.DataFrame:
        """가격 데이터 조회"""
        records = session.query(StockDailyPrice).filter(
            and_(
                StockDailyPrice.stock_code == stock_code,
                StockDailyPrice.price_date >= start_date,
                StockDailyPrice.price_date <= end_date
            )
        ).order_by(StockDailyPrice.price_date).all()
        
        if not records:
            return pd.DataFrame()
        
        data = [{
            'date': r.price_date,
            'close': r.close_price,
            'volume': r.volume
        } for r in records]
        
        df = pd.DataFrame(data)
        df.set_index('date', inplace=True)
        return df
    
    def _calc_forward_return(self, prices: pd.DataFrame, start_date, days: int) -> Optional[float]:
        """N일 후 수익률 계산"""
        try:
            start_idx = prices.index.get_loc(start_date)
            end_idx = start_idx + days
            
            if end_idx >= len(prices):
                return None
            
            start_price = prices.iloc[start_idx]['close']
            end_price = prices.iloc[end_idx]['close']
            
            return (end_price - start_price) / start_price
        except Exception:
            return None
    
    def _determine_confidence(self, decoupling_rate: float, sample_count: int) -> str:
        """신뢰도 결정"""
        for level, thresholds in self.CONFIDENCE_THRESHOLDS.items():
            if decoupling_rate >= thresholds['min_rate'] and sample_count >= thresholds['min_samples']:
                return level
        return 'LOW'
    
    def _get_best_holding_period(self, result: BacktestResult) -> int:
        """최적 보유 기간 결정"""
        returns = [
            (5, result.avg_benefit_return_5d, result.win_rate_5d),
            (10, result.avg_benefit_return_10d, result.win_rate_10d),
            (20, result.avg_benefit_return_20d, result.win_rate_20d),
        ]
        
        # 수익률 * 승률 기준 최적 기간
        best = max(returns, key=lambda x: x[1] * x[2] if x[1] and x[2] else 0)
        return best[0]
    
    def format_result_report(self, result: BacktestResult) -> str:
        """결과 리포트 포맷팅"""
        lines = [
            "═" * 70,
            f"📊 디커플링 백테스트 결과",
            "═" * 70,
            f"섹터: {result.sector_name} ({result.sector_code})",
            f"기간: {result.start_date.strftime('%Y-%m-%d')} ~ {result.end_date.strftime('%Y-%m-%d')}",
            "",
            f"리더: {result.leader_name} ({result.leader_code})",
            f"팔로워: {result.follower_name} ({result.follower_code})",
            "",
            "─" * 70,
            "📈 핵심 지표",
            "─" * 70,
            f"총 급락 이벤트: {result.total_events}건",
            f"디커플링 성공: {result.decoupling_count}건",
            f"디커플링 승률: {result.decoupling_rate:.1%}",
            "",
            f"리더 평균 급락: {result.avg_leader_drop:.1%}",
            f"팔로워 당일 수익: {result.avg_follower_return:.1%}",
            "",
            "─" * 70,
            "📊 기간별 성과",
            "─" * 70,
            f"D+5  평균 수익: {result.avg_benefit_return_5d:.1%} (승률: {result.win_rate_5d:.0%})",
            f"D+10 평균 수익: {result.avg_benefit_return_10d:.1%} (승률: {result.win_rate_10d:.0%})",
            f"D+20 평균 수익: {result.avg_benefit_return_20d:.1%} (승률: {result.win_rate_20d:.0%})",
            "",
            "─" * 70,
            "⚠️ 리스크 지표",
            "─" * 70,
            f"최대 손실 (MDD): {result.max_drawdown:.1%}",
            f"샤프 비율: {result.sharpe_ratio:.2f}",
            "",
            f"신뢰도: {result.confidence}",
            "═" * 70,
        ]
        
        return "\n".join(lines)


# ============================================================================
# 유틸리티 함수
# ============================================================================

def run_full_backtest() -> Dict:
    """
    전체 섹터 백테스트를 실행하고 결과를 저장합니다.
    """
    backtester = CompetitorBacktester()
    
    # 모든 섹터 분석
    all_results = backtester.run_all_sectors_analysis()
    
    # 결과 저장
    total_updated = 0
    for sector_code, results in all_results.items():
        updated = backtester.update_sector_stats(results)
        total_updated += updated
    
    # 요약 생성
    summary = {
        'sectors_analyzed': len(all_results),
        'pairs_analyzed': sum(len(r) for r in all_results.values()),
        'records_updated': total_updated,
        'results': {}
    }
    
    for sector_code, results in all_results.items():
        sector_summary = []
        for r in results:
            sector_summary.append({
                'leader': r.leader_name,
                'follower': r.follower_name,
                'decoupling_rate': r.decoupling_rate,
                'avg_return_20d': r.avg_benefit_return_20d,
                'confidence': r.confidence
            })
        summary['results'][sector_code] = sector_summary
    
    return summary


def backtest_single_sector(sector_code: str, lookback_days: int = 730) -> List[Dict]:
    """
    단일 섹터 백테스트를 실행합니다.
    """
    backtester = CompetitorBacktester()
    results = backtester.run_decoupling_analysis(sector_code, lookback_days)
    
    return [
        {
            'leader': r.leader_name,
            'follower': r.follower_name,
            'decoupling_rate': r.decoupling_rate,
            'avg_return_5d': r.avg_benefit_return_5d,
            'avg_return_10d': r.avg_benefit_return_10d,
            'avg_return_20d': r.avg_benefit_return_20d,
            'win_rate_20d': r.win_rate_20d,
            'max_drawdown': r.max_drawdown,
            'sharpe_ratio': r.sharpe_ratio,
            'confidence': r.confidence,
            'sample_count': r.total_events
        }
        for r in results
    ]

