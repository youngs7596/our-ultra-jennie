# services/command-handler/handler.py
# Version: v3.6
# Command Handler - Telegram 명령 처리 로직

import time
import logging
import sys
import os
from datetime import datetime, timezone

# shared 패키지 임포트
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import shared.database as database
import shared.redis_cache as redis_cache
from shared.notification import TelegramBot

logger = logging.getLogger(__name__)


class CommandHandler:
    """Telegram 명령 처리 클래스"""
    
    def __init__(self, kis, config, telegram_bot: TelegramBot = None):
        """
        Args:
            kis: KIS API 클라이언트
            config: ConfigManager 인스턴스
            telegram_bot: TelegramBot 인스턴스
        """
        self.kis = kis
        self.config = config
        self.telegram_bot = telegram_bot
        
        # 명령어별 핸들러 매핑
        self.command_handlers = {
            # 매매 제어
            'pause': self._handle_pause,
            'resume': self._handle_resume,
            'stop': self._handle_stop,
            'dryrun': self._handle_dryrun,
            # 수동 매매
            'buy': self._handle_manual_buy,
            'sell': self._handle_manual_sell,
            'sellall': self._handle_sellall,
            # 조회
            'status': self._handle_status,
            'portfolio': self._handle_portfolio,
            'pnl': self._handle_pnl,
            'balance': self._handle_balance,
            'price': self._handle_price,
            # 관심종목
            'watch': self._handle_watch,
            'unwatch': self._handle_unwatch,
            'watchlist': self._handle_watchlist,
            # 알림 제어
            'mute': self._handle_mute,
            'unmute': self._handle_unmute,
            'alert': self._handle_alert,
            'alerts': self._handle_alerts,
            # 설정
            'risk': self._handle_risk,
            'minscore': self._handle_minscore,
            'maxbuy': self._handle_maxbuy,
            'config': self._handle_config,
            # 도움말
            'help': self._handle_help,
        }
    
    def poll_and_process(self, dry_run: bool = True) -> dict:
        """
        Telegram에서 명령을 폴링하고 처리합니다.
        
        Args:
            dry_run: True면 매수/매도 시 로그만 기록
        
        Returns:
            {'status': 'success', 'processed_count': 2, 'failed_count': 0}
        """
        processed_count = 0
        failed_count = 0
        
        if not self.telegram_bot:
            logger.warning("⚠️ Telegram Bot이 설정되지 않았습니다.")
            return {
                "status": "error",
                "error": "Telegram Bot not configured",
                "processed_count": 0,
                "failed_count": 0
            }
        
        try:
            # Telegram에서 명령 가져오기
            commands = self.telegram_bot.get_pending_commands(timeout=1)
            
            if not commands:
                return {
                    "status": "success",
                    "processed_count": 0,
                    "failed_count": 0,
                    "message": "No pending commands"
                }
            
            logger.info(f"📩 {len(commands)}개 명령 수신")
            
            # 명령 처리
            for cmd in commands:
                try:
                    self._process_command(cmd, dry_run=dry_run)
                    processed_count += 1
                except Exception as e:
                    failed_count += 1
                    logger.error(f"❌ 명령 처리 실패: {cmd.get('command')} - {e}")
                    
                    # 에러 응답 전송
                    self.telegram_bot.reply(
                        cmd.get('chat_id'),
                        f"❌ 명령 처리 실패: {str(e)}"
                    )
            
            return {
                "status": "success",
                "processed_count": processed_count,
                "failed_count": failed_count,
                "total_commands": len(commands)
            }
            
        except Exception as e:
            logger.error(f"❌ 명령 폴링 중 오류: {e}", exc_info=True)
            return {
                "status": "error",
                "error": str(e),
                "processed_count": processed_count,
                "failed_count": failed_count
            }
    
    def _process_command(self, cmd: dict, dry_run: bool):
        """명령 처리"""
        command = cmd.get('command')
        args = cmd.get('args', [])
        chat_id = cmd.get('chat_id')
        username = cmd.get('username', 'unknown')
        
        logger.info(f"🔧 명령 처리 중: /{command} {' '.join(args)} (from @{username})")
        
        handler = self.command_handlers.get(command)
        if not handler:
            self.telegram_bot.reply(chat_id, f"❓ 알 수 없는 명령어: /{command}\n/help 로 도움말을 확인하세요.")
            return
        
        # 핸들러 호출
        result = handler(cmd, dry_run=dry_run)
        
        # 응답 전송 (핸들러에서 직접 응답하지 않은 경우)
        if result:
            self.telegram_bot.reply(chat_id, result)
    
    # ============================================================================
    # 매매 제어 명령어 핸들러
    # ============================================================================
    
    def _handle_pause(self, cmd: dict, dry_run: bool) -> str:
        """매수 일시 중지"""
        args = cmd.get('args', [])
        reason = ' '.join(args) if args else '사용자 요청'
        
        redis_cache.set_trading_flag('pause', True, reason=reason)
        
        return f"⏸️ 매수가 중지되었습니다.\n\n📝 사유: {reason}\n\n/resume 으로 재개할 수 있습니다."
    
    def _handle_resume(self, cmd: dict, dry_run: bool) -> str:
        """매수 재개"""
        redis_cache.set_trading_flag('pause', False, reason='사용자 요청')
        
        return "▶️ 매수가 재개되었습니다.\n\n자동 매수가 다시 활성화됩니다."
    
    def _handle_stop(self, cmd: dict, dry_run: bool) -> str:
        """긴급 전체 중지"""
        args = cmd.get('args', [])
        
        # 확인 키워드 필요
        if not args or args[0] != '확인':
            return "⚠️ 긴급 중지 명령입니다.\n\n모든 매수/매도가 중단됩니다.\n확인하려면 `/stop 확인`을 입력하세요."
        
        redis_cache.set_trading_flag('stop', True, reason='긴급 중지')
        redis_cache.set_trading_flag('pause', True, reason='긴급 중지')
        
        return "🛑 *긴급 중지 완료*\n\n모든 자동 거래가 중단되었습니다.\n\n재개하려면 `/resume`을 입력하세요."
    
    def _handle_dryrun(self, cmd: dict, dry_run: bool) -> str:
        """DRY_RUN 모드 전환"""
        args = cmd.get('args', [])
        
        if not args:
            # 현재 상태 조회
            is_dryrun = redis_cache.is_dryrun_enabled()
            status = "ON ✅" if is_dryrun else "OFF ⭕"
            return f"🔧 DRY\\_RUN 모드: {status}\n\n변경하려면 `/dryrun on` 또는 `/dryrun off`"
        
        value = args[0].lower()
        if value in ['on', 'true', '1']:
            redis_cache.set_trading_flag('dryrun', True, reason='사용자 설정')
            return "🔧 DRY\\_RUN 모드: ON ✅\n\n실제 주문이 실행되지 않습니다."
        elif value in ['off', 'false', '0']:
            redis_cache.set_trading_flag('dryrun', False, reason='사용자 설정')
            return "🔧 DRY\\_RUN 모드: OFF ⭕\n\n⚠️ 실제 주문이 실행됩니다!"
        else:
            return "❓ 사용법: `/dryrun on` 또는 `/dryrun off`"
    
    # ============================================================================
    # 조회 명령어 핸들러
    # ============================================================================
    
    def _handle_status(self, cmd: dict, dry_run: bool) -> str:
        """시스템 상태 확인"""
        flags = redis_cache.get_all_trading_flags()
        
        pause_status = "⏸️ 중지" if flags['pause'].get('value') else "▶️ 활성"
        stop_status = "🛑 긴급중지" if flags['stop'].get('value') else "✅ 정상"
        dryrun_status = "🔧 ON (테스트)" if redis_cache.is_dryrun_enabled() else "💰 OFF (실거래)"
        
        trading_mode = os.getenv("TRADING_MODE", "MOCK")
        mode_emoji = "🧪" if trading_mode == "MOCK" else "💹"
        
        return f"""📊 *시스템 상태*

{mode_emoji} 거래 모드: {trading_mode}
{pause_status} 매수 상태
{stop_status} 시스템 상태
{dryrun_status} DRY\\_RUN

⏰ 현재 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
    
    def _handle_portfolio(self, cmd: dict, dry_run: bool) -> str:
        """현재 포트폴리오 조회"""
        try:
            with database.get_db_connection_context() as db_conn:
                portfolio = database.get_active_portfolio(db_conn)
            
            if not portfolio:
                return "📭 현재 보유 종목이 없습니다."
            
            lines = [f"📊 *현재 포트폴리오* ({len(portfolio)}종목)\n"]
            
            total_value = 0
            total_profit = 0
            
            for i, p in enumerate(portfolio, 1):
                code = p.get('stock_code') or p.get('code')
                name = p.get('stock_name') or p.get('name', code)
                qty = p.get('quantity', 0)
                buy_price = p.get('buy_price', 0)
                current_price = p.get('current_price', buy_price)
                
                profit_pct = ((current_price - buy_price) / buy_price * 100) if buy_price > 0 else 0
                profit_emoji = "📈" if profit_pct >= 0 else "📉"
                
                value = qty * current_price
                total_value += value
                total_profit += (current_price - buy_price) * qty
                
                lines.append(f"{i}. {name} ({code})")
                lines.append(f"   {qty}주 | 평단: {buy_price:,.0f}원")
                lines.append(f"   현재: {current_price:,.0f}원 | {profit_emoji} {profit_pct:+.2f}%\n")
            
            total_profit_pct = (total_profit / (total_value - total_profit) * 100) if total_value > total_profit else 0
            profit_emoji = "📈" if total_profit >= 0 else "📉"
            
            lines.append(f"💰 총 평가금액: {total_value:,.0f}원")
            lines.append(f"{profit_emoji} 총 수익: {total_profit:+,.0f}원 ({total_profit_pct:+.2f}%)")
            
            return '\n'.join(lines)
            
        except Exception as e:
            logger.error(f"포트폴리오 조회 오류: {e}")
            return f"❌ 포트폴리오 조회 실패: {e}"
    
    def _handle_pnl(self, cmd: dict, dry_run: bool) -> str:
        """오늘 손익 현황"""
        try:
            with database.get_db_connection_context() as db_conn:
                today_trades = database.get_today_trades(db_conn)
            
            if not today_trades:
                return "📊 오늘 체결된 거래가 없습니다."
            
            buy_count = sum(1 for t in today_trades if t.get('trade_type') == 'BUY')
            sell_count = sum(1 for t in today_trades if 'SELL' in t.get('trade_type', ''))
            
            # 실현 손익 계산 (매도 거래에서)
            realized_pnl = sum(t.get('profit_amount', 0) or 0 for t in today_trades if 'SELL' in t.get('trade_type', ''))
            
            profit_emoji = "📈" if realized_pnl >= 0 else "📉"
            
            return f"""📊 *오늘의 거래 현황*

