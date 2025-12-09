# services/command-handler/handler.py
# Version: v3.6
# Command Handler - Telegram 명령 처리 로직

import logging
import sys
import os
from datetime import datetime, timezone
from typing import Optional

# shared 패키지 임포트
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import shared.database as database
import shared.redis_cache as redis_cache
from shared.notification import TelegramBot
from shared.rabbitmq import RabbitMQPublisher
from limits import (
    is_rate_limited,
    check_and_increment_manual_trade_limit,
)
from messages import HELP_TEXT, stop_confirm_message
from handler_trading import (
    handle_manual_buy,
    handle_manual_sell,
    handle_sellall,
)

logger = logging.getLogger(__name__)


class CommandHandler:
    """Telegram 명령 처리 클래스"""
    
    def __init__(self, kis, config, telegram_bot: TelegramBot = None,
                 buy_publisher: RabbitMQPublisher = None,
                 sell_publisher: RabbitMQPublisher = None):
        """
        Args:
            kis: KIS API 클라이언트
            config: ConfigManager 인스턴스
            telegram_bot: TelegramBot 인스턴스
            buy_publisher: buy-signals 큐 퍼블리셔
            sell_publisher: sell-orders 큐 퍼블리셔
        """
        self.kis = kis
        self.config = config
        self.telegram_bot = telegram_bot
        self.buy_publisher = buy_publisher
        self.sell_publisher = sell_publisher
        self.min_command_interval = int(os.getenv("COMMAND_MIN_INTERVAL_SECONDS", "5"))
        self.manual_trade_daily_limit = int(os.getenv("MANUAL_TRADE_DAILY_LIMIT", "20"))
        
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
        
        # 레이트 리미트 체크 (기본 5초)
        if is_rate_limited(chat_id, self.min_command_interval):
            wait_msg = f"⏳ 명령이 너무 빠릅니다. {self.min_command_interval}초 후 다시 시도하세요."
            self.telegram_bot.reply(chat_id, wait_msg)
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
        """매수 재개 + 긴급중지 해제"""
        redis_cache.set_trading_flag('pause', False, reason='사용자 요청')
        redis_cache.set_trading_flag('stop', False, reason='사용자 요청')
        
        return "▶️ 매수가 재개되었습니다.\n\n긴급 중지도 해제되었습니다."
    
    def _handle_stop(self, cmd: dict, dry_run: bool) -> str:
        """긴급 전체 중지"""
        args = cmd.get('args', [])
        
        # 확인 키워드 필요
        if not args or args[0] not in ['확인', '긴급']:
            return stop_confirm_message()
        
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
        """수동 매수 - handler_trading.handle_manual_buy 위임"""
        return handle_manual_buy(
            cmd=cmd,
            dry_run=dry_run,
            kis=self.kis,
            telegram_bot=self.telegram_bot,
            buy_publisher=self.buy_publisher,
            manual_trade_daily_limit=self.manual_trade_daily_limit,
            resolve_stock_fn=self._resolve_stock
        )
    
    def _handle_manual_sell(self, cmd: dict, dry_run: bool) -> str:
        """수동 매도 - handler_trading.handle_manual_sell 위임"""
        return handle_manual_sell(
            cmd=cmd,
            dry_run=dry_run,
            kis=self.kis,
            telegram_bot=self.telegram_bot,
            sell_publisher=self.sell_publisher,
            manual_trade_daily_limit=self.manual_trade_daily_limit,
            resolve_stock_fn=self._resolve_stock
        )
    
    def _handle_sellall(self, cmd: dict, dry_run: bool) -> str:
        """전체 청산 - handler_trading.handle_sellall 위임"""
        return handle_sellall(
            cmd=cmd,
            dry_run=dry_run,
            sell_publisher=self.sell_publisher
        )

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
        """가격 알림 설정"""
        args = cmd.get('args', [])
        
        if not args:
            return """❓ *가격 알림 설정 사용법*

`/alert 종목명 목표가격`

*예시:*
• `/alert 삼성전자 80000` - 80,000원 도달 시 알림
• `/alert 005930 75000` - 종목코드로 설정

삭제: `/alert 삼성전자 삭제`"""
        
        if len(args) < 2:
            return "❓ 종목과 목표가격을 모두 입력하세요.\n예: `/alert 삼성전자 80000`"
        
        stock_input = args[0]
        action = args[1]
        
        try:
            # 1. 종목 코드 변환
            stock_code, stock_name = self._resolve_stock(stock_input)
            if not stock_code:
                return f"❓ 종목을 찾을 수 없습니다: {stock_input}"
            
            # 2. 삭제 명령인지 확인
            if action in ['삭제', 'delete', 'remove', 'del']:
                if redis_cache.delete_price_alert(stock_code):
                    return f"🗑️ 가격 알림이 삭제되었습니다.\n\n{stock_name} ({stock_code})"
                else:
                    return f"ℹ️ {stock_name}에 설정된 알림이 없습니다."
            
            # 3. 목표 가격 파싱
            try:
                target_price = int(action.replace(',', '').replace('원', ''))
            except ValueError:
                return f"❓ 올바른 가격을 입력하세요: {action}"
            
            if target_price <= 0:
                return "❓ 가격은 0보다 커야 합니다."
            
            # 4. 현재가 조회하여 알림 타입 결정
            try:
                snapshot = self.kis.get_stock_snapshot(stock_code)
                current_price = snapshot.get('price', 0) if snapshot else 0
                
                if current_price > 0:
                    if target_price > current_price:
                        alert_type = "above"
                        direction = "⬆️ 이상"
                    else:
                        alert_type = "below"
                        direction = "⬇️ 이하"
                else:
                    alert_type = "above"
                    direction = "도달"
                    current_price = 0
            except Exception:
                alert_type = "above"
                direction = "도달"
                current_price = 0
            
            # 5. 알림 설정
            redis_cache.set_price_alert(
                stock_code=stock_code,
                target_price=target_price,
                stock_name=stock_name,
                alert_type=alert_type
            )
            
            result = f"""⏰ *가격 알림 설정 완료*

📌 {stock_name} ({stock_code})
🎯 목표가: {target_price:,.0f}원 {direction}"""
            
            if current_price > 0:
                diff = target_price - current_price
                diff_pct = (diff / current_price * 100) if current_price > 0 else 0
                result += f"\n💵 현재가: {current_price:,.0f}원"
                result += f"\n📊 차이: {diff:+,.0f}원 ({diff_pct:+.2f}%)"
            
            result += f"\n\n⏳ 7일간 유효 | 삭제: `/alert {stock_input} 삭제`"
            
            return result
            
        except Exception as e:
            logger.error(f"가격 알림 설정 오류: {e}", exc_info=True)
            return f"❌ 가격 알림 설정 실패: {e}"
    
    def _handle_alerts(self, cmd: dict, dry_run: bool) -> str:
        """설정된 가격 알림 목록"""
        try:
            alerts = redis_cache.get_price_alerts()
            
            if not alerts:
                return "📭 설정된 가격 알림이 없습니다.\n\n`/alert 종목명 목표가격`으로 설정하세요."
            
            lines = [f"⏰ *가격 알림 목록* ({len(alerts)}개)\n"]
            
            for code, info in alerts.items():
                name = info.get('stock_name', code)
                target = info.get('target_price', 0)
                alert_type = info.get('alert_type', 'above')
                direction = "⬆️" if alert_type == "above" else "⬇️"
                
                lines.append(f"• {name} ({code})")
                lines.append(f"  {direction} {target:,.0f}원")
            
            lines.append(f"\n💡 삭제: `/alert 종목명 삭제`")
            
            return '\n'.join(lines)
            
        except Exception as e:
            logger.error(f"가격 알림 목록 오류: {e}", exc_info=True)
            return f"❌ 가격 알림 목록 조회 실패: {e}"

    # ============================================================================
    # 설정 핸들러
    # ============================================================================
    
    def _handle_risk(self, cmd: dict, dry_run: bool) -> str:
        """리스크 레벨 변경"""
        args = cmd.get('args', [])
        
        valid_levels = ['conservative', 'moderate', 'aggressive']
        level_descriptions = {
            'conservative': '보수적 - 소액 분산 투자, 손절 빠름',
            'moderate': '보통 - 균형 잡힌 매매',
            'aggressive': '공격적 - 적극 매수, 손절 늦음'
        }
        
        if not args:
            current = redis_cache.get_config_value('risk_level', 'moderate')
            desc = level_descriptions.get(current, '')
            
            return f"""⚙️ *현재 리스크 레벨*: {current}
{desc}

*변경 방법:*
• `/risk conservative` - 보수적
• `/risk moderate` - 보통
• `/risk aggressive` - 공격적"""
        
        level = args[0].lower()
        
        if level not in valid_levels:
            return f"❓ 유효한 레벨: conservative, moderate, aggressive"
        
        redis_cache.set_config_value('risk_level', level)
        desc = level_descriptions.get(level, '')
        
        return f"✅ 리스크 레벨이 변경되었습니다.\n\n📊 {level}\n{desc}"
    
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
        return HELP_TEXT
    
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

