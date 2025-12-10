# services/sell-executor/executor.py
# Version: v3.5
# Sell Executor - 매도 주문 실행 로직

import logging
import sys
import os
from datetime import datetime, timezone, timedelta

# shared 패키지 임포트
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import shared.database as database
from shared.db.connection import session_scope
from shared.db import repository as repo
from shared.strategy_presets import (
    apply_preset_to_config,
    resolve_preset_for_regime,
)

logger = logging.getLogger(__name__)


class SellExecutor:
    """매도 주문 실행 클래스"""
    
    def __init__(self, kis, config, telegram_bot=None):
        """
        Args:
            kis: KIS API 클라이언트
            config: ConfigManager 인스턴스
            telegram_bot: TelegramBot 인스턴스 (optional)
        """
        self.kis = kis
        self.config = config
        self.telegram_bot = telegram_bot
    
    def execute_sell_order(self, stock_code: str, stock_name: str, quantity: int,
                          sell_reason: str, strategy_preset: dict | None = None,
                          risk_setting: dict | None = None,
                          dry_run: bool = True) -> dict:
        """
        매도 주문 실행
        
        Args:
            stock_code: 종목 코드
            stock_name: 종목 이름
            quantity: 매도 수량
            sell_reason: 매도 사유
            dry_run: True면 로그만 기록, False면 실제 주문
        
        Returns:
            {
                "status": "success" | "error",
                "stock_code": "005930",
                "stock_name": "삼성전자",
                "order_no": "12345",
                "quantity": 10,
                "price": 72000,
                "reason": "..."
            }
        """
        logger.info(f"=== 매도 주문 실행 시작: {stock_name}({stock_code}) ===")
        
        try:
            shared_regime_cache = None
            preset_info = strategy_preset or {}
            preset_name = preset_info.get('name')
            preset_params = preset_info.get('params', {})
            if not preset_params:
                shared_regime_cache = database.get_market_regime_cache()
                if shared_regime_cache:
                    shared_preset = shared_regime_cache.get('strategy_preset', {}) or {}
                    preset_name = shared_preset.get('name', preset_name)
                    preset_params = shared_preset.get('params', preset_params)
            if not preset_params:
                preset_name, preset_params = resolve_preset_for_regime("SIDEWAYS")
            apply_preset_to_config(self.config, preset_params)
            logger.info(f"전략 프리셋 적용: {preset_name}")
            
            if risk_setting is None:
                if shared_regime_cache is None:
                    shared_regime_cache = database.get_market_regime_cache()
                if shared_regime_cache:
                    risk_setting = shared_regime_cache.get('risk_setting')
            risk_setting = risk_setting or {} # type: ignore
            market_context = {}
            if shared_regime_cache:
                market_context = shared_regime_cache.get('market_context_dict', {}) or {}

            with session_scope() as session:
                # 1. 보유 내역 확인
                portfolio = repo.get_active_portfolio(session)
                holding = next((h for h in portfolio if h['code'] == stock_code), None)
                
                if not holding:
                    logger.error(f"보유 내역이 없습니다: {stock_code}")
                    return {"status": "error", "reason": "Not in portfolio"}
                
                # 1.5 중복 주문 체크 (Idempotency)
                # 최근 매도 주문 확인 (중복 실행 방지)
                if repo.was_traded_recently(session, stock_code, hours=0.17): # 10분
                    logger.warning(f"⚠️ 최근 매도 주문 이력 존재: {stock_name}({stock_code}) - 중복 실행 방지")
                    return {"status": "skipped", "reason": f"Duplicate sell order detected for {stock_code}"}
                
                # 2. 현재가 조회
                trading_mode = os.getenv("TRADING_MODE", "MOCK")
                if trading_mode == "MOCK":
                    # Mock 모드: DB에서 최근 종가 사용
                    daily_prices = database.get_daily_prices(session, stock_code, limit=1, table_name="STOCK_DAILY_PRICES_3Y")
                    if daily_prices.empty:
                        logger.error("가격 조회 실패")
                        return {"status": "error", "reason": "Failed to get price"}
                    current_price = float(daily_prices['CLOSE_PRICE'].iloc[-1])
                    logger.info(f"MOCK 모드: 매도 가격 = {current_price}")
                else:
                    snapshot = self.kis.get_stock_snapshot(stock_code)
                    if not snapshot:
                        logger.error("실시간 가격 조회 실패")
                        return {"status": "error", "reason": "Failed to get current price"}
                    current_price = snapshot['price']
                
                # 3. 수익률 계산
                buy_price = holding['avg_price']
                profit_pct = ((current_price - buy_price) / buy_price) * 100
                profit_amount = (current_price - buy_price) * quantity
                
                # 보유 일수 계산
                holding_days = 0
                if 'created_at' in holding and holding['created_at']:
                    buy_date = holding['created_at']
                    if isinstance(buy_date, str):
                        buy_date = datetime.strptime(buy_date, '%Y-%m-%d %H:%M:%S') if ' ' in buy_date else datetime.strptime(buy_date, '%Y-%m-%d')
                    if buy_date.tzinfo is None:
                        buy_date_utc = buy_date.replace(tzinfo=timezone.utc)
                    else:
                        buy_date_utc = buy_date
                    holding_days = (datetime.now(timezone.utc) - buy_date_utc).days
                
                logger.info(f"매수가: {buy_price:,}원, 현재가: {current_price:,}원")
                logger.info(f"수익률: {profit_pct:.2f}%, 수익금: {profit_amount:,}원, 보유일: {holding_days}일")
                
                # RAG 캐시 신선도 검증
                rag_context = "최신 뉴스 없음"
                is_fresh = False
                last_updated: Optional[datetime] = None
                try:
                    rag_context, is_fresh, last_updated = database.get_rag_context_with_validation(
                        session, stock_code, max_age_hours=24
                    )
                    if is_fresh:
                        logger.info(f"✅ [{stock_code}] 신선한 RAG 캐시 사용 (업데이트: {last_updated})")
                    elif last_updated:
                        logger.warning(f"⚠️ [{stock_code}] 오래된 RAG 캐시 폐기 (업데이트: {last_updated})")
                    else:
                        logger.info(f"ℹ️ [{stock_code}] RAG 캐시 없음")
                except Exception as e:
                    logger.error(f"RAG 컨텍스트 조회 오류: {e}")
                
                # 복기용 지표 수집
                key_metrics_dict = {
                    "sell_reason": sell_reason,
                    "current_price": float(current_price),
                    "buy_price": float(buy_price),
                    "profit_pct": round(profit_pct, 2),
                    "profit_amount": round(profit_amount, 0),
                    "holding_days": holding_days,
                    "stop_loss_price": float(holding.get('stop_loss_price', 0)),
                    "high_price": float(holding.get('high_price', 0)),
                    "rag_fresh": is_fresh,
                    "rag_last_updated": str(last_updated) if last_updated else None,
                    "risk_setting": risk_setting
                }
                
                # 4. 매도 주문 실행
                if dry_run:
                    logger.info(f"🔧 [DRY_RUN] 매도 주문: {stock_name}({stock_code}) {quantity}주 @ {current_price:,}원")
                    order_no = f"DRY_RUN_SELL_{datetime.now().strftime('%Y%m%d%H%M%S')}"
                else:
                    order_no = self.kis.place_sell_order(
                        stock_code=stock_code,
                        quantity=quantity,
                        price=0  # 시장가
                    )
                    
                    if not order_no:
                        logger.error("매도 주문 실패")
                        return {"status": "error", "reason": "Order failed"}
                    
                    logger.info(f"✅ 매도 주문 체결: 주문번호 {order_no}")
                
                # 5. DB 업데이트 (복기용 지표 포함)
                self._record_sell_trade(
                    session=session,
                    stock_code=stock_code,
                    stock_name=stock_name,
                    quantity=quantity,
                    sell_price=current_price,
                    buy_price=buy_price,
                    profit_pct=profit_pct,
                    profit_amount=profit_amount,
                    sell_reason=sell_reason,
                    order_no=order_no,
                    holding=holding,
                    key_metrics_dict=key_metrics_dict,
                    dry_run=dry_run,
                    market_context=market_context
                )
                
                # 텔레그램 알림 발송
                if self.telegram_bot:
                    try:
                        profit_emoji = "📈" if profit_pct > 0 else "📉"
                        
                        # Mock/Real 모드 및 DRY_RUN 표시
                        trading_mode = os.getenv('TRADING_MODE', 'REAL')
                        mode_indicator = ""
                        if trading_mode == "MOCK":
                            mode_indicator = "🧪 *[MOCK 테스트]*\n"
                        if dry_run:
                            mode_indicator += "⚠️ *[DRY RUN - 실제 주문 없음]*\n"
                        
                        message = f"""{mode_indicator}{profit_emoji} *매도 체결*

📊 *종목*: {stock_name} ({stock_code})
💵 *매도가*: {current_price:,}원
💰 *매수가*: {buy_price:,}원
📊 *수량*: {quantity}주

💸 *수익금*: {profit_amount:+,}원
📈 *수익률*: {profit_pct:+.2f}%
🏷️ *사유*: {sell_reason}
📅 *보유일*: {holding_days}일"""
                        
                        self.telegram_bot.send_message(message)
                        logger.info("✅ 텔레그램 알림 발송 완료")
                    except Exception as e:
                        logger.warning(f"⚠️ 텔레그램 알림 발송 실패: {e}")
                
                logger.info("=== 매도 처리 완료 ===")
                return {
                    "status": "success",
                    "stock_code": stock_code,
                    "stock_name": stock_name,
                    "order_no": order_no,
                    "quantity": quantity,
                    "sell_price": current_price,
                    "buy_price": buy_price,
                    "profit_pct": round(profit_pct, 2),
                    "profit_amount": round(profit_amount, 0),
                    "sell_reason": sell_reason,
                    "risk_setting": risk_setting,
                    "dry_run": dry_run
                }
        
        except Exception as e:
            logger.error(f"❌ 매도 처리 중 오류: {e}", exc_info=True)
            return {"status": "error", "reason": str(e)}
    
    def _record_sell_trade(self, session, stock_code: str, stock_name: str,
                          quantity: int, sell_price: float, buy_price: float,
                          profit_pct: float, profit_amount: float, sell_reason: str,
                          order_no: str, holding: dict, key_metrics_dict: dict,
                          dry_run: bool, market_context: dict | None = None):
        """매도 거래 기록 (복기용 지표 포함)"""
        try:
            # execute_trade_and_log 사용 (Portfolio + TradeLog 통합 처리)
            database.execute_trade_and_log(
                connection=session,
                trade_type='SELL',
                stock_info={'id': holding['id'], 'code': stock_code, 'name': stock_name},
                quantity=quantity,
                price=sell_price,
                llm_decision={'reason': sell_reason},
                strategy_signal=sell_reason,
                key_metrics_dict=key_metrics_dict,
                market_context_dict=market_context or {}
            )
            logger.info(f"✅ Trade Log 기록 완료 (복기용 지표: {len(key_metrics_dict)}개)")
            
            # 성과 통계 업데이트 (선택적)
            if not dry_run and 'buy_date' in holding:
                try:
                    holding_days = (datetime.now(timezone.utc) - holding['created_at']).days
                    database.update_performance_stats(
                        db_conn=session,
                        stock_code=stock_code,
                        profit_pct=profit_pct,
                        profit_amount=profit_amount,
                        holding_days=holding_days
                    )
                    logger.info("✅ 성과 통계 업데이트 완료")
                except Exception as e:
                    logger.warning(f"⚠️ 성과 통계 업데이트 실패: {e}")
            
        except Exception as e:
            logger.error(f"거래 기록 오류: {e}", exc_info=True)
            raise