💵 매수: {buy_count}건
💰 매도: {sell_count}건
{profit_emoji} 실현 손익: {realized_pnl:+,.0f}원

⏰ 기준: {datetime.now().strftime('%Y-%m-%d')}"""
            
        except Exception as e:
            logger.error(f"PnL 조회 오류: {e}")
            return f"❌ 손익 조회 실패: {e}"
    
    def _handle_balance(self, cmd: dict, dry_run: bool) -> str:
        """계좌 잔고 조회"""
        try:
            cash = self.kis.get_cash_balance()
            return f"💰 *계좌 잔고*\n\n가용 현금: {cash:,.0f}원"
        except Exception as e:
            logger.error(f"잔고 조회 오류: {e}")
            return f"❌ 잔고 조회 실패: {e}"
    
    def _handle_price(self, cmd: dict, dry_run: bool) -> str:
        """현재가 조회"""
        args = cmd.get('args', [])
        if not args:
            return "❓ 사용법: `/price 삼성전자` 또는 `/price 005930`"
        
        stock_input = args[0]
        
        try:
            # 종목 코드 변환
            stock_code, stock_name = self._resolve_stock(stock_input)
            if not stock_code:
                return f"❓ 종목을 찾을 수 없습니다: {stock_input}"
            
            snapshot = self.kis.get_stock_snapshot(stock_code)
            if not snapshot:
                return f"❌ 가격 조회 실패: {stock_name}"
            
            price = snapshot.get('price', 0)
            open_price = snapshot.get('open', 0)
            high = snapshot.get('high', 0)
            low = snapshot.get('low', 0)
            
            change_pct = ((price - open_price) / open_price * 100) if open_price > 0 else 0
            change_emoji = "📈" if change_pct >= 0 else "📉"
            
            return f"""📊 *{stock_name}* ({stock_code})

