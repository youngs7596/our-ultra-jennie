#!/usr/bin/env python3
"""
utilities/sync_portfolio_from_account.py
=========================================

KIS 계좌의 실제 보유 종목과 DB PORTFOLIO 테이블을 동기화합니다.

기능:
1. KIS Gateway에서 실제 계좌 보유 종목 조회
2. DB PORTFOLIO 테이블과 비교 (미스매치 리포트)
3. 최근 거래 이력 확인 (청산 여부 검증)
4. 사용자 확인 후 동기화:
   - DB에 없는 보유 종목 → 추가 (선택)
   - DB에 있지만 실제로는 청산된 종목 → 상태 변경 (HOLDING → SOLD)
   - 수량 불일치 → 수정

사용법:
    python utilities/sync_portfolio_from_account.py [--dry-run] [--auto-confirm]
    
옵션:
    --dry-run       실제 DB 변경 없이 리포트만 출력
    --auto-confirm  확인 없이 자동 적용 (주의!)
"""

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set

# 프로젝트 루트 추가
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# 환경변수 설정 (CLI 실행 시)
if not os.getenv('KIS_GATEWAY_URL'):
    os.environ['KIS_GATEWAY_URL'] = 'http://127.0.0.1:8080'
if not os.getenv('SECRETS_FILE'):
    os.environ['SECRETS_FILE'] = os.path.join(os.path.dirname(__file__), '..', 'secrets.json')

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from shared.db.connection import get_session, init_engine, ensure_engine_initialized
from shared.db.models import Portfolio, TradeLog
from shared.kis.gateway_client import KISGatewayClient

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# DB 엔진 초기화
ensure_engine_initialized()


def get_kis_holdings() -> List[Dict]:
    """
    KIS Gateway에서 실제 계좌 보유 종목 조회
    
    Returns:
        [{'code': str, 'name': str, 'quantity': int, 'avg_price': float, 'current_price': float}, ...]
    """
    logger.info("🔍 KIS Gateway에서 계좌 보유 종목 조회 중...")
    
    try:
        client = KISGatewayClient()
        holdings = client.get_account_balance()
        
        if holdings is None:
            logger.error("❌ KIS Gateway 계좌 조회 실패")
            return []
        
        logger.info(f"✅ KIS 계좌 보유 종목: {len(holdings)}개")
        return holdings
    except Exception as e:
        logger.error(f"❌ KIS Gateway 오류: {e}")
        return []


def get_db_portfolio(session: Session) -> List[Portfolio]:
    """
    DB에서 HOLDING 상태인 포트폴리오 조회
    """
    logger.info("🔍 DB에서 보유 중인 포트폴리오 조회 중...")
    
    holdings = session.query(Portfolio).filter(
        Portfolio.status == 'HOLDING'
    ).all()
    
    logger.info(f"✅ DB 포트폴리오: {len(holdings)}개")
    return holdings


def get_recent_trades(session: Session, days: int = 7) -> List[TradeLog]:
    """
    최근 N일 거래 이력 조회 (매도 이력 확인용)
    """
    cutoff_date = datetime.now() - timedelta(days=days)
    
    trades = session.query(TradeLog).filter(
        TradeLog.trade_timestamp >= cutoff_date
    ).order_by(TradeLog.trade_timestamp.desc()).all()
    
    logger.info(f"📋 최근 {days}일 거래 이력: {len(trades)}건")
    return trades


