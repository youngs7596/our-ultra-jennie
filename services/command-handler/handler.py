# services/command-handler/handler.py
# Version: v3.5
# Command Handler - 수동 명령 처리 로직

import time
import logging
import sys
import os
from datetime import datetime

# shared 패키지 임포트
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import shared.database as database

logger = logging.getLogger(__name__)


class CommandHandler:
    """App으로부터의 수동 명령 처리 클래스"""
    
    def __init__(self, kis, config):
        """
        Args:
            kis: KIS API 클라이언트
            config: ConfigManager 인스턴스
        """
        self.kis = kis
        self.config = config
        
        # 명령 타입별 핸들러 매핑
        self.command_handlers = {
            'MANUAL_SELL': self._handle_manual_sell,
            'MANUAL_BUY': self._handle_manual_buy,
            'CANCEL_ORDER': self._handle_cancel_order,
        }
    
    def poll_and_process(self, dry_run: bool = True) -> dict:
        """
        AGENT_COMMANDS 테이블 폴링 및 명령 처리
        
        Args:
            dry_run: True면 로그만 기록, False면 실제 주문
        
        Returns:
            {
                "status": "success",
                "processed_count": 2,
                "failed_count": 0
            }
        """
        processed_count = 0
        failed_count = 0
        
        try:
            # 대기 중인 명령 조회
            with database.get_db_connection_context() as db_conn:
                pending_commands = database.get_pending_agent_commands(db_conn, limit=100)
            
            if not pending_commands:
                return {
                    "status": "success",
                    "processed_count": 0,
                    "failed_count": 0,
                    "message": "No pending commands"
                }
            
            logger.info(f"대기 중인 명령 {len(pending_commands)}개 발견")
            
            # 명령 처리
            for command in pending_commands:
                try:
                    # 상태를 PROCESSING으로 변경
                    with database.get_db_connection_context() as db_conn:
                        database.update_agent_command_status(
                            db_conn,
                            command['command_id'],
                            'PROCESSING'
                        )
                    
                    # 명령 처리
                    self._process_command(command, dry_run=dry_run)
                    processed_count += 1
                    
                except Exception as e:
                    failed_count += 1
                    logger.error(f"명령 처리 실패 (ID: {command['command_id']}): {e}")
            
            return {
                "status": "success",
                "processed_count": processed_count,
                "failed_count": failed_count,
                "total_commands": len(pending_commands)
            }
            
        except Exception as e:
            logger.error(f"❌ 명령 폴링 중 오류: {e}", exc_info=True)
            return {
                "status": "error",
                "error": str(e),
                "processed_count": processed_count,
                "failed_count": failed_count
            }
    
    def _process_command(self, command: dict, dry_run: bool):
        """명령 처리 메인 로직"""
        command_id = command['command_id']
        command_type = command['command_type']
        payload = command['payload']
        
        logger.info(f"명령 처리 시작 (ID: {command_id}, Type: {command_type})")
        
        try:
            # 명령 타입에 따라 핸들러 호출
            handler = self.command_handlers.get(command_type)
            
            if not handler:
                raise Exception(f"지원하지 않는 명령 타입: {command_type}")
            
            # 실제 명령 처리
            result = handler(command_id, payload, dry_run=dry_run)
            
            # result가 tuple인 경우 (result_msg, order_no) 형태
            if isinstance(result, tuple):
                result_msg, order_no = result
            else:
                result_msg = result
                order_no = None
            
            # 성공 처리
            with database.get_db_connection_context() as db_conn:
                database.update_agent_command_status(
                    db_conn,
                    command_id,
                    'COMPLETED',
                    result_msg=result_msg,
                    order_no=order_no
                )
            
            logger.info(f"✅ 명령 처리 완료 (ID: {command_id})")
            
        except Exception as e:
            logger.error(f"❌ 명령 처리 실패 (ID: {command_id}): {e}", exc_info=True)
            
            # 실패 처리
            try:
                with database.get_db_connection_context() as db_conn:
                    database.update_agent_command_status(
                        db_conn,
                        command_id,
                        'FAILED',
                        result_msg=f"처리 실패: {str(e)}"
                    )
            except Exception as update_error:
                logger.error(f"상태 업데이트 실패: {update_error}")
    
    # ============================================================================
    # 명령 타입별 핸들러
    # ============================================================================
    
    def _handle_manual_sell(self, command_id: int, payload: dict, dry_run: bool) -> tuple:
        """수동 매도 명령 처리"""
        try:
            portfolio_id = payload['portfolio_id']
            stock_code = payload['stock_code']
            stock_name = payload.get('stock_name', stock_code)
            quantity = payload['quantity']
            reason = payload.get('reason', '사용자 수동 매도')
            
            logger.info(f"[수동 매도] {stock_name}({stock_code}) {quantity}주 매도 시작...")
            
            # 1. Portfolio 조회
            with database.get_db_connection_context() as db_conn:
                portfolio = database.get_active_portfolio(db_conn)
            
            holding = next((h for h in portfolio if h.get('id') == portfolio_id), None)
            if not holding:
                raise Exception(f"Portfolio ID {portfolio_id}를 찾을 수 없습니다")
            
            # 2. 현재가 조회
            trading_mode = os.getenv("TRADING_MODE", "MOCK")
            if trading_mode == "MOCK":
                with database.get_db_connection_context() as db_conn:
                    daily_prices = database.get_daily_prices(db_conn, stock_code, limit=1)
                if daily_prices.empty:
                    raise Exception("가격 조회 실패")
                current_price = float(daily_prices['CLOSE_PRICE'].iloc[-1])
                logger.info(f"MOCK 모드: 매도 가격 = {current_price:,}원")
            else:
                snapshot = self.kis.get_stock_snapshot(stock_code)
                if not snapshot:
                    raise Exception("실시간 가격 조회 실패")
                current_price = snapshot['price']
            
            # 3. 매도 주문
            if dry_run:
                logger.info(f"🔧 [DRY_RUN] 매도 주문: {stock_name}({stock_code}) {quantity}주 @ {current_price:,}원")
                order_no = f"DRY_RUN_SELL_{datetime.now().strftime('%Y%m%d%H%M%S')}"
            else:
                order_no = self.kis.trading.place_sell_order(stock_code, quantity, 0)
                if not order_no:
                    raise Exception("매도 주문 실패")
                logger.info(f"매도 주문 완료 (주문번호: {order_no})")
                
                # 체결 확인 (최대 15초)
                is_filled = False
                for i in range(15):
                    time.sleep(1)
                    if self.kis.trading.check_order_status(order_no):
                        is_filled = True
                        logger.info(f"✅ 체결 완료! ({i+1}초 경과)")
                        break
                
                if not is_filled:
                    logger.warning("체결 시간 초과! 주문 취소 시도...")
                    self.kis.trading.cancel_order(order_no, quantity)
                    raise Exception("체결 시간 초과, 주문 취소됨")
                
                # 체결 가격 조회
                filled_details = self.kis.trading.get_filled_order_details(order_no)
                if filled_details:
                    current_price = filled_details.get('avg_price', current_price)
            
            # 4. DB 업데이트
            buy_price = holding['buy_price']
            profit_pct = ((current_price - buy_price) / buy_price) * 100
            profit_amount = (current_price - buy_price) * quantity
            
            shared_regime_cache = database.get_market_regime_cache()
            market_context = shared_regime_cache.get('market_context_dict', {}) if shared_regime_cache else {}
            risk_setting = shared_regime_cache.get('risk_setting', {}) if shared_regime_cache else {}
            
            with database.get_db_connection_context() as db_conn:
                # Portfolio 상태 변경
                database.update_portfolio_status(db_conn, portfolio_id, 'SOLD')
                
                # TradeLog 기록
                stock_info = {
                    'code': stock_code,
                    'name': stock_name,
                    'id': portfolio_id
                }
                
                database.execute_trade_and_log(
                    connection=db_conn,
                    trade_type='SELL_MANUAL',
                    stock_info=stock_info,
                    quantity=quantity,
                    price=current_price,
                    llm_decision={'decision': 'SELL', 'reason': reason},
                    initial_stop_loss_price=None,
                    strategy_signal='MANUAL_SELL',
                    key_metrics_dict={'command_id': command_id, 'order_no': order_no, 'risk_setting': risk_setting},
                    market_context_dict=market_context
                )
            
            result_msg = f"매도 성공: {stock_name}({stock_code}) {quantity}주, 체결가: {current_price:,}원, 수익률: {profit_pct:.2f}%"
            logger.info(f"💰 {result_msg}")
            
            return (result_msg, order_no)
            
        except Exception as e:
            error_msg = f"매도 실패: {str(e)}"
            logger.error(f"❌ {error_msg}")
            raise
    
    def _handle_manual_buy(self, command_id: int, payload: dict, dry_run: bool) -> str:
        """수동 매수 명령 처리 (향후 구현)"""
        logger.warning(f"[수동 매수] 아직 구현되지 않음 (Command ID: {command_id})")
        return "수동 매수 기능은 아직 구현되지 않았습니다."
    
    def _handle_cancel_order(self, command_id: int, payload: dict, dry_run: bool) -> str:
        """주문 취소 명령 처리 (향후 구현)"""
        logger.warning(f"[주문 취소] 아직 구현되지 않음 (Command ID: {command_id})")
        return "주문 취소 기능은 아직 구현되지 않았습니다."