💵 현재가: {price:,.0f}원
{change_emoji} 등락률: {change_pct:+.2f}%

⬆️ 고가: {high:,.0f}원
⬇️ 저가: {low:,.0f}원"""
            
        except Exception as e:
            logger.error(f"가격 조회 오류: {e}")
            return f"❌ 가격 조회 실패: {e}"
    
    # ============================================================================
    # 수동 매매 핸들러
    # ============================================================================
    
    def _handle_manual_buy(self, cmd: dict, dry_run: bool) -> str:
        """수동 매수"""
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
            # 1. 종목 코드 변환
            stock_code, stock_name = self._resolve_stock(stock_input)
            if not stock_code:
                return f"❓ 종목을 찾을 수 없습니다: {stock_input}"
            
            # 2. 현재가 조회
            snapshot = self.kis.get_stock_snapshot(stock_code)
            if not snapshot:
                return f"❌ 현재가 조회 실패: {stock_name}"
            
            current_price = snapshot.get('price', 0)
            if current_price <= 0:
                return f"❌ 유효한 현재가가 없습니다: {stock_name}"
            
            # 3. 수량 자동 계산 (미지정 시)
            if quantity is None:
                try:
                    cash = self.kis.get_cash_balance()
                    # 기본: 가용 현금의 20%로 매수 (최대 5% 비중)
                    invest_amount = min(cash * 0.2, cash * 0.05)
                    quantity = int(invest_amount / current_price)
                    if quantity <= 0:
                        return f"❌ 잔고 부족으로 매수 불가\n\n가용 현금: {cash:,.0f}원\n현재가: {current_price:,.0f}원"
                except Exception as e:
                    logger.error(f"잔고 조회 오류: {e}")
                    return f"❌ 잔고 조회 실패: {e}"
            
            total_amount = current_price * quantity
            
            # 4. DRY_RUN 또는 dry_run 모드 체크
            effective_dry_run = dry_run or redis_cache.is_dryrun_enabled()
            
            if effective_dry_run:
                return f"""🔧 *[DRY\\_RUN] 수동 매수 시뮬레이션*