def compare_holdings(
    kis_holdings: List[Dict], 
    db_portfolio: List[Portfolio],
    recent_trades: List[TradeLog]
) -> Dict:
    """
    KIS 보유 종목과 DB 포트폴리오 비교
    
    Returns:
        {
            'only_in_kis': [...],      # KIS에만 있는 종목 (DB 추가 필요)
            'only_in_db': [...],       # DB에만 있는 종목 (청산된 것으로 추정)
            'quantity_mismatch': [...], # 수량 불일치
            'matched': [...]           # 일치하는 종목
        }
    """
    result = {
        'only_in_kis': [],
        'only_in_db': [],
        'quantity_mismatch': [],
        'matched': []
    }
    
    # KIS 보유 종목을 코드별로 매핑
    kis_map = {h['code']: h for h in kis_holdings}
    
    # DB 포트폴리오를 코드별로 매핑
    db_map = {p.stock_code: p for p in db_portfolio}
    
    # 최근 매도 이력을 코드별로 매핑
    sell_trades = {}
    for trade in recent_trades:
        if trade.trade_type == 'SELL':
            if trade.stock_code not in sell_trades:
                sell_trades[trade.stock_code] = []
            sell_trades[trade.stock_code].append(trade)
    
    kis_codes = set(kis_map.keys())
    db_codes = set(db_map.keys())
    
    # 1. KIS에만 있는 종목 (DB에 추가 필요할 수 있음)
    for code in kis_codes - db_codes:
        kis_item = kis_map[code]
        result['only_in_kis'].append({
            'code': code,
            'name': kis_item['name'],
            'quantity': kis_item['quantity'],
            'avg_price': kis_item['avg_price'],
            'current_price': kis_item['current_price']
        })
    
    # 2. DB에만 있는 종목 (청산된 것으로 추정)
    for code in db_codes - kis_codes:
        db_item = db_map[code]
        sell_history = sell_trades.get(code, [])
        result['only_in_db'].append({
            'code': code,
            'name': db_item.stock_name,
            'db_quantity': db_item.quantity,
            'db_avg_price': db_item.average_buy_price,
            'db_id': db_item.id,
            'sell_trades': len(sell_history),
            'last_sell': sell_history[0].trade_timestamp if sell_history else None
        })
    
    # 3. 양쪽에 있는 종목 비교
    for code in kis_codes & db_codes:
        kis_item = kis_map[code]
        db_item = db_map[code]
        
        if kis_item['quantity'] != db_item.quantity:
            result['quantity_mismatch'].append({
                'code': code,
                'name': kis_item['name'],
                'kis_quantity': kis_item['quantity'],
                'db_quantity': db_item.quantity,
                'db_id': db_item.id
            })
        else:
            result['matched'].append({
                'code': code,
                'name': kis_item['name'],
                'quantity': kis_item['quantity']
            })
    
    return result


def print_report(comparison: Dict):
    """미스매치 리포트 출력"""
    
    print("\n" + "=" * 70)
    print("📊 포트폴리오 동기화 리포트")
    print("=" * 70)
    
    # 일치하는 종목
    if comparison['matched']:
        print(f"\n✅ 일치하는 종목 ({len(comparison['matched'])}개):")
        for item in comparison['matched']:
            print(f"   - {item['code']} {item['name']}: {item['quantity']}주")
    
    # KIS에만 있는 종목
    if comparison['only_in_kis']:
        print(f"\n⚠️ KIS 계좌에만 있는 종목 (DB 추가 필요) ({len(comparison['only_in_kis'])}개):")
        for item in comparison['only_in_kis']:
            print(f"   - {item['code']} {item['name']}: {item['quantity']}주 @ {item['avg_price']:,.0f}원")
    
    # DB에만 있는 종목 (청산 추정)
    if comparison['only_in_db']:
        print(f"\n🚨 DB에만 있는 종목 (청산 추정) ({len(comparison['only_in_db'])}개):")
        for item in comparison['only_in_db']:
            sell_info = f", 매도 이력 {item['sell_trades']}건" if item['sell_trades'] > 0 else ""
            last_sell = f" (마지막 매도: {item['last_sell'].strftime('%Y-%m-%d %H:%M')})" if item['last_sell'] else ""
            print(f"   - {item['code']} {item['name']}: DB수량 {item['db_quantity']}주{sell_info}{last_sell}")
    
    # 수량 불일치
    if comparison['quantity_mismatch']:
        print(f"\n⚠️ 수량 불일치 ({len(comparison['quantity_mismatch'])}개):")
        for item in comparison['quantity_mismatch']:
            diff = item['kis_quantity'] - item['db_quantity']
            diff_str = f"+{diff}" if diff > 0 else str(diff)
            print(f"   - {item['code']} {item['name']}: KIS {item['kis_quantity']}주 vs DB {item['db_quantity']}주 ({diff_str})")
    
    print("\n" + "=" * 70)
    
    total_issues = len(comparison['only_in_kis']) + len(comparison['only_in_db']) + len(comparison['quantity_mismatch'])
    if total_issues == 0:
        print("✅ 모든 종목이 일치합니다!")
    else:
        print(f"⚠️ 총 {total_issues}개 불일치 발견")
    print("=" * 70 + "\n")


