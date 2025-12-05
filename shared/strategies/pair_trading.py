#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Version: v1.0
# 작업 LLM: Claude Opus 4.5
"""
[v1.0] 페어 트레이딩 전략 모듈

경쟁사 악재 발생 시 롱/숏 페어 트레이딩 전략을 생성합니다.

주요 기능:
1. 페어 신호 생성: 피해 기업 숏 + 수혜 기업 롱
2. 디커플링 통계 기반 신뢰도 평가
3. 리스크 관리: 손절/익절 설정
4. 포지션 사이징: 변동성 기반 가중치

⚠️ 주의사항:
- 페어 트레이딩은 고급 전략입니다
- 숏 포지션은 무제한 손실 가능
- 한국 시장은 공매도 제한이 있음
- 개인 투자자에게는 권장하지 않음
- 충분한 백테스트 필수

사용 예시:
    from shared.strategies.pair_trading import PairTradingStrategy
    
    strategy = PairTradingStrategy()
    signal = strategy.generate_pair_signal({
        'affected_company': '쿠팡',
        'affected_code': 'CPNG',
        'event_type': '보안사고',
        'severity': -15
    })
    
    if signal:
        print(f"롱: {signal['long']['stock_name']}")
        print(f"숏: {signal['short']['stock_name']}")
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy import and_

from shared.db.connection import get_session
from shared.db.models import (
    IndustryCompetitors,
    EventImpactRules,
    SectorRelationStats,
)

logger = logging.getLogger(__name__)


@dataclass
class PairPosition:
    """페어 포지션 정보"""
    stock_code: str
    stock_name: str
    direction: str  # 'LONG' or 'SHORT'
    weight: float   # 0.0 ~ 1.0
    entry_reason: str
    exchange: str = 'KRX'


@dataclass
class PairSignal:
    """페어 트레이딩 신호"""
    signal_id: str
    strategy_type: str = 'PAIR_TRADE'
    
    # 포지션
    long_position: Optional[PairPosition] = None
    short_position: Optional[PairPosition] = None
    
    # 이벤트 정보
    event_type: str = ''
    event_severity: int = 0
    affected_company: str = ''
    
    # 통계 기반 기대값
    decoupling_rate: float = 0.0
    expected_spread: float = 0.0
    expected_return: float = 0.0
    confidence: str = 'LOW'
    
    # 리스크 관리
    stop_loss_pct: float = -0.03
    take_profit_pct: float = 0.08
    max_holding_days: int = 20
    
    # 메타데이터
    sector_code: str = ''
    sector_name: str = ''
    sample_count: int = 0
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: Optional[datetime] = None
    
    # 실행 가능 여부
    is_executable: bool = False
    execution_notes: List[str] = field(default_factory=list)


class PairTradingStrategy:
    """
    페어 트레이딩 전략 클래스
    
    경쟁사 악재 발생 시 롱/숏 페어 전략을 생성합니다.
    
    ⚠️ 이 전략은 고급 투자자용이며, 개인 투자자에게 권장하지 않습니다.
    """
    
    # 최소 조건
    MIN_DECOUPLING_RATE = 0.5   # 디커플링 승률 50% 이상
    MIN_SAMPLE_COUNT = 20       # 최소 표본 수
    MIN_SEVERITY = -8           # 최소 악재 심각도
    
    # 포지션 설정
    DEFAULT_WEIGHT = 0.5        # 기본 롱/숏 동일 가중치
    MAX_POSITION_SIZE = 0.1     # 최대 포트폴리오 비중 10%
    
    def __init__(self):
        self._event_rules_cache: Optional[Dict] = None
        self._signal_counter = 0
    
    def generate_pair_signal(self, event: Dict) -> Optional[PairSignal]:
        """
        페어 트레이딩 신호를 생성합니다.
        
        Args:
            event: {
                'affected_company': '쿠팡',
                'affected_code': 'CPNG',
                'event_type': '보안사고',
                'severity': -15,
                'event_title': '개인정보 3370만건 유출'
            }
        
        Returns:
            PairSignal 또는 None (조건 미충족 시)
        """
        affected_code = event.get('affected_code')
        affected_company = event.get('affected_company')
        event_type = event.get('event_type')
        severity = event.get('severity', 0)
        
        if not all([affected_code, affected_company, event_type]):
            logger.warning("필수 이벤트 정보 누락")
            return None
        
        # 1. 악재 심각도 확인
        if severity > self.MIN_SEVERITY:
            logger.info(f"악재 심각도 미달: {severity} > {self.MIN_SEVERITY}")
            return None
        
        session = get_session()
        try:
            # 2. 피해 기업의 섹터 정보 조회
            affected_stock = session.query(IndustryCompetitors).filter(
                IndustryCompetitors.stock_code == affected_code
            ).first()
            
            if not affected_stock:
                logger.warning(f"{affected_code}는 경쟁사 매핑에 없음")
                return None
            
            sector_code = affected_stock.sector_code
            sector_name = affected_stock.sector_name
            
            # 3. 디커플링 통계 조회
            stats = session.query(SectorRelationStats).filter(
                SectorRelationStats.leader_stock_code == affected_code
            ).order_by(SectorRelationStats.decoupling_rate.desc()).first()
            
            if not stats:
                # 리더가 아닌 경우, 같은 섹터의 다른 통계 조회
                stats = session.query(SectorRelationStats).filter(
                    SectorRelationStats.sector_code == sector_code
                ).order_by(SectorRelationStats.decoupling_rate.desc()).first()
            
            if not stats:
                logger.warning(f"{sector_name} 섹터 디커플링 통계 없음")
                return None
            
            # 4. 디커플링 승률 확인
            if stats.decoupling_rate < self.MIN_DECOUPLING_RATE:
                logger.info(
                    f"디커플링 승률 미달: {stats.decoupling_rate:.0%} < {self.MIN_DECOUPLING_RATE:.0%}"
                )
                return None
            
            if stats.sample_count < self.MIN_SAMPLE_COUNT:
                logger.info(
                    f"표본 수 미달: {stats.sample_count} < {self.MIN_SAMPLE_COUNT}"
                )
                return None
            
            # 5. 수혜 기업 정보 조회
            beneficiary_code = stats.follower_stock_code
            beneficiary = session.query(IndustryCompetitors).filter(
                IndustryCompetitors.stock_code == beneficiary_code
            ).first()
            
            if not beneficiary:
                logger.warning(f"수혜 기업 정보 없음: {beneficiary_code}")
                return None
            
            # 6. 페어 신호 생성
            self._signal_counter += 1
            signal_id = f"PAIR_{datetime.now().strftime('%Y%m%d')}_{self._signal_counter:04d}"
            
            # 신뢰도 결정
            if stats.decoupling_rate >= 0.6 and stats.sample_count >= 50:
                confidence = 'HIGH'
            elif stats.decoupling_rate >= 0.5 and stats.sample_count >= 30:
                confidence = 'MID'
            else:
                confidence = 'LOW'
            
            # 예상 스프레드 (롱 수익 + 숏 수익)
            expected_spread = stats.avg_benefit_return + abs(stats.avg_leader_drop or 0)
            
            # 만료 시간
            expires_at = datetime.now(timezone.utc) + timedelta(
                days=stats.recommended_holding_days or 20
            )
            
            # 실행 가능 여부 및 노트
            is_executable = True
            execution_notes = []
            
            # 숏 가능 여부 체크
            if affected_stock.exchange == 'KRX':
                execution_notes.append("⚠️ 한국 시장: 개인 공매도 제한")
                is_executable = False
            elif affected_stock.exchange in ['NYSE', 'NASDAQ']:
                execution_notes.append("✅ 미국 시장: 공매도 가능")
            
            # 신호 객체 생성
            signal = PairSignal(
                signal_id=signal_id,
                strategy_type='PAIR_TRADE',
                
                long_position=PairPosition(
                    stock_code=beneficiary.stock_code,
                    stock_name=beneficiary.stock_name,
                    direction='LONG',
                    weight=self.DEFAULT_WEIGHT,
                    entry_reason=f"경쟁사 {affected_company}의 {event_type}로 인한 수혜 예상",
                    exchange=beneficiary.exchange or 'KRX'
                ),
                
                short_position=PairPosition(
                    stock_code=affected_stock.stock_code,
                    stock_name=affected_stock.stock_name,
                    direction='SHORT',
                    weight=self.DEFAULT_WEIGHT,
                    entry_reason=f"{event_type}으로 인한 하락 예상",
                    exchange=affected_stock.exchange or 'KRX'
                ),
                
                event_type=event_type,
                event_severity=severity,
                affected_company=affected_company,
                
                decoupling_rate=stats.decoupling_rate,
                expected_spread=expected_spread,
                expected_return=expected_spread,  # 스프레드 = 기대 수익
                confidence=confidence,
                
                stop_loss_pct=stats.stop_loss_pct or -0.03,
                take_profit_pct=stats.take_profit_pct or 0.08,
                max_holding_days=stats.recommended_holding_days or 20,
                
                sector_code=sector_code,
                sector_name=sector_name,
                sample_count=stats.sample_count,
                expires_at=expires_at,
                
                is_executable=is_executable,
                execution_notes=execution_notes
            )
            
            logger.info(
                f"✅ 페어 신호 생성: {signal_id}\n"
                f"   롱: {signal.long_position.stock_name} ({signal.long_position.stock_code})\n"
                f"   숏: {signal.short_position.stock_name} ({signal.short_position.stock_code})\n"
                f"   디커플링 승률: {signal.decoupling_rate:.0%}, 신뢰도: {confidence}"
            )
            
            return signal
            
        finally:
            session.close()
    
    def get_active_pair_signals(self) -> List[Dict]:
        """
        현재 활성화된 페어 트레이딩 신호 목록을 조회합니다.
        (CompetitorBenefitEvents 테이블에서 ACTIVE 상태인 것들)
        """
        from shared.db.models import CompetitorBenefitEvents
        
        session = get_session()
        try:
            events = session.query(CompetitorBenefitEvents).filter(
                and_(
                    CompetitorBenefitEvents.status == 'ACTIVE',
                    CompetitorBenefitEvents.expires_at >= datetime.now(timezone.utc)
                )
            ).order_by(CompetitorBenefitEvents.benefit_score.desc()).all()
            
            signals = []
            for event in events:
                signal = self.generate_pair_signal({
                    'affected_code': event.affected_stock_code,
                    'affected_company': event.affected_stock_name,
                    'event_type': event.event_type,
                    'severity': event.event_severity,
                    'event_title': event.event_title
                })
                
                if signal:
                    signals.append({
                        'signal': signal,
                        'event_id': event.id,
                        'detected_at': event.detected_at,
                        'expires_at': event.expires_at
                    })
            
            return signals
            
        finally:
            session.close()
    
    def format_signal_for_display(self, signal: PairSignal) -> str:
        """
        페어 신호를 표시용 문자열로 포맷팅합니다.
        """
        if not signal:
            return "신호 없음"
        
        lines = [
            "═" * 60,
            f"📊 페어 트레이딩 신호: {signal.signal_id}",
            "═" * 60,
            "",
            f"🔴 피해 기업: {signal.affected_company}",
            f"   이벤트: {signal.event_type} (심각도: {signal.event_severity})",
            "",
            "📈 롱 포지션",
        ]
        
        if signal.long_position:
            lines.extend([
                f"   종목: {signal.long_position.stock_name} ({signal.long_position.stock_code})",
                f"   비중: {signal.long_position.weight:.0%}",
                f"   사유: {signal.long_position.entry_reason}",
            ])
        
        lines.append("")
        lines.append("📉 숏 포지션")
        
        if signal.short_position:
            lines.extend([
                f"   종목: {signal.short_position.stock_name} ({signal.short_position.stock_code})",
                f"   비중: {signal.short_position.weight:.0%}",
                f"   사유: {signal.short_position.entry_reason}",
            ])
        
        lines.extend([
            "",
            "📊 통계 기반 기대값",
            f"   디커플링 승률: {signal.decoupling_rate:.0%}",
            f"   예상 스프레드: {signal.expected_spread:.1%}",
            f"   표본 수: {signal.sample_count}건",
            f"   신뢰도: {signal.confidence}",
            "",
            "⚙️ 리스크 관리",
            f"   손절선: {signal.stop_loss_pct:.1%}",
            f"   익절선: {signal.take_profit_pct:.1%}",
            f"   최대 보유 기간: {signal.max_holding_days}일",
            "",
        ])
        
        if signal.execution_notes:
            lines.append("📝 실행 참고사항")
            for note in signal.execution_notes:
                lines.append(f"   {note}")
            lines.append("")
        
        is_exec = "✅ 실행 가능" if signal.is_executable else "❌ 실행 불가"
        lines.append(f"상태: {is_exec}")
        lines.append("═" * 60)
        
        return "\n".join(lines)


# ============================================================================
# 유틸리티 함수
# ============================================================================

def analyze_pair_opportunity(affected_code: str, event_type: str, severity: int) -> Optional[Dict]:
    """
    간편 페어 트레이딩 분석 함수
    
    Args:
        affected_code: 피해 기업 종목코드
        event_type: 이벤트 유형
        severity: 악재 심각도
    
    Returns:
        페어 신호 딕셔너리 또는 None
    """
    strategy = PairTradingStrategy()
    
    # 피해 기업 정보 조회
    session = get_session()
    try:
        stock = session.query(IndustryCompetitors).filter(
            IndustryCompetitors.stock_code == affected_code
        ).first()
        
        if not stock:
            return None
        
        signal = strategy.generate_pair_signal({
            'affected_code': affected_code,
            'affected_company': stock.stock_name,
            'event_type': event_type,
            'severity': severity
        })
        
        if not signal:
            return None
        
        return {
            'signal_id': signal.signal_id,
            'long': {
                'code': signal.long_position.stock_code,
                'name': signal.long_position.stock_name,
                'weight': signal.long_position.weight
            } if signal.long_position else None,
            'short': {
                'code': signal.short_position.stock_code,
                'name': signal.short_position.stock_name,
                'weight': signal.short_position.weight
            } if signal.short_position else None,
            'decoupling_rate': signal.decoupling_rate,
            'expected_return': signal.expected_return,
            'confidence': signal.confidence,
            'is_executable': signal.is_executable,
            'notes': signal.execution_notes
        }
        
    finally:
        session.close()


def get_pair_trading_summary() -> Dict:
    """
    현재 활성화된 페어 트레이딩 기회 요약을 반환합니다.
    """
    strategy = PairTradingStrategy()
    active_signals = strategy.get_active_pair_signals()
    
    return {
        'total_signals': len(active_signals),
        'executable': sum(1 for s in active_signals if s['signal'].is_executable),
        'high_confidence': sum(1 for s in active_signals if s['signal'].confidence == 'HIGH'),
        'signals': [
            {
                'id': s['signal'].signal_id,
                'long': s['signal'].long_position.stock_name if s['signal'].long_position else None,
                'short': s['signal'].short_position.stock_name if s['signal'].short_position else None,
                'confidence': s['signal'].confidence,
                'expires_at': s['expires_at'].isoformat() if s['expires_at'] else None
            }
            for s in active_signals[:10]  # 최대 10개
        ]
    }