📈 {stock_name} ({stock_code})
📊 현재가: {current_price:,.0f}원
🛒 주문 수량: {quantity}주
💰 예상 금액: {total_amount:,.0f}원

⚠️ 실제 주문은 실행되지 않았습니다.
실거래를 원하면 `/dryrun off` 후 재시도하세요."""
            
            # 5. 실제 매수 주문
            logger.info(f"💰 수동 매수 주문: {stock_name} ({stock_code}) {quantity}주 @ {current_price:,.0f}원")
            
            order_result = self.kis.place_buy_order(stock_code, quantity, current_price)
            
            if order_result and order_result.get('order_no'):
                order_no = order_result['order_no']
                
                # 거래 로그 기록
                try:
                    with database.get_db_connection_context() as db_conn:
                        database.record_trade(
                            db_conn,
                            stock_code=stock_code,
                            trade_type='BUY',
                            quantity=quantity,
                            price=current_price,
                            reason=f"[Telegram 수동매수] /buy {stock_input}"
                        )
                except Exception as e:
                    logger.warning(f"거래 로그 기록 실패: {e}")
                
                return f"""✅ *수동 매수 주문 완료*

📈 {stock_name} ({stock_code})
📊 주문가: {current_price:,.0f}원
🛒 수량: {quantity}주
💰 금액: {total_amount:,.0f}원
🔖 주문번호: {order_no}

