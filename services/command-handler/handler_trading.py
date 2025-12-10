# services/command-handler/handler_trading.py
# Version: v1.0
# Manual Trading Handlers - 수동 매수/매도/청산 핸들러
#
# CommandHandler에서 분리된 수동 거래 관련 핸들러 함수들

import logging
from datetime import datetime
from typing import Optional

from shared.db.connection import session_scope
from shared.db import repository as repo
import shared.database as database
import shared.redis_cache as redis_cache
from shared.rabbitmq import RabbitMQPublisher

from limits import check_and_increment_manual_trade_limit

logger = logging.getLogger(__name__)


def handle_manual_buy(
    cmd: dict,
    dry_run: bool,
    kis,
    telegram_bot,
    buy_publisher: RabbitMQPublisher,
    manual_trade_daily_limit: int,
    resolve_stock_fn
) -> str:
    """
    수동 매수 핸들러
    
    Args:
        cmd: 명령 정보 (args, chat_id, username 등)
        dry_run: 시뮬레이션 모드 여부
        kis: KIS API 클라이언트
        telegram_bot: TelegramBot 인스턴스
        buy_publisher: buy-signals 큐 퍼블리셔
        manual_trade_daily_limit: 일일 수동 거래 제한
        resolve_stock_fn: 종목 코드 변환 함수
    
    Returns:
        응답 메시지
    """
    args = cmd.get('args', [])
    
    if not args:
        return """❓ *수동 매수 사용법*

`/buy 종목명 [수량]`

*예시:*
• `/buy 삼성전자 10` - 삼성전자 10주 매수
• `/buy 005930 5` - 종목코드로 5주 매수
• `/buy 카카오` - 자동 수량 계산"""
    
    stock_input = args[0]
    quantity = None
    
    if len(args) >= 2:
        try:
            quantity = int(args[1])
        except ValueError:
            return f"❓ 수량이 올바르지 않습니다: {args[1]}"
    
    try:
        # 일일 수동 거래 횟수 제한
        limit_error = check_and_increment_manual_trade_limit(cmd.get('chat_id'), manual_trade_daily_limit)
        if limit_error:
            return limit_error
        
        # 퍼블리셔 확인
        if not buy_publisher:
            return "❌ 매수 퍼블리셔가 초기화되지 않았습니다."
        
        # 1. 종목 코드 변환
        stock_code, stock_name = resolve_stock_fn(stock_input)
        if not stock_code:
            return f"❓ 종목을 찾을 수 없습니다: {stock_input}"
        
        # 2. 현재가 조회
        snapshot = kis.get_stock_snapshot(stock_code)
        if not snapshot:
            return f"❌ 현재가 조회 실패: {stock_name}"
        
        current_price = snapshot.get('price', 0)
        if current_price <= 0:
            return f"❌ 유효한 현재가가 없습니다: {stock_name}"
        
        # 3. 수량 자동 계산 (미지정 시)
        if quantity is None:
            try:
                cash = kis.get_cash_balance()
                invest_amount = cash * 0.2  # 기본 20%
                quantity = int(invest_amount / current_price)
                if quantity <= 0:
                    return f"❌ 잔고 부족으로 매수 불가\n\n가용 현금: {cash:,.0f}원\n현재가: {current_price:,.0f}원"
            except Exception as e:
                logger.error(f"잔고 조회 오류: {e}")
                return f"❌ 잔고 조회 실패: {e}"
        
        total_amount = current_price * quantity
        
        # 4. DRY_RUN 플래그
        effective_dry_run = dry_run or redis_cache.is_dryrun_enabled()
        
        # 5. 큐로 퍼블리시 (buy-executor가 포지션/리스크 검증 수행)
        payload = {
            "source": "telegram-manual",
            "market_regime": "MANUAL",
            "strategy_preset": {"name": "MANUAL_TELEGRAM", "params": {}},
            "risk_setting": {"position_size_ratio": 1.0},
            "manual_quantity": quantity,
            "dry_run": effective_dry_run,
            "user": cmd.get('username', 'unknown'),
            "requested_at": datetime.now().isoformat(),
            "candidates": [{
                "stock_code": stock_code,
                "stock_name": stock_name,
                "current_price": current_price,
                "llm_score": 100,
                "llm_reason": "[Telegram 수동매수] 사용자 입력",
                "buy_signal_type": "MANUAL_TELEGRAM",
                "factor_score": 100,
                "manual_quantity": quantity
            }]
        }
        
        msg_id = buy_publisher.publish(payload)
        if not msg_id:
            return "❌ 매수 요청 발행 실패 (RabbitMQ)"
        
        dry_run_suffix = "\n⚠️ DRY_RUN 모드: 실행 서비스에서 시뮬레이션 처리" if effective_dry_run else ""
        return f"""📨 매수 요청을 접수했습니다.

📈 {stock_name} ({stock_code})
🛒 수량: {quantity}주
💰 예상 금액: {total_amount:,.0f}원
🧾 메시지 ID: {msg_id}{dry_run_suffix}"""
        
    except Exception as e:
        logger.error(f"수동 매수 오류: {e}", exc_info=True)
        return f"❌ 매수 처리 중 오류 발생: {e}"


