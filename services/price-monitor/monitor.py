# monitor.py
# Price Monitor - 실시간 가격 감시 및 매도 신호 발행

import time
import logging
import sys
import os
from datetime import datetime
from threading import Event

# shared 패키지 임포트
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import shared.database as database
import shared.strategy as strategy
import shared.redis_cache as redis_cache
from shared.db.connection import session_scope
from shared.db import repository as repo
from shared.notification import TelegramBot

logger = logging.getLogger(__name__)


class PriceMonitor:
    """실시간 가격 감시 클래스"""
    
    def __init__(self, kis, config, tasks_publisher, telegram_bot: TelegramBot = None):
        """
        Args:
            kis: KIS API 클라이언트
            config: ConfigManager 인스턴스
            tasks_publisher: RabbitMQPublisher 인스턴스
            telegram_bot: 가격 알림 전송용 텔레그램 봇 (옵션)
        """
        self.kis = kis
        self.config = config
        self.tasks_publisher = tasks_publisher
        self.telegram_bot = telegram_bot
        self.stop_event = Event()
        
        trading_mode = os.getenv("TRADING_MODE", "MOCK")
        self.use_websocket = (trading_mode == "REAL")
        self.alert_check_interval = int(os.getenv("PRICE_ALERT_CHECK_INTERVAL", "15"))
        
        logger.info(f"Price Monitor 설정: TRADING_MODE={trading_mode}, USE_WEBSOCKET={self.use_websocket}")
        
        self.portfolio_cache = {}
    
    def start_monitoring(self, dry_run: bool = True):
        logger.info("=== 가격 모니터링 시작 ===")
        try:
            # 시장 운영 여부 확인 (휴장/주말/장외면 바로 중단)
            try:
                if hasattr(self.kis, "check_market_open"):
                    if not self.kis.check_market_open():
                        logger.warning("💤 시장 미운영(휴장/주말/장외)으로 모니터링을 건너뜁니다.")
                        return
                else:
                    # Gateway 클라이언트 등 최소한의 주말/시간 필터
                    from datetime import datetime
                    import pytz
                    kst = pytz.timezone("Asia/Seoul")
                    now = datetime.now(kst)
                    if not (0 <= now.weekday() <= 4 and 8 <= now.hour <= 16):
                        logger.warning("💤 시장 미운영 시간(주말/장외)으로 모니터링을 건너뜁니다.")
                        return
            except Exception as e:
                logger.error(f"시장 운영 여부 확인 실패: {e}", exc_info=True)
                return

            if self.use_websocket:
                self._monitor_with_websocket(dry_run)
            else:
                self._monitor_with_polling(dry_run)
        except Exception as e:
            logger.error(f"❌ 모니터링 중 오류: {e}", exc_info=True)
        finally:
            logger.info("=== 가격 모니터링 종료 ===")
    
    def stop_monitoring(self):
        logger.info("모니터링 중단 신호 수신")
        self.stop_event.set()
    
    def _monitor_with_websocket(self, dry_run: bool):
        logger.info("=== WebSocket 모드로 실시간 모니터링 시작 ===")
        
        last_alert_check = 0
        while not self.stop_event.is_set():
            try:
                with session_scope(readonly=True) as session:
                    portfolio = repo.get_active_portfolio(session)
                
                if not portfolio:
                    logger.info("   (WS) 보유 종목이 없습니다. 60초 후 다시 확인합니다.")
                    time.sleep(60)
                    continue
                
                portfolio_codes = list(set(item['code'] for item in portfolio))
                self.portfolio_cache = {item['id']: item for item in portfolio}
                
                self.kis.websocket.start_realtime_monitoring(
                    portfolio_codes=portfolio_codes,
                    on_price_func=self._on_websocket_price_update
                )
                
                if not self.kis.websocket.connection_event.wait(timeout=15):
                    logger.error("   (WS) ❌ WebSocket 연결 시간(15초) 초과! 재시도합니다.")
                    if self.kis.websocket.ws:
                        self.kis.websocket.ws.close()
                    time.sleep(5)
                    continue
                
                logger.info("   (WS) ✅ WebSocket 연결 확인! 실시간 감시 시작.")
                
                last_status_log_time = time.time()
                while self.kis.websocket.connection_event.is_set() and not self.stop_event.is_set():
                    time.sleep(1)
                    now = time.time()
                    if now - last_status_log_time >= 600:
                        logger.info(f"   (WS) [상태 체크] 연결 유지 중, 감시: {len(self.portfolio_cache)}개")
                        last_status_log_time = now
                    if now - last_alert_check >= self.alert_check_interval:
                        self._process_price_alerts()
                        last_alert_check = now
                
                if self.stop_event.is_set():
                    break
                
                logger.warning("   (WS) WebSocket 연결 끊김. 재연결 시도.")
                
            except Exception as e:
                logger.error(f"❌ (WS) 모니터링 오류: {e}", exc_info=True)
                time.sleep(60)
        
        self.kis.websocket.stop()
    
    def _monitor_with_polling(self, dry_run: bool):
        logger.info("HTTP Polling 모드로 모니터링 시작")
        check_interval = self.config.get_int('PRICE_MONITOR_INTERVAL_SECONDS', default=10)
        
        last_alert_check = 0
        while not self.stop_event.is_set():
            try:
                with session_scope(readonly=True) as session:
                    portfolio = repo.get_active_portfolio(session)
                
                if not portfolio:
                    time.sleep(check_interval)
                    continue
                
                for holding in portfolio:
                    if self.stop_event.is_set(): break
                    
                    stock_code = holding['code']
                    trading_mode = os.getenv("TRADING_MODE", "MOCK")
                    
                    if trading_mode == "MOCK":
                        with session_scope(readonly=True) as session:
                            prices = database.get_daily_prices(session, stock_code, limit=1)
                            current_price = float(prices['CLOSE_PRICE'].iloc[-1]) if not prices.empty else 0
                    else:
                        snap = self.kis.get_stock_snapshot(stock_code)
                        current_price = snap['price'] if snap else 0
                    
                    if current_price <= 0: continue
                    
                    with session_scope(readonly=True) as session: # _check_sell_signal이 session을 받도록 수정
                        signal = self._check_sell_signal(
                            session, stock_code, holding.get('name', stock_code),
                            holding['avg_price'], current_price, holding
                        )
                    
                    if signal:
                        logger.info(f"🔔 매도 신호 발생: {holding.get('name', stock_code)}")
                        self._publish_sell_order(signal, holding, current_price)
                
                # 가격 알림 체크 (주기적)
                now = time.time()
                if now - last_alert_check >= self.alert_check_interval:
                    self._process_price_alerts()
                    last_alert_check = now
                
                time.sleep(check_interval)
            except Exception as e:
                logger.error(f"모니터링 루프 오류: {e}")
                time.sleep(check_interval)
    
    def _check_sell_signal(self, session, stock_code, stock_name, buy_price, current_price, holding):
        try:
            profit_pct = ((current_price - buy_price) / buy_price) * 100
            daily_prices = database.get_daily_prices(session, stock_code, limit=30)
            
            # 1. ATR Trailing Stop
            if not daily_prices.empty and len(daily_prices) >= 15:
                atr = strategy.calculate_atr(daily_prices, period=14)
                if atr:
                    mult = self.config.get_float('ATR_MULTIPLIER', default=2.0)
                    stop_price = buy_price - (mult * atr)
                    if current_price < stop_price:
                        return {"signal": True, "reason": f"ATR Stop (Price {current_price} < {stop_price:.0f})", "quantity_pct": 100.0}
            
            # Fallback: Fixed Stop Loss
            stop_loss = self.config.get_float('SELL_STOP_LOSS_PCT', default=-5.0)
            
            # [Jennie's Fix] Stop Loss는 항상 음수여야 합니다. 양수로 설정된 경우 음수로 변환합니다.
            # 예: 사용자가 5.0(5% 손절)으로 설정하면 -5.0으로 처리하여 2% 수익 구간에서 매도되는 사고 방지
            if stop_loss > 0:
                stop_loss = -stop_loss

            if profit_pct <= stop_loss:
                return {"signal": True, "reason": f"Fixed Stop Loss: {profit_pct:.2f}% (Limit: {stop_loss}%)", "quantity_pct": 100.0}

            # 2. RSI Overbought (Scale-out)
            if not daily_prices.empty and len(daily_prices) >= 15:
                prices = daily_prices['CLOSE_PRICE'].tolist() + [current_price]
                rsi = strategy.calculate_rsi(prices[::-1], period=14)
                threshold = self.config.get_float('SELL_RSI_OVERBOUGHT_THRESHOLD', default=75.0)
                if rsi and rsi >= threshold:
                    return {"signal": True, "reason": f"RSI Overbought ({rsi:.1f})", "quantity_pct": 50.0}

            # 3. Target Profit
            target = self.config.get_float('SELL_TARGET_PROFIT_PCT', default=10.0)
            if profit_pct >= target:
                return {"signal": True, "reason": f"Target Profit: {profit_pct:.2f}%", "quantity_pct": 100.0}
            
            # 4. Death Cross
            if not daily_prices.empty and len(daily_prices) >= 20:
                import pandas as pd
                new_row = pd.DataFrame([{'PRICE_DATE': datetime.now(), 'CLOSE_PRICE': current_price, 'OPEN_PRICE': current_price, 'HIGH_PRICE': current_price, 'LOW_PRICE': current_price}])
                df = pd.concat([daily_prices, new_row], ignore_index=True)
                if strategy.check_death_cross(df):
                    return {"signal": True, "reason": "Death Cross", "quantity_pct": 100.0}
            
            # 5. Max Holding Days
            if holding.get('buy_date'):
                days = (datetime.now() - datetime.strptime(holding['buy_date'], '%Y%m%d')).days
                if days >= self.config.get_int('MAX_HOLDING_DAYS', default=30):
                    return {"signal": True, "reason": f"Max Holding Days ({days})", "quantity_pct": 100.0}
            
            return None
        except Exception as e:
            logger.error(f"[{stock_name}] 신호 체크 오류: {e}")
            return None

    def _on_websocket_price_update(self, stock_code, current_price, current_high):
        try:
            # logger.debug(f"   (WS) [{stock_code}] {current_price}")
            holdings = [h for h in self.portfolio_cache.values() if h['code'] == stock_code]
            if not holdings: return
            
            for h in holdings:
                with session_scope(readonly=True) as session:
                    signal = self._check_sell_signal(session,
                        stock_code, h.get('name', stock_code),
                        h['avg_price'], current_price, h
                    )
                if signal:
                    logger.info(f"🔔 (WS) 매도 신호: {h.get('name', stock_code)}")
                    self._publish_sell_order(signal, h, current_price)
                    # 중복 매도 방지 위해 캐시 제거
                    self.portfolio_cache.pop(h['id'], None)
        except Exception as e:
            logger.error(f"❌ (WS) 오류: {e}")

    def _publish_sell_order(self, signal, holding, current_price):
        q_pct = signal.get('quantity_pct', 100.0)
        qty = int(holding['quantity'] * (q_pct / 100.0)) or 1
        
        payload = {
            "stock_code": holding['code'],
            "stock_name": holding.get('name', holding['code']),
            "quantity": qty,
            "current_price": current_price,
            "sell_reason": signal['reason'],
            "holding_id": holding.get('id')
        }
        
        # RabbitMQPublisher.publish() 사용 (create_task 대신)
        msg_id = self.tasks_publisher.publish(payload)
        if msg_id:
            logger.info(f"   ✅ 매도 요청 발행 완료: {msg_id}")
        else:
            logger.error(f"   ❌ 매도 요청 발행 실패: {holding['code']}")

    # ============================================================================
    # 가격 알림 처리
    # ============================================================================
    def _process_price_alerts(self):
        try:
            alerts = redis_cache.get_price_alerts()
            if not alerts:
                return
            
            trading_mode = os.getenv("TRADING_MODE", "MOCK")
            for code, info in alerts.items():
                target = info.get("target_price")
                alert_type = info.get("alert_type", "above")
                name = info.get("stock_name", code)
                
                current_price = 0
                if trading_mode == "MOCK":
                    with session_scope(readonly=True) as session:
                        prices = database.get_daily_prices(session, code, limit=1)
                        current_price = float(prices['CLOSE_PRICE'].iloc[-1]) if not prices.empty else 0
                else:
                    snap = self.kis.get_stock_snapshot(code)
                    current_price = snap.get("price", 0) if snap else 0
                
                if current_price <= 0:
                    continue
                
                triggered = False
                if alert_type == "above" and current_price >= target:
                    triggered = True
                if alert_type == "below" and current_price <= target:
                    triggered = True
                
                if triggered:
                    redis_cache.delete_price_alert(code)
                    msg = (
                        f"⏰ 가격 알림 도달\n\n"
                        f"{name} ({code})\n"
                        f"목표가: {target:,.0f}원 ({'이상' if alert_type=='above' else '이하'})\n"
                        f"현재가: {current_price:,.0f}원"
                    )
                    if self.telegram_bot:
                        self.telegram_bot.send_message(msg)
                    logger.info(f"[Alert] {code} {alert_type} {target} → {current_price}")
        except Exception as e:
            logger.error(f"가격 알림 처리 오류: {e}", exc_info=True)