⏳ 체결 확인은 잠시 후 `/portfolio` 로 확인하세요."""
            else:
                error_msg = order_result.get('error', 'Unknown error') if order_result else 'No response'
                return f"❌ 매수 주문 실패: {error_msg}"
            
        except Exception as e:
            logger.error(f"수동 매수 오류: {e}", exc_info=True)
            return f"❌ 매수 처리 중 오류 발생: {e}"
    
    def _handle_manual_sell(self, cmd: dict, dry_run: bool) -> str:
        """수동 매도"""
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
            # 1. 종목 코드 변환
            stock_code, stock_name = self._resolve_stock(stock_input)
            if not stock_code:
                return f"❓ 종목을 찾을 수 없습니다: {stock_input}"
            
            # 2. 보유 수량 조회
            with database.get_db_connection_context() as db_conn:
                portfolio = database.get_active_portfolio(db_conn)
            
            holding = None
            for p in portfolio:
                p_code = p.get('stock_code') or p.get('code')
                if p_code == stock_code:
                    holding = p
                    break
            
            if not holding:
                return f"❌ 보유하지 않은 종목입니다: {stock_name}"
            
            holding_qty = holding.get('quantity', 0)
            buy_price = holding.get('buy_price', 0)
            
            if sell_all or quantity is None:
                quantity = holding_qty
            
            if quantity > holding_qty:
                return f"❌ 보유 수량 초과\n\n보유: {holding_qty}주\n요청: {quantity}주"
            
            # 3. 현재가 조회
            snapshot = self.kis.get_stock_snapshot(stock_code)
            if not snapshot:
                return f"❌ 현재가 조회 실패: {stock_name}"
            
            current_price = snapshot.get('price', 0)
            if current_price <= 0:
                return f"❌ 유효한 현재가가 없습니다: {stock_name}"
            
            total_amount = current_price * quantity
            profit = (current_price - buy_price) * quantity
            profit_pct = ((current_price - buy_price) / buy_price * 100) if buy_price > 0 else 0
            profit_emoji = "📈" if profit >= 0 else "📉"
            
            # 4. DRY_RUN 체크
            effective_dry_run = dry_run or redis_cache.is_dryrun_enabled()
            
            if effective_dry_run:
                return f"""🔧 *[DRY\\_RUN] 수동 매도 시뮬레이션*

📉 {stock_name} ({stock_code})
📊 현재가: {current_price:,.0f}원
🛒 매도 수량: {quantity}주 / {holding_qty}주
💰 예상 금액: {total_amount:,.0f}원
{profit_emoji} 예상 손익: {profit:+,.0f}원 ({profit_pct:+.2f}%)

⚠️ 실제 주문은 실행되지 않았습니다."""
            
            # 5. 실제 매도 주문
            logger.info(f"💵 수동 매도 주문: {stock_name} ({stock_code}) {quantity}주 @ {current_price:,.0f}원")
            
            order_result = self.kis.place_sell_order(stock_code, quantity, current_price)
            
            if order_result and order_result.get('order_no'):
                order_no = order_result['order_no']
                
                # 거래 로그 기록
                try:
                    with database.get_db_connection_context() as db_conn:
                        database.record_trade(
                            db_conn,
                            stock_code=stock_code,
                            trade_type='SELL',
                            quantity=quantity,
                            price=current_price,
                            reason=f"[Telegram 수동매도] /sell {stock_input}"
                        )
                except Exception as e:
                    logger.warning(f"거래 로그 기록 실패: {e}")
                
                return f"""✅ *수동 매도 주문 완료*