def apply_sync(session: Session, comparison: Dict, dry_run: bool = True):
    """
    동기화 적용
    
    Args:
        session: SQLAlchemy 세션
        comparison: 비교 결과
        dry_run: True면 실제 변경 없이 로그만 출력
    """
    changes_made = 0
    
    # 1. DB에만 있는 종목 → SOLD로 변경
    for item in comparison['only_in_db']:
        if dry_run:
            logger.info(f"[DRY RUN] {item['code']} {item['name']}: HOLDING → SOLD 변경 예정")
        else:
            portfolio = session.query(Portfolio).filter(Portfolio.id == item['db_id']).first()
            if portfolio:
                portfolio.status = 'SOLD'
                portfolio.sell_state = 'SYNCED_FROM_ACCOUNT'
                portfolio.updated_at = datetime.now()
                
                # 최근 매도 거래가 있으면 매도 가격 정보도 업데이트 가능 (선택)
                logger.info(f"✅ {item['code']} {item['name']}: HOLDING → SOLD 변경 완료")
                changes_made += 1
    
    # 2. 수량 불일치 → DB 수량 업데이트
    for item in comparison['quantity_mismatch']:
        if dry_run:
            logger.info(f"[DRY RUN] {item['code']} {item['name']}: 수량 {item['db_quantity']} → {item['kis_quantity']} 변경 예정")
        else:
            portfolio = session.query(Portfolio).filter(Portfolio.id == item['db_id']).first()
            if portfolio:
                old_quantity = portfolio.quantity
                portfolio.quantity = item['kis_quantity']
                portfolio.updated_at = datetime.now()
                logger.info(f"✅ {item['code']} {item['name']}: 수량 {old_quantity} → {item['kis_quantity']} 변경 완료")
                changes_made += 1
    
    # 3. KIS에만 있는 종목은 수동 추가 권장 (자동 추가는 위험)
    if comparison['only_in_kis']:
        logger.warning(f"⚠️ KIS에만 있는 {len(comparison['only_in_kis'])}개 종목은 수동 검토 후 추가하세요.")
        for item in comparison['only_in_kis']:
            logger.warning(f"   - {item['code']} {item['name']}: {item['quantity']}주 @ {item['avg_price']:,.0f}원")
    
    if not dry_run and changes_made > 0:
        try:
            session.commit()
            logger.info(f"✅ 총 {changes_made}개 항목 동기화 완료")
        except Exception as e:
            session.rollback()
            logger.error(f"❌ 동기화 실패: {e}")
            raise
    elif dry_run:
        logger.info(f"[DRY RUN] 총 {len(comparison['only_in_db']) + len(comparison['quantity_mismatch'])}개 항목 변경 예정")