def handle_manual_sell(
    cmd: dict,
    dry_run: bool,
    kis,
    telegram_bot,
    sell_publisher: RabbitMQPublisher,
    manual_trade_daily_limit: int,
    resolve_stock_fn
) -> str:
    """
    수동 매도 핸들러
    
    Args:
        cmd: 명령 정보
        dry_run: 시뮬레이션 모드 여부
        kis: KIS API 클라이언트
        telegram_bot: TelegramBot 인스턴스
        sell_publisher: sell-orders 큐 퍼블리셔
        manual_trade_daily_limit: 일일 수동 거래 제한
        resolve_stock_fn: 종목 코드 변환 함수
    
    Returns:
        응답 메시지
    """
    args = cmd.get('args', [])
    
    if not args:
        return """❓ *수동 매도 사용법*

`/sell 종목명 [수량]`

*예시:*
• `/sell 삼성전자 10` - 삼성전자 10주 매도
• `/sell 005930 전량` - 전량 매도
• `/sell 카카오` - 전량 매도 (기본)"""
    
    stock_input = args[0]
    quantity = None
    sell_all = False
    
    if len(args) >= 2:
        if args[1] in ['전량', 'all', '모두']:
            sell_all = True
        else:
            try:
                quantity = int(args[1])
            except ValueError:
                return f"❓ 수량이 올바르지 않습니다: {args[1]}"
    else:
        sell_all = True  # 수량 미지정 시 전량 매도
    
    try:
        # 일일 수동 거래 횟수 제한
        limit_error = check_and_increment_manual_trade_limit(cmd.get('chat_id'), manual_trade_daily_limit)
        if limit_error:
            return limit_error
        
        if not sell_publisher:
            return "❌ 매도 퍼블리셔가 초기화되지 않았습니다."
        
        # 1. 종목 코드 변환
        stock_code, stock_name = resolve_stock_fn(stock_input) # type: ignore
        if not stock_code: # type: ignore
            return f"❓ 종목을 찾을 수 없습니다: {stock_input}"
        
        # 2. 보유 수량 조회
        with session_scope(readonly=True) as session: # type: ignore
            portfolio = repo.get_active_portfolio(session)
        
        holding = None
        for p in portfolio:
            p_code = p.get('stock_code') or p.get('code')
            if p_code == stock_code:
                holding = p
                break
        
        if not holding:
            return f"❌ 보유하지 않은 종목입니다: {stock_name}"
        
        holding_qty = int(holding.get('quantity', 0))
        buy_price = float(holding.get('avg_price', 0))
        
        if sell_all or quantity is None:
            quantity = holding_qty
        
        if quantity > holding_qty:
            return f"❌ 보유 수량 초과\n\n보유: {holding_qty}주\n요청: {quantity}주"
        
        # 3. 현재가 조회
        snapshot = kis.get_stock_snapshot(stock_code) # type: ignore
        if not snapshot:
            return f"❌ 현재가 조회 실패: {stock_name}"
        
        current_price = snapshot.get('price', 0)
        if current_price <= 0:
            return f"❌ 유효한 현재가가 없습니다: {stock_name}"
        
        total_amount = current_price * quantity
        profit = (current_price - buy_price) * quantity
        profit_pct = ((current_price - buy_price) / buy_price * 100) if buy_price > 0 else 0
        profit_emoji = "📈" if profit >= 0 else "📉"
        
        effective_dry_run = dry_run or redis_cache.is_dryrun_enabled() # type: ignore
        
        payload = {
            "source": "telegram-manual",
            "stock_code": stock_code,
            "stock_name": stock_name,
            "quantity": quantity,
            "sell_reason": "MANUAL_SELL",
            "requested_by": cmd.get('username', 'unknown'),
            "requested_at": datetime.now().isoformat(),
            "dry_run": effective_dry_run
        }
        
        msg_id = sell_publisher.publish(payload) # type: ignore
        if not msg_id:
            return "❌ 매도 요청 발행 실패 (RabbitMQ)"
        
        dry_run_suffix = "\n⚠️ DRY_RUN 모드: 실행 서비스에서 시뮬레이션 처리" if effective_dry_run else ""
        return f"""📨 매도 요청을 접수했습니다.

📉 {stock_name} ({stock_code})
🛒 수량: {quantity}주 / 보유 {holding_qty}주
💰 예상 금액: {total_amount:,.0f}원
{profit_emoji} 예상 손익: {profit:+,.0f}원 ({profit_pct:+.2f}%)
🧾 메시지 ID: {msg_id}{dry_run_suffix}"""
        
    except Exception as e:
        logger.error(f"수동 매도 오류: {e}", exc_info=True)
        return f"❌ 매도 처리 중 오류 발생: {e}"