📉 {stock_name} ({stock_code})
📊 주문가: {current_price:,.0f}원
🛒 수량: {quantity}주
💰 금액: {total_amount:,.0f}원
{profit_emoji} 예상 손익: {profit:+,.0f}원 ({profit_pct:+.2f}%)
🔖 주문번호: {order_no}"""
            else:
                error_msg = order_result.get('error', 'Unknown error') if order_result else 'No response'
                return f"❌ 매도 주문 실패: {error_msg}"
            
        except Exception as e:
            logger.error(f"수동 매도 오류: {e}", exc_info=True)
            return f"❌ 매도 처리 중 오류 발생: {e}"
    
    def _handle_sellall(self, cmd: dict, dry_run: bool) -> str:
        """전체 청산"""
        args = cmd.get('args', [])
        
        # 확인 키워드 필요
        if not args or args[0] != '확인':
            # 현재 포트폴리오 미리보기
            try:
                with database.get_db_connection_context() as db_conn:
                    portfolio = database.get_active_portfolio(db_conn)
                
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
        effective_dry_run = dry_run or redis_cache.is_dryrun_enabled()
        
        try:
            with database.get_db_connection_context() as db_conn:
                portfolio = database.get_active_portfolio(db_conn)
            
            if not portfolio:
                return "📭 청산할 보유 종목이 없습니다."
            
            results = []
            success_count = 0
            fail_count = 0
            
            for p in portfolio:
                stock_code = p.get('stock_code') or p.get('code')
                stock_name = p.get('stock_name') or p.get('name', stock_code)
                quantity = p.get('quantity', 0)
                
                if quantity <= 0:
                    continue
                
                try:
                    snapshot = self.kis.get_stock_snapshot(stock_code)
                    current_price = snapshot.get('price', 0) if snapshot else 0
                    
                    if effective_dry_run:
                        results.append(f"🔧 {stock_name}: {quantity}주 @ {current_price:,.0f}원")
                        success_count += 1
                    else:
                        order_result = self.kis.place_sell_order(stock_code, quantity, current_price)
                        if order_result and order_result.get('order_no'):
                            results.append(f"✅ {stock_name}: {quantity}주")
                            success_count += 1
                        else:
                            results.append(f"❌ {stock_name}: 주문 실패")
                            fail_count += 1
                            
                except Exception as e:
                    results.append(f"❌ {stock_name}: {e}")
                    fail_count += 1
            
            mode_prefix = "[DRY\\_RUN] " if effective_dry_run else ""
            
            return f"""🛑 *{mode_prefix}전체 청산 완료*

✅ 성공: {success_count}건
❌ 실패: {fail_count}건

*결과:*
""" + '\n'.join(results[:10])  # 최대 10개만 표시
            
        except Exception as e:
            logger.error(f"전체 청산 오류: {e}", exc_info=True)
            return f"❌ 전체 청산 중 오류 발생: {e}"

    # ============================================================================
    # 관심종목 핸들러
    # ============================================================================
    
    def _handle_watch(self, cmd: dict, dry_run: bool) -> str:
        """관심종목 추가"""
        args = cmd.get('args', [])
        
        if not args:
            return """❓ *관심종목 추가 사용법*

`/watch 종목명`

*예시:*
• `/watch 삼성전자`
• `/watch 005930`"""
        
        stock_input = args[0]
        
        try:
            # 1. 종목 코드 변환
            stock_code, stock_name = self._resolve_stock(stock_input)
            if not stock_code:
                return f"❓ 종목을 찾을 수 없습니다: {stock_input}"
            
            # 2. 이미 관심종목인지 확인
            with database.get_db_connection_context() as db_conn:
                watchlist = database.get_active_watchlist(db_conn)
            
            if stock_code in watchlist:
                return f"ℹ️ {stock_name}은(는) 이미 관심종목입니다."
            
            # 3. 관심종목 추가
            candidate = {
                'code': stock_code,
                'name': stock_name,
                'is_tradable': True,
                'llm_score': 50,  # 기본 점수
                'llm_reason': '[Telegram /watch 명령으로 수동 추가]'
            }
            
            with database.get_db_connection_context() as db_conn:
                database.save_to_watchlist(db_conn, [candidate])
            
            return f"✅ 관심종목에 추가되었습니다.\n\n📌 {stock_name} ({stock_code})"
            
        except Exception as e:
            logger.error(f"관심종목 추가 오류: {e}", exc_info=True)
            return f"❌ 관심종목 추가 실패: {e}"
    
    def _handle_unwatch(self, cmd: dict, dry_run: bool) -> str:
        """관심종목 제거"""
        args = cmd.get('args', [])
        
        if not args:
            return """❓ *관심종목 제거 사용법*

`/unwatch 종목명`