def add_missing_holdings(session: Session, missing_items: List[Dict], dry_run: bool = True):
    """
    KIS에만 있고 DB에 없는 종목을 추가
    
    Args:
        session: SQLAlchemy 세션
        missing_items: only_in_kis 리스트
        dry_run: True면 실제 변경 없이 로그만 출력
    """
    added_count = 0
    
    for item in missing_items:
        if dry_run:
            logger.info(f"[DRY RUN] {item['code']} {item['name']}: 신규 추가 예정 ({item['quantity']}주 @ {item['avg_price']:,.0f}원)")
        else:
            new_portfolio = Portfolio(
                stock_code=item['code'],
                stock_name=item['name'],
                quantity=item['quantity'],
                average_buy_price=item['avg_price'],
                total_buy_amount=item['quantity'] * item['avg_price'],
                current_high_price=item['current_price'],
                status='HOLDING',
                sell_state='SYNCED_FROM_ACCOUNT',
                stop_loss_price=item['avg_price'] * 0.98  # 기본 손절 -2%
            )
            session.add(new_portfolio)
            logger.info(f"✅ {item['code']} {item['name']}: 신규 추가 완료 ({item['quantity']}주 @ {item['avg_price']:,.0f}원)")
            added_count += 1
    
    if not dry_run and added_count > 0:
        try:
            session.commit()
            logger.info(f"✅ 총 {added_count}개 종목 신규 추가 완료")
        except Exception as e:
            session.rollback()
            logger.error(f"❌ 종목 추가 실패: {e}")
            raise


def main():
    parser = argparse.ArgumentParser(description='KIS 계좌와 DB 포트폴리오 동기화')
    parser.add_argument('--dry-run', action='store_true', help='실제 변경 없이 리포트만 출력')
    parser.add_argument('--auto-confirm', action='store_true', help='확인 없이 자동 적용')
    parser.add_argument('--add-missing', action='store_true', help='KIS에만 있는 종목을 DB에 추가')
    parser.add_argument('--trade-days', type=int, default=7, help='거래 이력 조회 일수 (기본: 7일)')
    args = parser.parse_args()
    
    print("\n🔄 KIS 계좌 ↔ DB 포트폴리오 동기화 시작\n")
    
    # 1. KIS 계좌 보유 종목 조회
    kis_holdings = get_kis_holdings()
    if not kis_holdings:
        logger.warning("⚠️ KIS 계좌에 보유 종목이 없거나 조회 실패")
        # 계속 진행 (DB에만 있는 종목 정리 목적)
    
    # 2. DB 포트폴리오 조회
    with get_session() as session:
        db_portfolio = get_db_portfolio(session)
        
        if not db_portfolio and not kis_holdings:
            print("✅ KIS 계좌와 DB 모두 보유 종목이 없습니다.")
            return
        
        # 3. 최근 거래 이력 조회
        recent_trades = get_recent_trades(session, days=args.trade_days)
        
        # 4. 비교
        comparison = compare_holdings(kis_holdings, db_portfolio, recent_trades)
        
        # 5. 리포트 출력
        print_report(comparison)
        
        # 6. 동기화 적용
        sync_items = len(comparison['only_in_db']) + len(comparison['quantity_mismatch'])
        add_items = len(comparison['only_in_kis']) if args.add_missing else 0
        total_changes = sync_items + add_items
        
        if total_changes == 0:
            print("✅ 동기화할 항목이 없습니다.\n")
            return
        
        if args.dry_run:
            print("📋 DRY RUN 모드: 실제 변경 없이 예상 결과만 표시합니다.\n")
            apply_sync(session, comparison, dry_run=True)
            if args.add_missing and comparison['only_in_kis']:
                add_missing_holdings(session, comparison['only_in_kis'], dry_run=True)
        else:
            # 요약 메시지
            summary_parts = []
            if sync_items > 0:
                summary_parts.append(f"청산/수량 변경 {sync_items}개")
            if add_items > 0:
                summary_parts.append(f"신규 추가 {add_items}개")
            
            if args.auto_confirm:
                confirm = 'y'
            else:
                confirm = input(f"\n⚠️ {' + '.join(summary_parts)}를 적용하시겠습니까? (y/N): ").strip().lower()
            
            if confirm == 'y':
                apply_sync(session, comparison, dry_run=False)
                if args.add_missing and comparison['only_in_kis']:
                    add_missing_holdings(session, comparison['only_in_kis'], dry_run=False)
                print("\n✅ 동기화 완료!\n")
            else:
                print("\n❌ 동기화가 취소되었습니다.\n")


if __name__ == '__main__':
    main()

