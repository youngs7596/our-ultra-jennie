#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Version: v1.0
# 작업 LLM: Claude Opus 4.5
"""
[v1.0] CompetitorAnalyzer - 경쟁사 수혜 분석 모듈

역할: 경쟁사의 악재 발생 시 반사이익을 포착하는 분석기

주요 기능:
1. 경쟁사 매핑: 특정 종목의 동일 섹터 경쟁사 조회
2. 악재 감지: 경쟁사의 최근 부정적 이벤트 탐지
3. 디커플링 분석: 리더 급락 시 팔로워 반응 통계 조회
4. 수혜 점수 계산: 반사이익 가산점 산출
5. 리포트 생성: 경쟁사 수혜 분석 결과 보고서

참조 모델:
- IndustryCompetitors: 산업/경쟁사 매핑
- EventImpactRules: 이벤트 영향 규칙
- SectorRelationStats: 섹터 관계 통계
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy import and_

from shared.db.connection import get_session
from shared.db.models import (
    IndustryCompetitors,
    EventImpactRules,
    SectorRelationStats,
    NewsSentiment,
)
from shared.news_classifier import (
    classify_news_category,
    get_event_severity,
    get_competitor_benefit,
    NEWS_CATEGORIES,
)

logger = logging.getLogger(__name__)


@dataclass
class CompetitorInfo:
    """경쟁사 정보"""
    stock_code: str
    stock_name: str
    sector_code: str
    sector_name: str
    market_share: float
    rank_in_sector: int
    is_leader: bool
    exchange: str


@dataclass
class NegativeEvent:
    """부정적 이벤트 정보"""
    stock_code: str
    stock_name: str
    event_type: str
    event_title: str
    severity: int
    published_at: datetime
    source_url: Optional[str] = None


@dataclass
class BenefitAnalysis:
    """경쟁사 수혜 분석 결과"""
    target_stock_code: str
    target_stock_name: str
    affected_competitor: str
    competitor_name: str
    event_type: str
    event_title: str
    
    # 점수 및 통계
    benefit_score: int
    decoupling_rate: float
    avg_benefit_return: float
    confidence: str
    
    # 전략 추천
    recommended_holding_days: int
    stop_loss_pct: float
    take_profit_pct: float
    
    # 메타데이터
    sample_count: int
    analysis_reason: str


@dataclass 
class CompetitorBenefitReport:
    """경쟁사 수혜 분석 종합 리포트"""
    target_stock_code: str
    target_stock_name: str
    sector_code: str
    sector_name: str
    
    # 분석 결과
    total_benefit_score: int = 0
    has_opportunity: bool = False
    benefits: List[BenefitAnalysis] = field(default_factory=list)
    competitor_events: List[NegativeEvent] = field(default_factory=list)
    
    # 요약
    summary: str = ""
    recommendation: str = ""


class CompetitorAnalyzer:
    """
    경쟁사 수혜 분석기
    
    경쟁사의 악재 발생 시 반사이익 기회를 분석합니다.
    
    사용 예시:
        analyzer = CompetitorAnalyzer()
        report = analyzer.analyze("035420")  # NAVER 분석
        
        if report.has_opportunity:
            print(f"수혜 기회 감지! 점수: {report.total_benefit_score}")
            for benefit in report.benefits:
                print(f"- {benefit.competitor_name}의 {benefit.event_type}로 인한 수혜")
    """
    
    # 분석 기간 설정
    DEFAULT_LOOKBACK_DAYS = 7  # 최근 7일 이내 이벤트만 분석
    MIN_DECOUPLING_RATE = 0.4  # 최소 디커플링 승률 40%
    MIN_BENEFIT_SCORE = 3      # 최소 수혜 점수
    
    def __init__(self):
        self._event_rules_cache: Optional[Dict[str, dict]] = None
        self._competitor_cache: Dict[str, List[CompetitorInfo]] = {}
    
    def analyze(self, stock_code: str, lookback_days: int = None) -> CompetitorBenefitReport:
        """
        특정 종목의 경쟁사 수혜 기회 분석
        
        Args:
            stock_code: 분석 대상 종목 코드
            lookback_days: 이벤트 탐색 기간 (일)
            
        Returns:
            CompetitorBenefitReport: 경쟁사 수혜 분석 결과
        """
        if lookback_days is None:
            lookback_days = self.DEFAULT_LOOKBACK_DAYS
            
        logger.info(f"🔍 [CompetitorAnalyzer] {stock_code} 경쟁사 수혜 분석 시작")
        
        session = get_session()
        try:
            # 1. 대상 종목의 섹터 및 경쟁사 정보 조회
            target_info = self._get_stock_info(session, stock_code)
            if not target_info:
                logger.warning(f"   종목 {stock_code}의 섹터 정보를 찾을 수 없습니다.")
                return CompetitorBenefitReport(
                    target_stock_code=stock_code,
                    target_stock_name="Unknown",
                    sector_code="",
                    sector_name="",
                    summary="섹터 정보 없음"
                )
            
            # 리포트 초기화
            report = CompetitorBenefitReport(
                target_stock_code=stock_code,
                target_stock_name=target_info.stock_name,
                sector_code=target_info.sector_code,
                sector_name=target_info.sector_name
            )
            
            # 2. 동일 섹터 경쟁사 목록 조회
            competitors = self._get_competitors(session, stock_code, target_info.sector_code)
            logger.info(f"   섹터 [{target_info.sector_name}] 경쟁사 {len(competitors)}개 발견")
            
            if not competitors:
                report.summary = "동일 섹터 경쟁사 없음"
                return report
            
            # 3. 각 경쟁사의 최근 부정적 이벤트 확인
            for competitor in competitors:
                events = self._get_negative_events(
                    session, 
                    competitor.stock_code, 
                    lookback_days
                )
                
                for event in events:
                    report.competitor_events.append(event)
                    
                    # 4. 수혜 점수 계산
                    benefit = self._calculate_benefit(
                        session,
                        target_info,
                        competitor,
                        event
                    )
                    
                    if benefit and benefit.benefit_score >= self.MIN_BENEFIT_SCORE:
                        report.benefits.append(benefit)
                        report.total_benefit_score += benefit.benefit_score
            
            # 5. 리포트 요약 생성
            report = self._generate_summary(report)
            
            logger.info(
                f"   분석 완료: 수혜점수={report.total_benefit_score}, "
                f"기회감지={'✅' if report.has_opportunity else '❌'}"
            )
            
            return report
            
        finally:
            session.close()
    
    def get_competitors_by_sector(self, sector_code: str) -> List[CompetitorInfo]:
        """
        특정 섹터의 모든 경쟁사 목록 조회
        
        Args:
            sector_code: 섹터 코드 (예: 'ECOM', 'SEMI')
            
        Returns:
            List[CompetitorInfo]: 경쟁사 정보 리스트
        """
        session = get_session()
        try:
            records = session.query(IndustryCompetitors).filter(
                and_(
                    IndustryCompetitors.sector_code == sector_code,
                    IndustryCompetitors.is_active == 1
                )
            ).order_by(IndustryCompetitors.rank_in_sector).all()
            
            return [
                CompetitorInfo(
                    stock_code=r.stock_code,
                    stock_name=r.stock_name,
                    sector_code=r.sector_code,
                    sector_name=r.sector_name,
                    market_share=r.market_share or 0.0,
                    rank_in_sector=r.rank_in_sector or 99,
                    is_leader=bool(r.is_leader),
                    exchange=r.exchange or 'KRX'
                )
                for r in records
            ]
        finally:
            session.close()
    
    def get_decoupling_stats(
        self, 
        sector_code: str,
        leader_code: str = None,
        follower_code: str = None
    ) -> List[dict]:
        """
        섹터의 디커플링 통계 조회
        
        Args:
            sector_code: 섹터 코드
            leader_code: 리더 종목 코드 (선택)
            follower_code: 팔로워 종목 코드 (선택)
            
        Returns:
            List[dict]: 디커플링 통계 리스트
        """
        session = get_session()
        try:
            query = session.query(SectorRelationStats).filter(
                SectorRelationStats.sector_code == sector_code
            )
            
            if leader_code:
                query = query.filter(SectorRelationStats.leader_stock_code == leader_code)
            if follower_code:
                query = query.filter(SectorRelationStats.follower_stock_code == follower_code)
            
            records = query.all()
            
            return [
                {
                    'sector_code': r.sector_code,
                    'sector_name': r.sector_name,
                    'leader_stock': r.leader_stock_name,
                    'follower_stock': r.follower_stock_name,
                    'decoupling_rate': r.decoupling_rate,
                    'avg_benefit_return': r.avg_benefit_return,
                    'sample_count': r.sample_count,
                    'confidence': r.confidence,
                    'recommended_holding_days': r.recommended_holding_days,
                    'stop_loss_pct': r.stop_loss_pct,
                    'take_profit_pct': r.take_profit_pct
                }
                for r in records
            ]
        finally:
            session.close()
    
    def get_event_impact_rules(self, event_type: str = None) -> Dict[str, dict]:
        """
        이벤트 영향 규칙 조회
        
        Args:
            event_type: 특정 이벤트 유형 (선택)
            
        Returns:
            Dict[str, dict]: 이벤트 유형별 영향 규칙
        """
        if self._event_rules_cache is None:
            session = get_session()
            try:
                records = session.query(EventImpactRules).filter(
                    EventImpactRules.is_active == 1
                ).all()
                
                self._event_rules_cache = {
                    r.event_type: {
                        'keywords': json.loads(r.event_keywords) if r.event_keywords else [],
                        'impact_on_self': r.impact_on_self,
                        'impact_on_competitor': r.impact_on_competitor,
                        'effect_duration_days': r.effect_duration_days,
                        'confidence_level': r.confidence_level,
                        'description': r.description
                    }
                    for r in records
                }
            finally:
                session.close()
        
        if event_type:
            return {event_type: self._event_rules_cache.get(event_type, {})}
        return self._event_rules_cache
    
    # ============== Private Methods ==============
    
    def _get_stock_info(self, session, stock_code: str) -> Optional[CompetitorInfo]:
        """종목의 섹터 및 기본 정보 조회"""
        record = session.query(IndustryCompetitors).filter(
            IndustryCompetitors.stock_code == stock_code
        ).first()
        
        if not record:
            return None
            
        return CompetitorInfo(
            stock_code=record.stock_code,
            stock_name=record.stock_name,
            sector_code=record.sector_code,
            sector_name=record.sector_name,
            market_share=record.market_share or 0.0,
            rank_in_sector=record.rank_in_sector or 99,
            is_leader=bool(record.is_leader),
            exchange=record.exchange or 'KRX'
        )
    
    def _get_competitors(
        self, 
        session, 
        stock_code: str, 
        sector_code: str
    ) -> List[CompetitorInfo]:
        """동일 섹터의 경쟁사 목록 조회 (자기 자신 제외)"""
        records = session.query(IndustryCompetitors).filter(
            and_(
                IndustryCompetitors.sector_code == sector_code,
                IndustryCompetitors.stock_code != stock_code,
                IndustryCompetitors.is_active == 1
            )
        ).order_by(IndustryCompetitors.rank_in_sector).all()
        
        return [
            CompetitorInfo(
                stock_code=r.stock_code,
                stock_name=r.stock_name,
                sector_code=r.sector_code,
                sector_name=r.sector_name,
                market_share=r.market_share or 0.0,
                rank_in_sector=r.rank_in_sector or 99,
                is_leader=bool(r.is_leader),
                exchange=r.exchange or 'KRX'
            )
            for r in records
        ]
    
    def _get_negative_events(
        self, 
        session, 
        stock_code: str, 
        lookback_days: int
    ) -> List[NegativeEvent]:
        """경쟁사의 최근 부정적 이벤트 조회"""
        cutoff_date = datetime.now() - timedelta(days=lookback_days)
        
        # NEWS_SENTIMENT 테이블에서 부정적 뉴스 조회
        records = session.query(NewsSentiment).filter(
            and_(
                NewsSentiment.stock_code == stock_code,
                NewsSentiment.sentiment_score < 40,  # 부정적 뉴스
                NewsSentiment.created_at >= cutoff_date
            )
        ).order_by(NewsSentiment.created_at.desc()).all()
        
        events = []
        event_rules = self.get_event_impact_rules()
        
        for record in records:
            title = record.news_title or ""
            reason = record.sentiment_reason or ""
            
            # 뉴스 카테고리 분류
            category = classify_news_category(title, reason)
            
            # 부정적 이벤트 카테고리만 필터링
            if category in event_rules:
                severity = get_event_severity(category)
                
                # 종목명 조회
                stock_info = self._get_stock_info(session, stock_code)
                stock_name = stock_info.stock_name if stock_info else stock_code
                
                events.append(NegativeEvent(
                    stock_code=stock_code,
                    stock_name=stock_name,
                    event_type=category,
                    event_title=title,
                    severity=severity,
                    published_at=record.published_at or record.created_at,
                    source_url=record.source_url
                ))
        
        return events
    
    def _calculate_benefit(
        self,
        session,
        target: CompetitorInfo,
        competitor: CompetitorInfo,
        event: NegativeEvent
    ) -> Optional[BenefitAnalysis]:
        """수혜 점수 계산"""
        
        # 디커플링 통계 조회
        stats = session.query(SectorRelationStats).filter(
            and_(
                SectorRelationStats.sector_code == target.sector_code,
                SectorRelationStats.leader_stock_code == competitor.stock_code,
                SectorRelationStats.follower_stock_code == target.stock_code
            )
        ).first()
        
        # 역방향도 확인 (리더-팔로워 관계가 반대일 수 있음)
        if not stats:
            stats = session.query(SectorRelationStats).filter(
                and_(
                    SectorRelationStats.sector_code == target.sector_code,
                    SectorRelationStats.leader_stock_code == competitor.stock_code
                )
            ).first()
        
        # 기본 수혜 점수 계산
        base_benefit = get_competitor_benefit(event.event_type)
        
        if stats and stats.decoupling_rate >= self.MIN_DECOUPLING_RATE:
            # 디커플링 승률이 높으면 추가 가산
            adjusted_benefit = int(base_benefit * (1 + stats.decoupling_rate))
            
            return BenefitAnalysis(
                target_stock_code=target.stock_code,
                target_stock_name=target.stock_name,
                affected_competitor=competitor.stock_code,
                competitor_name=competitor.stock_name,
                event_type=event.event_type,
                event_title=event.event_title,
                benefit_score=adjusted_benefit,
                decoupling_rate=stats.decoupling_rate or 0.0,
                avg_benefit_return=stats.avg_benefit_return or 0.0,
                confidence=stats.confidence or 'LOW',
                recommended_holding_days=stats.recommended_holding_days or 20,
                stop_loss_pct=stats.stop_loss_pct or -0.03,
                take_profit_pct=stats.take_profit_pct or 0.08,
                sample_count=stats.sample_count or 0,
                analysis_reason=f"경쟁사 {competitor.stock_name}의 {event.event_type}로 인한 수혜 예상"
            )
        elif base_benefit > 0:
            # 통계가 없어도 기본 수혜 점수 반환
            return BenefitAnalysis(
                target_stock_code=target.stock_code,
                target_stock_name=target.stock_name,
                affected_competitor=competitor.stock_code,
                competitor_name=competitor.stock_name,
                event_type=event.event_type,
                event_title=event.event_title,
                benefit_score=base_benefit,
                decoupling_rate=0.0,
                avg_benefit_return=0.0,
                confidence='LOW',
                recommended_holding_days=14,
                stop_loss_pct=-0.05,
                take_profit_pct=0.05,
                sample_count=0,
                analysis_reason=f"경쟁사 {competitor.stock_name}의 {event.event_type}로 인한 잠재적 수혜"
            )
        
        return None
    
    def _generate_summary(self, report: CompetitorBenefitReport) -> CompetitorBenefitReport:
        """리포트 요약 생성"""
        
        if not report.benefits:
            report.summary = "현재 감지된 경쟁사 악재 없음"
            report.recommendation = "관망"
            report.has_opportunity = False
            return report
        
        # 최고 점수 수혜 분석 찾기
        best_benefit = max(report.benefits, key=lambda x: x.benefit_score)
        
        if report.total_benefit_score >= 10:
            report.has_opportunity = True
            report.recommendation = "매수 검토"
        elif report.total_benefit_score >= 5:
            report.has_opportunity = True
            report.recommendation = "관심 종목 추가"
        else:
            report.has_opportunity = False
            report.recommendation = "관망"
        
        # 요약 생성
        event_summary = ", ".join(
            f"{b.competitor_name}({b.event_type})" 
            for b in report.benefits[:3]  # 최대 3개
        )
        
        report.summary = (
            f"🔴 경쟁사 악재 감지: {event_summary}\n"
            f"📊 총 수혜 점수: +{report.total_benefit_score}점\n"
            f"📈 디커플링 승률: {best_benefit.decoupling_rate:.0%}\n"
            f"💡 추천: {report.recommendation}"
        )
        
        return report


# ============== 유틸리티 함수 ==============

def analyze_competitor_benefit(stock_code: str) -> dict:
    """
    간편 분석 함수 - 단일 종목의 경쟁사 수혜 분석
    
    Args:
        stock_code: 종목 코드
        
    Returns:
        dict: 분석 결과 딕셔너리
    """
    analyzer = CompetitorAnalyzer()
    report = analyzer.analyze(stock_code)
    
    return {
        'stock_code': report.target_stock_code,
        'stock_name': report.target_stock_name,
        'sector': report.sector_name,
        'total_benefit_score': report.total_benefit_score,
        'has_opportunity': report.has_opportunity,
        'recommendation': report.recommendation,
        'summary': report.summary,
        'benefits': [
            {
                'competitor': b.competitor_name,
                'event_type': b.event_type,
                'score': b.benefit_score,
                'decoupling_rate': b.decoupling_rate,
                'confidence': b.confidence
            }
            for b in report.benefits
        ]
    }


def get_all_sectors() -> List[dict]:
    """모든 섹터 목록 조회"""
    session = get_session()
    try:
        from sqlalchemy import distinct
        records = session.query(
            distinct(IndustryCompetitors.sector_code),
            IndustryCompetitors.sector_name
        ).filter(IndustryCompetitors.is_active == 1).all()
        
        return [
            {'code': r[0], 'name': r[1]}
            for r in records
        ]
    finally:
        session.close()