*예시:*
• `/unwatch 삼성전자`
• `/unwatch 005930`"""
        
        stock_input = args[0]
        
        try:
            # 1. 종목 코드 변환
            stock_code, stock_name = self._resolve_stock(stock_input)
            if not stock_code:
                return f"❓ 종목을 찾을 수 없습니다: {stock_input}"
            
            # 2. 관심종목에서 제거
            with database.get_db_connection_context() as db_conn:
                cursor = db_conn.cursor()
                cursor.execute("DELETE FROM WatchList WHERE STOCK_CODE = %s", [stock_code])
                deleted = cursor.rowcount
                db_conn.commit()
                cursor.close()
            
            if deleted > 0:
                return f"✅ 관심종목에서 제거되었습니다.\n\n🗑️ {stock_name} ({stock_code})"
            else:
                return f"ℹ️ {stock_name}은(는) 관심종목에 없습니다."
            
        except Exception as e:
            logger.error(f"관심종목 제거 오류: {e}", exc_info=True)
            return f"❌ 관심종목 제거 실패: {e}"
    
    def _handle_watchlist(self, cmd: dict, dry_run: bool) -> str:
        """관심종목 조회"""
        try:
            with database.get_db_connection_context() as db_conn:
                watchlist = database.get_active_watchlist(db_conn)
            
            if not watchlist:
                return "📭 관심종목이 없습니다.\n\n`/watch 종목명`으로 추가하세요."
            
            lines = [f"📌 *관심종목* ({len(watchlist)}종목)\n"]
            
            # LLM 점수 순으로 정렬
            sorted_items = sorted(
                watchlist.items(),
                key=lambda x: x[1].get('llm_score', 0),
                reverse=True
            )
            
            for i, (code, info) in enumerate(sorted_items[:15], 1):  # 최대 15개
                name = info.get('name', code)
                score = info.get('llm_score', 0)
                tradable = "✅" if info.get('is_tradable', True) else "⏸️"
                
                # 점수에 따른 이모지
                if score >= 80:
                    score_emoji = "🔥"
                elif score >= 60:
                    score_emoji = "📈"
                elif score >= 40:
                    score_emoji = "➖"
                else:
                    score_emoji = "📉"
                
                lines.append(f"{i}. {tradable} {name} ({code}) {score_emoji} {score}점")
            
            if len(watchlist) > 15:
                lines.append(f"\n... 외 {len(watchlist) - 15}개")
            
            lines.append(f"\n💡 `/unwatch 종목명`으로 제거")
            
            return '\n'.join(lines)
            
        except Exception as e:
            logger.error(f"관심종목 조회 오류: {e}", exc_info=True)
            return f"❌ 관심종목 조회 실패: {e}"

    
    # ============================================================================
    # 알림 제어 핸들러 (Phase 5에서 구현)
    # ============================================================================
    
    def _handle_mute(self, cmd: dict, dry_run: bool) -> str:
        """알림 음소거"""
        args = cmd.get('args', [])
        if not args:
            return "❓ 사용법: `/mute 30` (30분간 음소거)"
        
        try:
            minutes = int(args[0])
            until_timestamp = int(datetime.now(timezone.utc).timestamp()) + (minutes * 60)
            redis_cache.set_notification_mute(until_timestamp)
            return f"🔇 {minutes}분간 알림이 꺼집니다."
        except ValueError:
            return "❓ 올바른 숫자를 입력하세요. 예: `/mute 30`"
    
    def _handle_unmute(self, cmd: dict, dry_run: bool) -> str:
        """알림 음소거 해제"""
        redis_cache.clear_notification_mute()
        return "🔔 알림이 다시 켜졌습니다."
    
    def _handle_alert(self, cmd: dict, dry_run: bool) -> str:
        return "🚧 가격 알림 기능은 Phase 5에서 구현 예정입니다."
    
    def _handle_alerts(self, cmd: dict, dry_run: bool) -> str:
        return "🚧 알림 목록 기능은 Phase 5에서 구현 예정입니다."
    
    # ============================================================================
    # 설정 핸들러 (Phase 6에서 구현)
    # ============================================================================
    
    def _handle_risk(self, cmd: dict, dry_run: bool) -> str:
        return "🚧 리스크 레벨 설정 기능은 Phase 6에서 구현 예정입니다."
    
    def _handle_minscore(self, cmd: dict, dry_run: bool) -> str:
        """최소 LLM 점수 변경"""
        args = cmd.get('args', [])
        
        if not args:
            current = redis_cache.get_config_value('min_llm_score', int(os.getenv('MIN_LLM_SCORE', '70')))
            return f"⚙️ 현재 최소 LLM 점수: {current}점\n\n변경: `/minscore 80`"
        
        try:
            score = int(args[0])
            if not (0 <= score <= 100):
                return "❓ 점수는 0~100 사이여야 합니다."
            
            redis_cache.set_config_value('min_llm_score', score)
            return f"✅ 최소 LLM 점수가 {score}점으로 변경되었습니다."
        except ValueError:
            return "❓ 올바른 숫자를 입력하세요. 예: `/minscore 80`"
    
    def _handle_maxbuy(self, cmd: dict, dry_run: bool) -> str:
        """일일 최대 매수 횟수 변경"""
        args = cmd.get('args', [])
        
        if not args:
            current = redis_cache.get_config_value('max_buy_per_day', 5)
            return f"⚙️ 현재 일일 최대 매수: {current}회\n\n변경: `/maxbuy 3`"
        
        try:
            count = int(args[0])
            if not (0 <= count <= 20):
                return "❓ 횟수는 0~20 사이여야 합니다."
            
            redis_cache.set_config_value('max_buy_per_day', count)
            return f"✅ 일일 최대 매수가 {count}회로 변경되었습니다."
        except ValueError:
            return "❓ 올바른 숫자를 입력하세요. 예: `/maxbuy 3`"
    
    def _handle_config(self, cmd: dict, dry_run: bool) -> str:
        """현재 설정 조회"""
        flags = redis_cache.get_all_trading_flags()
        min_score = redis_cache.get_config_value('min_llm_score', int(os.getenv('MIN_LLM_SCORE', '70')))
        max_buy = redis_cache.get_config_value('max_buy_per_day', 5)
        muted = redis_cache.is_notification_muted()
        
        return f"""⚙️ *현재 설정*

