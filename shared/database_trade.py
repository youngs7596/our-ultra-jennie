"""
shared/database_trade.py - 거래 실행 및 로깅 관련 함수

이 모듈은 거래 실행, 로깅, 중복 주문 검사, 포트폴리오 관리 등의 함수를 제공합니다.
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from shared.database_base import _get_table_name, _is_mariadb, _is_sqlalchemy_ready
from shared.db import connection as sa_connection
from shared.db import repository as sa_repository

logger = logging.getLogger(__name__)


def execute_trade_and_log(
    connection, trade_type, stock_info, quantity, price, llm_decision,
    initial_stop_loss_price=None,
    strategy_signal: str = None,
    key_metrics_dict: dict = None,
    market_context_dict: dict = None
):
    """
    거래 실행 및 로깅 (SQLAlchemy 우선, Legacy 폴백)
    """
    if _is_sqlalchemy_ready():
        try:
            with sa_connection.session_scope() as session:
                return _execute_trade_and_log_sqlalchemy(
                    session, trade_type, stock_info, quantity, price, llm_decision,
                    initial_stop_loss_price, strategy_signal, key_metrics_dict, market_context_dict
                )
        except Exception as e:
            logger.error(f"❌ [SQLAlchemy] execute_trade_and_log 실패 - legacy 폴백: {e}", exc_info=True)
    
    return _execute_trade_and_log_legacy(
        connection, trade_type, stock_info, quantity, price, llm_decision,
        initial_stop_loss_price, strategy_signal, key_metrics_dict, market_context_dict
    )


def _execute_trade_and_log_sqlalchemy(
    session, trade_type, stock_info, quantity, price, llm_decision,
    initial_stop_loss_price, strategy_signal, key_metrics_dict, market_context_dict
):
    """SQLAlchemy 기반 거래 실행 및 로깅"""
    from shared.db import models as db_models
    import numpy as np
    
    if price <= 0:
        logger.error(f"❌ DB: execute_trade_and_log price 유효하지 않음 (price: {price})")
        return False
    
    llm_reason = llm_decision.get('reason', 'N/A') if llm_decision else 'N/A'
    MAX_REASON_LENGTH = 1950
    if len(llm_reason) > MAX_REASON_LENGTH:
        llm_reason = llm_reason[:MAX_REASON_LENGTH-3] + '...'

    def convert_numpy_types(obj):
        if isinstance(obj, dict):
            return {k: convert_numpy_types(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [convert_numpy_types(item) for item in obj]
        elif isinstance(obj, (np.integer, np.int64)):
            return int(obj)
        elif isinstance(obj, (np.floating, np.float64)):
            return float(obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        else:
            return obj

    key_metrics_json = json.dumps(convert_numpy_types(key_metrics_dict or {}))
    market_context_json = json.dumps(convert_numpy_types(market_context_dict or {}))

    new_portfolio_id = None

    if trade_type.startswith('BUY'):
        existing = session.query(db_models.Portfolio).filter_by(
            stock_code=stock_info['code'], status='HOLDING'
        ).first()

        if existing:
            new_quantity = existing.quantity + quantity
            new_total_amount = existing.total_buy_amount + (quantity * price)
            new_avg_price = new_total_amount / new_quantity if new_quantity > 0 else price
            new_high_price = max(existing.current_high_price or price, price)

            if initial_stop_loss_price is None:
                initial_stop_loss_price = price * 0.93
            new_stop_loss = min(
                existing.stop_loss_price if existing.stop_loss_price and existing.stop_loss_price > 0 else initial_stop_loss_price, 
                initial_stop_loss_price
            )

            existing.quantity = new_quantity
            existing.average_buy_price = new_avg_price
            existing.total_buy_amount = new_total_amount
            existing.current_high_price = new_high_price
            existing.stop_loss_price = new_stop_loss
            if existing.sell_state not in ('SECURED', 'TRAILING'):
                existing.sell_state = 'INITIAL'
            new_portfolio_id = existing.id
            logger.info("   (SQLAlchemy) Portfolio 업데이트 (ID=%s)", new_portfolio_id)
        else:
            if initial_stop_loss_price is None:
                initial_stop_loss_price = price * 0.93
            portfolio = db_models.Portfolio(
                stock_code=stock_info['code'],
                stock_name=stock_info['name'],
                quantity=quantity,
                average_buy_price=price,
                total_buy_amount=quantity * price,
                current_high_price=price,
                status='HOLDING',
                sell_state='INITIAL',
                stop_loss_price=initial_stop_loss_price,
            )
            session.add(portfolio)
            session.flush()
            new_portfolio_id = portfolio.id
            logger.info("   (SQLAlchemy) 새 Portfolio 생성 (ID=%s)", new_portfolio_id)
    elif trade_type == 'SELL':
        portfolio = session.get(db_models.Portfolio, stock_info['id'])
        if portfolio:
            portfolio.status = 'SOLD'
            portfolio.sell_state = 'SOLD'
            new_portfolio_id = portfolio.id

    trade_log = db_models.TradeLog(
        portfolio_id=new_portfolio_id,
        stock_code=stock_info['code'],
        trade_type=trade_type,
        quantity=quantity,
        price=price,
        reason=llm_reason,
        strategy_signal=strategy_signal,
        key_metrics_json=key_metrics_json,
        market_context_json=market_context_json,
    )
    session.add(trade_log)
    logger.info("   (SQLAlchemy) TradeLog 저장 (portfolio_id=%s, type=%s)", new_portfolio_id, trade_type)
    return True


def _execute_trade_and_log_legacy(
    connection, trade_type, stock_info, quantity, price, llm_decision,
    initial_stop_loss_price, strategy_signal, key_metrics_dict, market_context_dict
):
    """MariaDB 전용 거래 실행 및 로깅"""
    cursor = None
    try:
        import numpy as np
        
        if price <= 0:
            logger.error(f"❌ DB: execute_trade_and_log price 유효하지 않음 (price: {price})")
            return False
        
        cursor = connection.cursor()
        llm_reason = llm_decision.get('reason', 'N/A') if llm_decision else 'N/A'
        
        MAX_REASON_LENGTH = 1950
        if len(llm_reason) > MAX_REASON_LENGTH:
            llm_reason = llm_reason[:MAX_REASON_LENGTH-3] + '...'
        
        new_portfolio_id = None
        
        def convert_numpy_types(obj):
            if isinstance(obj, dict):
                return {k: convert_numpy_types(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [convert_numpy_types(item) for item in obj]
            elif isinstance(obj, (np.integer, np.int64)):
                return int(obj)
            elif isinstance(obj, (np.floating, np.float64)):
                return float(obj)
            elif isinstance(obj, np.ndarray):
                return obj.tolist()
            else:
                return obj
        
        key_metrics_json = json.dumps(convert_numpy_types(key_metrics_dict or {}))
        market_context_json = json.dumps(convert_numpy_types(market_context_dict or {}))

        portfolio_table = _get_table_name("Portfolio")
        tradelog_table = _get_table_name("TradeLog")
        
        if trade_type.startswith('BUY'):
            sql_check = f"""
            SELECT id, quantity, average_buy_price, total_buy_amount, current_high_price, STOP_LOSS_PRICE, SELL_STATE
            FROM {portfolio_table}
            WHERE stock_code = %s AND status = 'HOLDING'
            ORDER BY id ASC
            LIMIT 1
            """
            cursor.execute(sql_check, [stock_info['code']])
            existing = cursor.fetchone()
            
            if existing:
                existing_id = existing['id']
                existing_quantity = existing['quantity']
                existing_avg_price = existing['average_buy_price']
                existing_total_amount = existing['total_buy_amount']
                existing_high_price = existing['current_high_price']
                existing_stop_loss = existing['STOP_LOSS_PRICE']
                existing_sell_state = existing['SELL_STATE']
                
                new_quantity = existing_quantity + quantity
                new_total_amount = existing_total_amount + (quantity * price)
                new_avg_price = new_total_amount / new_quantity if new_quantity > 0 else price
                new_high_price = max(existing_high_price if existing_high_price else price, price)
                
                if initial_stop_loss_price is None:
                    initial_stop_loss_price = price * 0.93
                new_stop_loss = min(existing_stop_loss if existing_stop_loss and existing_stop_loss > 0 else initial_stop_loss_price, initial_stop_loss_price)
                
                new_sell_state = existing_sell_state if existing_sell_state in ('SECURED', 'TRAILING') else 'INITIAL'
                
                sql_update = f"""
                UPDATE {portfolio_table}
                SET quantity = %s,
                    average_buy_price = %s,
                    total_buy_amount = %s,
                    current_high_price = %s,
                    STOP_LOSS_PRICE = %s,
                    SELL_STATE = %s
                WHERE id = %s
                """
                cursor.execute(sql_update, [
                    new_quantity, new_avg_price, new_total_amount, new_high_price,
                    new_stop_loss, new_sell_state, existing_id
                ])
                new_portfolio_id = existing_id
                logger.info(f"   (DB) Portfolio 업데이트 (ID: {existing_id})")
            else:
                sql_portfolio = f"""
                INSERT INTO {portfolio_table} (
                    stock_code, stock_name, quantity, average_buy_price, total_buy_amount, 
                    current_high_price, status, SELL_STATE, STOP_LOSS_PRICE
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, 'HOLDING', 'INITIAL', %s
                )
                """
                if initial_stop_loss_price is None:
                    initial_stop_loss_price = price * 0.93
                cursor.execute(sql_portfolio, [
                    stock_info['code'], stock_info['name'], quantity, price, quantity * price, 
                    price, initial_stop_loss_price
                ])
                new_portfolio_id = cursor.lastrowid
                logger.info(f"   (DB) 새 Portfolio 생성 (ID: {new_portfolio_id})")
        elif trade_type == 'SELL':
            sql_portfolio = f"UPDATE {portfolio_table} SET status = 'SOLD', SELL_STATE = 'SOLD' WHERE id = %s"
            cursor.execute(sql_portfolio, [stock_info['id']])
            new_portfolio_id = stock_info['id']

        sql_log = f"""
        INSERT INTO {tradelog_table} (
            portfolio_id, stock_code, trade_type, quantity, price, reason, 
            trade_timestamp, 
            STRATEGY_SIGNAL, KEY_METRICS_JSON, MARKET_CONTEXT_JSON
        ) VALUES (
            %s, %s, %s, %s, %s, %s, NOW(),
            %s, %s, %s
        )
        """
        cursor.execute(sql_log, [
            new_portfolio_id, stock_info['code'], trade_type, quantity, price, llm_reason,
            strategy_signal, key_metrics_json, market_context_json
        ])
        logger.info(f"   (DB) TradeLog 저장")
        connection.commit()
        return True
    except Exception as e:
        logger.error(f"❌ DB: execute_trade_and_log 실패! (에러: {e})")
        if connection: connection.rollback()
        return False
    finally:
        if cursor: cursor.close()


def get_trade_log(connection, limit=50):
    """거래 로그 조회"""
    logs_df = None
    cursor = None
    try:
        import pandas as pd
        cursor = connection.cursor()
        tradelog_table = _get_table_name("TradeLog")
        
        if _is_mariadb():
            sql = f"""
            SELECT LOG_ID, PORTFOLIO_ID, STOCK_CODE, TRADE_TYPE, QUANTITY, PRICE, REASON, TRADE_TIMESTAMP
            FROM {tradelog_table}
            ORDER BY TRADE_TIMESTAMP DESC
            LIMIT %s
            """
            cursor.execute(sql, (limit,))
        else:
            sql = f"""
            SELECT LOG_ID, PORTFOLIO_ID, STOCK_CODE, TRADE_TYPE, QUANTITY, PRICE, REASON, TRADE_TIMESTAMP
            FROM {tradelog_table}
            ORDER BY TRADE_TIMESTAMP DESC
            FETCH FIRST {limit} ROWS ONLY
            """
            cursor.execute(sql)
        
        rows = cursor.fetchall()
        if rows:
            logs_df = pd.DataFrame(rows, columns=['LOG_ID', 'PORTFOLIO_ID', 'STOCK_CODE', 'TRADE_TYPE', 'QUANTITY', 'PRICE', 'REASON', 'TRADE_TIMESTAMP'])
        return logs_df
    except Exception as e:
        logger.error(f"❌ DB: get_trade_log 실패! (에러: {e})")
        return None
    finally:
        if cursor: cursor.close()


def was_traded_recently(connection, stock_code, hours=24):
    """
    특정 종목이 최근 N시간 이내에 거래(매수/매도)되었는지 확인합니다.
    """
    cursor = None
    try:
        cursor = connection.cursor()
        tradelog_table = _get_table_name("TradeLog")
        
        if _is_mariadb():
            sql = f"""
            SELECT 1 FROM {tradelog_table}
            WHERE STOCK_CODE = %s 
              AND TRADE_TIMESTAMP >= DATE_SUB(NOW(), INTERVAL %s HOUR)
            LIMIT 1
            """
            cursor.execute(sql, (stock_code, hours))
        else:
            sql = f"""
            SELECT 1 FROM {tradelog_table}
            WHERE STOCK_CODE = :1 
              AND TRADE_TIMESTAMP >= SYSTIMESTAMP - INTERVAL '1' HOUR * :2
              AND ROWNUM = 1
            """
            cursor.execute(sql, [stock_code, hours])
        
        result = cursor.fetchone()
        return result is not None
        
    except Exception as e:
        logger.error(f"❌ DB: was_traded_recently 실패! (에러: {e})")
        return False
    finally:
        if cursor: cursor.close()


def get_recently_traded_stocks_batch(connection, stock_codes: list, hours: int = 24):
    """
    여러 종목의 최근 거래 여부를 한 번에 조회합니다.
    """
    if not stock_codes:
        return set()
    
    cursor = None
    try:
        cursor = connection.cursor()
        tradelog_table = _get_table_name("TradeLog")
        
        if _is_mariadb():
            placeholders = ','.join(['%s'] * len(stock_codes))
            sql = f"""
            SELECT DISTINCT STOCK_CODE 
            FROM {tradelog_table}
            WHERE STOCK_CODE IN ({placeholders}) 
              AND TRADE_TIMESTAMP >= DATE_SUB(NOW(), INTERVAL %s HOUR)
            """
            cursor.execute(sql, stock_codes + [hours])
        else:
            placeholders = ','.join([f':code_{i}' for i in range(len(stock_codes))])
            sql = f"""
            SELECT DISTINCT STOCK_CODE 
            FROM {tradelog_table}
            WHERE STOCK_CODE IN ({placeholders}) 
              AND TRADE_TIMESTAMP >= SYSTIMESTAMP - INTERVAL '1' HOUR * :hours
            """
            params = {f'code_{i}': code for i, code in enumerate(stock_codes)}
            params['hours'] = hours
            cursor.execute(sql, params)
        
        rows = cursor.fetchall()
        
        traded_codes = set()
        for row in rows:
            if isinstance(row, dict):
                traded_codes.add(row.get('STOCK_CODE'))
            else:
                traded_codes.add(row[0])
        
        return traded_codes
        
    except Exception as e:
        logger.error(f"❌ DB: get_recently_traded_stocks_batch 실패! (에러: {e})")
        return set()
    finally:
        if cursor: cursor.close()


def check_duplicate_order(connection, stock_code, trade_type, time_window_minutes=5):
    """
    최근 N분 이내에 동일한 종목에 대한 동일한 유형의 주문이 있었는지 확인 (중복 주문 방지)
    """
    cursor = None
    try:
        cursor = connection.cursor()
        tradelog_table = _get_table_name("TradeLog")
        
        if _is_mariadb():
            sql = f"""
            SELECT 1 FROM {tradelog_table}
            WHERE STOCK_CODE = %s 
              AND TRADE_TYPE = %s
              AND TRADE_TIMESTAMP >= DATE_SUB(NOW(), INTERVAL %s MINUTE)
            LIMIT 1
            """
            cursor.execute(sql, (stock_code, trade_type, time_window_minutes))
        else:
            sql = f"""
            SELECT 1 FROM {tradelog_table}
            WHERE STOCK_CODE = :1 
              AND TRADE_TYPE = :2
              AND TRADE_TIMESTAMP >= SYSTIMESTAMP - INTERVAL '1' MINUTE * :3
              AND ROWNUM = 1
            """
            cursor.execute(sql, [stock_code, trade_type, time_window_minutes])
        
        result = cursor.fetchone()
        
        if result:
            logger.warning(f"⚠️ DB: 중복 주문 감지! ({stock_code}, {trade_type})")
            return True
        return False
        
    except Exception as e:
        logger.error(f"❌ DB: check_duplicate_order 실패! (에러: {e})")
        return False
    finally:
        if cursor: cursor.close()


def remove_from_portfolio(connection, stock_code, quantity):
    """
    포트폴리오에서 종목을 매도 처리합니다.
    """
    cursor = None
    try:
        cursor = connection.cursor()
        portfolio_table = _get_table_name("Portfolio")
        
        if _is_mariadb():
            sql_select = f"""
            SELECT ID, QUANTITY, AVERAGE_BUY_PRICE 
            FROM {portfolio_table} 
            WHERE STOCK_CODE = %s AND STATUS = 'HOLDING'
            FOR UPDATE
            """
            cursor.execute(sql_select, (stock_code,))
        else:
            sql_select = f"""
            SELECT ID, QUANTITY, AVERAGE_BUY_PRICE 
            FROM {portfolio_table} 
            WHERE STOCK_CODE = :1 AND STATUS = 'HOLDING'
            FOR UPDATE
            """
            cursor.execute(sql_select, [stock_code])
        
        row = cursor.fetchone()
        
        if not row:
            logger.warning(f"⚠️ DB: 매도 처리 실패 - 보유 중인 종목이 아님 ({stock_code})")
            return False
        
        if isinstance(row, dict):
            portfolio_id = row['ID']
            current_qty = row['QUANTITY']
            avg_price = row['AVERAGE_BUY_PRICE']
        else:
            portfolio_id, current_qty, avg_price = row
        
        if current_qty <= quantity:
            # 전량 매도
            if _is_mariadb():
                sql_update = f"""
                UPDATE {portfolio_table}
                SET STATUS = 'SOLD', SELL_STATE = 'SOLD', QUANTITY = 0, UPDATED_AT = NOW()
                WHERE ID = %s
                """
                cursor.execute(sql_update, (portfolio_id,))
            else:
                sql_update = f"""
                UPDATE {portfolio_table}
                SET STATUS = 'SOLD', SELL_STATE = 'SOLD', QUANTITY = 0, UPDATED_AT = SYSTIMESTAMP
                WHERE ID = :1
                """
                cursor.execute(sql_update, [portfolio_id])
            logger.info(f"✅ DB: 전량 매도 처리 완료 ({stock_code}, {current_qty}주)")
        else:
            # 부분 매도
            new_qty = current_qty - quantity
            new_total_amount = new_qty * avg_price
            if _is_mariadb():
                sql_update = f"""
                UPDATE {portfolio_table}
                SET QUANTITY = %s, TOTAL_BUY_AMOUNT = %s, UPDATED_AT = NOW()
                WHERE ID = %s
                """
                cursor.execute(sql_update, (new_qty, new_total_amount, portfolio_id))
            else:
                sql_update = f"""
                UPDATE {portfolio_table}
                SET QUANTITY = :1, TOTAL_BUY_AMOUNT = :2, UPDATED_AT = SYSTIMESTAMP
                WHERE ID = :3
                """
                cursor.execute(sql_update, [new_qty, new_total_amount, portfolio_id])
            logger.info(f"✅ DB: 부분 매도 처리 완료 ({stock_code}, {quantity}주 매도, 잔여 {new_qty}주)")
            
        connection.commit()
        return True
        
    except Exception as e:
        logger.error(f"❌ DB: remove_from_portfolio 실패! (에러: {e})")
        if connection: connection.rollback()
        return False
    finally:
        if cursor: cursor.close()