def handle_sellall(
    cmd: dict,
    dry_run: bool,
    sell_publisher: RabbitMQPublisher
) -> str:
    """
    전체 청산 핸들러
    
    Args:
        cmd: 명령 정보
        dry_run: 시뮬레이션 모드 여부
        sell_publisher: sell-orders 큐 퍼블리셔
    
    Returns:
        응답 메시지
    """
    args = cmd.get('args', [])
    
    # 확인 키워드 필요
    if not args or args[0] != '확인':
        # 현재 포트폴리오 미리보기
        try: # type: ignore
            with session_scope(readonly=True) as session: # type: ignore
                portfolio = repo.get_active_portfolio(session)
            
            if not portfolio:
                return "📭 청산할 보유 종목이 없습니다."
            
            lines = [f"⚠️ *전체 청산 확인*\n"]
            lines.append(f"총 {len(portfolio)}개 종목이 청산됩니다:\n")
            
            for p in portfolio[:5]:  # 최대 5개만 표시
                name = p.get('stock_name') or p.get('name', 'Unknown')
                qty = p.get('quantity', 0)
                lines.append(f"• {name}: {qty}주")
            
            if len(portfolio) > 5:
                lines.append(f"... 외 {len(portfolio) - 5}개")
            
            lines.append(f"\n확인하려면 `/sellall 확인`을 입력하세요.")
            
            return '\n'.join(lines)
            
        except Exception as e:
            return f"❌ 포트폴리오 조회 실패: {e}"
    
    # 실제 청산 실행
    effective_dry_run = dry_run or redis_cache.is_dryrun_enabled() # type: ignore
    
    try:
        if not sell_publisher:
            return "❌ 매도 퍼블리셔가 초기화되지 않았습니다."
        
        with session_scope(readonly=True) as session: # type: ignore
            portfolio = repo.get_active_portfolio(session)
        
        if not portfolio:
            return "📭 청산할 보유 종목이 없습니다."
        
        results = []
        success_count = 0
        fail_count = 0
        
        for p in portfolio:
            stock_code = p.get('code')
            stock_name = p.get('name', stock_code)
            quantity = int(p.get('quantity', 0))
            
            if quantity <= 0:
                continue
            
            payload = {
                "source": "telegram-manual",
                "stock_code": stock_code,
                "stock_name": stock_name,
                "quantity": quantity,
                "sell_reason": "MANUAL_SELLALL",
                "requested_by": cmd.get('username', 'unknown'),
                "requested_at": datetime.now().isoformat(),
                "dry_run": effective_dry_run
            }
            
            msg_id = sell_publisher.publish(payload) # type: ignore
            if msg_id:
                results.append(f"✅ {stock_name}: {quantity}주 (msg: {msg_id})")
                success_count += 1
            else:
                results.append(f"❌ {stock_name}: 발행 실패")
                fail_count += 1
        
        mode_prefix = "[DRY_RUN] " if effective_dry_run else ""
        
        return f"""🛑 *{mode_prefix}전체 청산 요청을 접수했습니다.*

✅ 발행 성공: {success_count}건
❌ 발행 실패: {fail_count}건

*결과(최대 10개):*
""" + '\n'.join(results[:10])
        
    except Exception as e:
        logger.error(f"전체 청산 오류: {e}", exc_info=True)
        return f"❌ 전체 청산 중 오류 발생: {e}"