📊 매수 상태: {'⏸️ 중지' if flags['pause'].get('value') else '▶️ 활성'}
🔧 DRY\\_RUN: {'ON' if redis_cache.is_dryrun_enabled() else 'OFF'}
📈 최소 LLM 점수: {min_score}점
🛒 일일 최대 매수: {max_buy}회
🔔 알림: {'🔇 음소거' if muted else '🔔 활성'}"""
    
    # ============================================================================
    # 도움말
    # ============================================================================
    
    def _handle_help(self, cmd: dict, dry_run: bool) -> str:
        """도움말"""
        return """📚 *Ultra Jennie 명령어*

*매매 제어*
/pause - 매수 중지
/resume - 매수 재개
/stop 확인 - 긴급 전체 중지
/dryrun on/off - 테스트 모드

*조회*
/status - 시스템 상태
/portfolio - 보유 종목
/pnl - 오늘 손익
/balance - 계좌 잔고
/price 종목명 - 현재가

*알림*
/mute 분 - N분간 알림 끄기
/unmute - 알림 켜기

*설정*
/minscore 점수 - 최소 LLM 점수
/maxbuy 횟수 - 일일 최대 매수
/config - 현재 설정 조회"""
    
    # ============================================================================
    # 유틸리티
    # ============================================================================
    
    def _resolve_stock(self, name_or_code: str) -> tuple:
        """
        종목명 또는 코드를 (code, name) 튜플로 변환
        """
        try:
            # 6자리 숫자면 코드로 간주
            if name_or_code.isdigit() and len(name_or_code) == 6:
                with database.get_db_connection_context() as db_conn:
                    stock = database.get_stock_by_code(db_conn, name_or_code)
                if stock:
                    return (name_or_code, stock.get('stock_name', name_or_code))
                return (name_or_code, name_or_code)
            else:
                # 종목명으로 검색
                with database.get_db_connection_context() as db_conn:
                    stock = database.search_stock_by_name(db_conn, name_or_code)
                if stock:
                    return (stock.get('stock_code'), stock.get('stock_name'))
                return (None, None)
        except Exception as e:
            logger.error(f"종목 검색 오류: {e}")
            return (None, None)
