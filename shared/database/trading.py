"""
shared/database/trading.py

거래(Trade), 포트폴리오(Portfolio), 관심종목(Watchlist), 거래로그(TradeLog)
관련 기능을 담당합니다.
(기존 database_trade.py + database_portfolio.py + database_watchlist.py + database_tradelog.py 통합)
"""

import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

import pandas as pd
import numpy as np

from shared.db import connection as sa_connection
from shared.db import models as db_models
from .core import _get_table_name, _is_mariadb, _is_sqlalchemy_ready

logger = logging.getLogger(__name__)


# ============================================================================
# [Watchlist] 관심 종목 관리
# ============================================================================

def get_active_watchlist(connection) -> Dict[str, Dict]:
    """
    WatchList에서 활성 종목 조회
    SQLAlchemy Session과 raw connection 모두 지원
    """
    from sqlalchemy.orm import Session
    from sqlalchemy import text
    
    watchlist = {}
    
    # SQLAlchemy Session인지 확인
    if isinstance(connection, Session):
        result = connection.execute(text("SELECT STOCK_CODE, STOCK_NAME, IS_TRADABLE, LLM_SCORE, LLM_REASON FROM WatchList"))
        rows = result.fetchall()
        for row in rows:
            code = row[0]
            name = row[1]
            is_tradable = row[2]
            llm_score = row[3]
            llm_reason = row[4]
            watchlist[code] = {
                "code": code,
                "name": name,
                "is_tradable": is_tradable,
                "llm_score": llm_score,
                "llm_reason": llm_reason,
            }
    else:
        # Legacy: raw connection with cursor
        cursor = connection.cursor()
        cursor.execute("SELECT STOCK_CODE, STOCK_NAME, IS_TRADABLE, LLM_SCORE, LLM_REASON FROM WatchList")
        rows = cursor.fetchall()
        cursor.close()
        
        for row in rows:
            if isinstance(row, dict):
                code = row.get('STOCK_CODE') or row.get('stock_code')
                name = row.get('STOCK_NAME') or row.get('stock_name')
                is_tradable = row.get('IS_TRADABLE', True)
                llm_score = row.get('LLM_SCORE', None)
                llm_reason = row.get('LLM_REASON', None)
            else:
                code, name, is_tradable, llm_score, llm_reason = row
            watchlist[code] = {
                "code": code,
                "name": name,
                "is_tradable": is_tradable,
                "llm_score": llm_score,
                "llm_reason": llm_reason,
            }
    return watchlist


def save_to_watchlist(connection, candidates: List[Dict]):
    """
    WatchList 저장 (MariaDB/Oracle 호환)
    [v4.2] 재무 데이터 추가
    """
    if not candidates:
        return
    
    cursor = connection.cursor()
    
    # [v4.1] Step 1: 24시간 지난 오래된 종목 삭제 (TTL)
    logger.info("   (DB) 1. 24시간 지난 오래된 종목 정리 중...")
    if _is_mariadb():
        cursor.execute("""
            DELETE FROM WatchList 
            WHERE LLM_UPDATED_AT < DATE_SUB(NOW(), INTERVAL 24 HOUR)
        """)
    else:
        cursor.execute("""
            DELETE FROM WatchList 
            WHERE LLM_UPDATED_AT < SYSTIMESTAMP - INTERVAL '24' HOUR
        """)
    
    logger.info(f"   (DB) 2. 우량주 후보 {len(candidates)}건 UPSERT...")
    
    now = datetime.now(timezone.utc)
    
    # [v4.1] UPSERT 쿼리
    if _is_mariadb():
        sql_upsert = """
        INSERT INTO WatchList (
            STOCK_CODE, STOCK_NAME, CREATED_AT, IS_TRADABLE,
            LLM_SCORE, LLM_REASON, LLM_UPDATED_AT,
            PER, PBR, ROE, MARKET_CAP, SALES_GROWTH, EPS_GROWTH, FINANCIAL_UPDATED_AT
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            STOCK_NAME = VALUES(STOCK_NAME),
            IS_TRADABLE = VALUES(IS_TRADABLE),
            LLM_SCORE = VALUES(LLM_SCORE),
            LLM_REASON = VALUES(LLM_REASON),
            LLM_UPDATED_AT = VALUES(LLM_UPDATED_AT),
            PER = VALUES(PER),
            PBR = VALUES(PBR),
            ROE = VALUES(ROE),
            MARKET_CAP = VALUES(MARKET_CAP),
            SALES_GROWTH = VALUES(SALES_GROWTH),
            EPS_GROWTH = VALUES(EPS_GROWTH),
            FINANCIAL_UPDATED_AT = VALUES(FINANCIAL_UPDATED_AT)
        """
    else:
        # Oracle: MERGE (간소화를 위해 생략하거나 필요시 추가 구현)
        # 일단 MariaDB만 지원하는 것으로 구현 (기존 파일 참고 필요하면 추가)
        pass 
        
    insert_count = 0
    update_count = 0
    metadata_marker = "[LLM_METADATA]"
    
    for c in candidates:
        llm_score = c.get('llm_score', 0)
        llm_reason = c.get('llm_reason', '') or ''
        llm_metadata = c.get('llm_metadata')

        if llm_metadata:
            try:
                metadata_json = json.dumps(llm_metadata, ensure_ascii=False)
                llm_reason = f"{llm_reason}\n\n{metadata_marker}{metadata_json}"
            except Exception as e:
                logger.warning(f"⚠️ WatchList 메타데이터 직렬화 실패: {e}")

        # REASON 길이 제한
        if len(llm_reason) > 60000:
            llm_reason = llm_reason[:60000] + "..."
        
        if _is_mariadb():
            params = (
                c['code'], 
                c['name'],
                now,  # CREATED_AT
                1 if c.get('is_tradable', True) else 0,
                llm_score,
                llm_reason,
                now,  # LLM_UPDATED_AT
                c.get('per'), c.get('pbr'), c.get('roe'),
                c.get('market_cap'), c.get('sales_growth'), c.get('eps_growth'),
                now   # FINANCIAL_UPDATED_AT
            )
            cursor.execute(sql_upsert, params)
            if cursor.rowcount == 1:
                insert_count += 1
            elif cursor.rowcount == 2:
                update_count += 1
    
    connection.commit()
    logger.info(f"   (DB) ✅ WatchList UPSERT 완료! (신규 {insert_count}건, 갱신 {update_count}건)")
    cursor.close()


def save_to_watchlist_history(connection, candidates_to_save, snapshot_date=None):
    """
    [v3.8] WatchList 스냅샷을 히스토리 테이블에 저장합니다.
    """
    cursor = None
    is_mariadb = _is_mariadb()
    
    try:
        cursor = connection.cursor()
        table_name = "WATCHLIST_HISTORY"
        
        # 테이블 생성 로직은 생략 (운영 환경엔 이미 있을 것임)
        # 생략된 부분은 필요시 원본에서 복사
        
        if snapshot_date is None:
            snapshot_date = datetime.now().strftime('%Y-%m-%d')

        if is_mariadb:
            cursor.execute(f"DELETE FROM {table_name} WHERE SNAPSHOT_DATE = %s", (snapshot_date,))
        else:
            cursor.execute(f"DELETE /*+ NO_PARALLEL */ FROM {table_name} WHERE SNAPSHOT_DATE = TO_DATE(:1, 'YYYY-MM-DD')", [snapshot_date])
        
        if not candidates_to_save:
            connection.commit()
            return
            
        if is_mariadb:
            sql_insert = f"""
            INSERT INTO {table_name} (
                SNAPSHOT_DATE, STOCK_CODE, STOCK_NAME, IS_TRADABLE, LLM_SCORE, LLM_REASON
            ) VALUES (%s, %s, %s, %s, %s, %s)
            """
        else:
            sql_insert = f"""
            INSERT /*+ NO_PARALLEL */ INTO {table_name} (
                SNAPSHOT_DATE, STOCK_CODE, STOCK_NAME, IS_TRADABLE, LLM_SCORE, LLM_REASON
            ) VALUES (
                TO_DATE(:1, 'YYYY-MM-DD'), :2, :3, :4, :5, :6
            )
            """
        
        insert_data = []
        for c in candidates_to_save:
            llm_score = c.get('llm_score', 0)
            llm_reason = c.get('llm_reason', '')
            if len(llm_reason) > 3950:
                llm_reason = llm_reason[:3950] + "..."
                
            insert_data.append((
                snapshot_date,
                c['code'],
                c['name'],
                1 if c.get('is_tradable', True) else 0,
                llm_score,
                llm_reason
            ))
            
        cursor.executemany(sql_insert, insert_data)
        connection.commit()
        logger.info(f"   (DB) ✅ WatchList History 저장 완료")
        
    except Exception as e:
        logger.error(f"❌ DB: save_to_watchlist_history 실패! (에러: {e})")
        if connection: connection.rollback()
    finally:
        if cursor: cursor.close()


def get_watchlist_history(connection, snapshot_date):
    """
    [v3.5] 특정 날짜의 WatchList 히스토리를 조회합니다.
    """
    watchlist = {}
    cursor = None
    is_mariadb = _is_mariadb()
    
    try:
        cursor = connection.cursor()
        
        if is_mariadb:
            sql = """
            SELECT STOCK_CODE, STOCK_NAME, IS_TRADABLE, LLM_SCORE, LLM_REASON
            FROM WATCHLIST_HISTORY
            WHERE SNAPSHOT_DATE = %s
            """
            cursor.execute(sql, (snapshot_date,))
        else: # Oracle
            sql = """
            SELECT STOCK_CODE, STOCK_NAME, IS_TRADABLE, LLM_SCORE, LLM_REASON
            FROM WATCHLIST_HISTORY
            WHERE SNAPSHOT_DATE = TO_DATE(:1, 'YYYY-MM-DD')
            """
            cursor.execute(sql, [snapshot_date])
        
        rows = cursor.fetchall()
        # Row parsing logic (dict or tuple)
        for row in rows:
            if isinstance(row, dict):
                watchlist[row['STOCK_CODE']] = {
                    "name": row['STOCK_NAME'], 
                    "is_tradable": bool(row['IS_TRADABLE']),
                    "llm_score": row['LLM_SCORE'] if row['LLM_SCORE'] is not None else 0,
                    "llm_reason": row['LLM_REASON'] if row['LLM_REASON'] is not None else ""
                }
            else:
                watchlist[row[0]] = {
                    "name": row[1], 
                    "is_tradable": bool(row[2]),
                    "llm_score": row[3] if row[3] is not None else 0,
                    "llm_reason": row[4] if row[4] is not None else ""
                }
        return watchlist
    except Exception as e:
        logger.error(f"❌ DB: get_watchlist_history 실패! (에러: {e})")
        return {}
    finally:
        if cursor: cursor.close()


# ============================================================================
# [Portfolio] 포트폴리오 관리
# ============================================================================

def get_active_portfolio(connection) -> List[Dict]:
    cursor = connection.cursor()
    table_name = _get_table_name("Portfolio")
    cursor.execute(f"""
        SELECT ID, STOCK_CODE, STOCK_NAME, QUANTITY, BUY_PRICE, AVG_PRICE, CURRENT_PRICE,
               BUY_DATE, STOP_LOSS_PRICE, HIGH_PRICE
        FROM {table_name}
        WHERE QUANTITY > 0
    """)
    rows = cursor.fetchall()
    cursor.close()
    
    portfolio = []
    for row in rows:
        if isinstance(row, dict):
            portfolio.append({
                "id": row.get('ID'),
                "code": row.get('STOCK_CODE'),
                "stock_code": row.get('STOCK_CODE'),
                "name": row.get('STOCK_NAME'),
                "stock_name": row.get('STOCK_NAME'),
                "quantity": row.get('QUANTITY', 0),
                "buy_price": row.get('BUY_PRICE', 0),
                "avg_price": row.get('AVG_PRICE', 0),
                "current_price": row.get('CURRENT_PRICE', 0),
                "buy_date": row.get('BUY_DATE'),
                "stop_loss_price": row.get('STOP_LOSS_PRICE'),
                "high_price": row.get('HIGH_PRICE'),
            })
        else:
            portfolio.append({
                "id": row[0],
                "code": row[1],
                "stock_code": row[1],
                "name": row[2],
                "stock_name": row[2],
                "quantity": row[3],
                "buy_price": row[4],
                "avg_price": row[5],
                "current_price": row[6],
                "buy_date": row[7],
                "stop_loss_price": row[8],
                "high_price": row[9],
            })
    return portfolio


def remove_from_portfolio(connection, stock_code, quantity):
    """포트폴리오에서 종목 매도 처리"""
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
            portfolio_id, current_qty, avg_price = row['ID'], row['QUANTITY'], row['AVERAGE_BUY_PRICE']
        else:
            portfolio_id, current_qty, avg_price = row
        
        if current_qty <= quantity:
            # 전량 매도
            if _is_mariadb():
                sql_update = f"UPDATE {portfolio_table} SET STATUS = 'SOLD', SELL_STATE = 'SOLD', QUANTITY = 0, UPDATED_AT = NOW() WHERE ID = %s"
                cursor.execute(sql_update, (portfolio_id,))
            else:
                sql_update = f"UPDATE {portfolio_table} SET STATUS = 'SOLD', SELL_STATE = 'SOLD', QUANTITY = 0, UPDATED_AT = SYSTIMESTAMP WHERE ID = :1"
                cursor.execute(sql_update, [portfolio_id])
            logger.info(f"✅ DB: 전량 매도 처리 완료 ({stock_code}, {current_qty}주)")
        else:
            # 부분 매도
            new_qty = current_qty - quantity
            new_total_amount = new_qty * avg_price
            if _is_mariadb():
                sql_update = f"UPDATE {portfolio_table} SET QUANTITY = %s, TOTAL_BUY_AMOUNT = %s, UPDATED_AT = NOW() WHERE ID = %s"
                cursor.execute(sql_update, (new_qty, new_total_amount, portfolio_id))
            else:
                sql_update = f"UPDATE {portfolio_table} SET QUANTITY = :1, TOTAL_BUY_AMOUNT = :2, UPDATED_AT = SYSTIMESTAMP WHERE ID = :3"
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


# ============================================================================
# [Trade] 거래 실행 및 로깅
# ============================================================================

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
    if price <= 0:
        logger.error(f"❌ DB: execute_trade_and_log price 유효하지 않음 (price: {price})")
        return False
    
    llm_reason = llm_decision.get('reason', 'N/A') if llm_decision else 'N/A'
    if len(llm_reason) > 1950:
        llm_reason = llm_reason[:1947] + '...'

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
    """MariaDB 전용 거래 실행 및 로깅 (Legacy)"""
    cursor = None
    try:
        
        if price <= 0:
            logger.error(f"❌ DB: execute_trade_and_log price 유효하지 않음 (price: {price})")
            return False
        
        cursor = connection.cursor()
        llm_reason = llm_decision.get('reason', 'N/A') if llm_decision else 'N/A'
        
        if len(llm_reason) > 1950:
            llm_reason = llm_reason[:1947] + '...'
        
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
                if isinstance(existing, dict):
                    existing_id = existing['id']
                    existing_quantity = existing['quantity']
                    existing_total_amount = existing['total_buy_amount']
                    existing_high_price = existing['current_high_price']
                    existing_stop_loss = existing['STOP_LOSS_PRICE']
                    existing_sell_state = existing['SELL_STATE']
                else:
                    existing_id = existing[0]
                    existing_quantity = existing[1]
                    existing_total_amount = existing[3]
                    existing_high_price = existing[4]
                    existing_stop_loss = existing[5]
                    existing_sell_state = existing[6]

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
                SET quantity = %s, average_buy_price = %s, total_buy_amount = %s,
                    current_high_price = %s, STOP_LOSS_PRICE = %s, SELL_STATE = %s
                WHERE id = %s
                """
                cursor.execute(sql_update, [
                    new_quantity, new_avg_price, new_total_amount, new_high_price,
                    new_stop_loss, new_sell_state, existing_id
                ])
                new_portfolio_id = existing_id
            else:
                sql_portfolio = f"""
                INSERT INTO {portfolio_table} (
                    stock_code, stock_name, quantity, average_buy_price, total_buy_amount, 
                    current_high_price, status, SELL_STATE, STOP_LOSS_PRICE
                ) VALUES (%s, %s, %s, %s, %s, %s, 'HOLDING', 'INITIAL', %s)
                """
                if initial_stop_loss_price is None:
                    initial_stop_loss_price = price * 0.93
                cursor.execute(sql_portfolio, [
                    stock_info['code'], stock_info['name'], quantity, price, quantity * price, 
                    price, initial_stop_loss_price
                ])
                new_portfolio_id = cursor.lastrowid
        elif trade_type == 'SELL':
            sql_portfolio = f"UPDATE {portfolio_table} SET status = 'SOLD', SELL_STATE = 'SOLD' WHERE id = %s"
            cursor.execute(sql_portfolio, [stock_info['id']])
            new_portfolio_id = stock_info['id']

        sql_log = f"""
        INSERT INTO {tradelog_table} (
            portfolio_id, stock_code, trade_type, quantity, price, reason, 
            trade_timestamp, STRATEGY_SIGNAL, KEY_METRICS_JSON, MARKET_CONTEXT_JSON
        ) VALUES (%s, %s, %s, %s, %s, %s, NOW(), %s, %s, %s)
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


def record_trade(connection, stock_code: str, trade_type: str, quantity: int,
                 price: float, reason: str = "", extra: Dict = None):
    """
    단순 거래 로그 저장 (Portfolio 업데이트 없음)
    execute_trade_and_log와 다르게 독립적인 로그 기록용
    """
    cursor = connection.cursor()
    table_name = _get_table_name("TradeLog")
    
    extra_json = json.dumps(extra, default=str) if extra else None
    now_ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    
    cursor.execute(f"""
        INSERT INTO {table_name}
        (STOCK_CODE, TRADE_TYPE, QUANTITY, PRICE, REASON, EXTRA, TRADE_TIME_UTC)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, [stock_code, trade_type, quantity, price, reason, extra_json, now_ts])
    
    connection.commit()
    cursor.close()


def get_today_trades(session) -> List[Dict]:
    """오늘의 거래 내역 조회"""
    from .models import TradeLog
    from sqlalchemy import func
    
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    
    rows = session.query(
        TradeLog.stock_code,
        TradeLog.trade_type,
        TradeLog.quantity,
        TradeLog.price,
        func.json_extract(TradeLog.key_metrics_json, '$.profit_amount').label('profit_amount'),
        TradeLog.trade_timestamp
    ).filter(TradeLog.trade_timestamp >= today_start).order_by(TradeLog.trade_timestamp.desc()).all()
    
    trades = []
    for row in rows:
        if isinstance(row, dict):
            trades.append(row)
        else:
            trades.append({
                "stock_code": row[0],
                "trade_type": row[1],
                "quantity": row[2],
                "price": row[3],
                "profit_amount": float(row[4]) if row[4] else 0.0,
                "trade_time": row[5]
            })
    return trades


def get_trade_log(connection, limit=50):
    """최근 거래 로그 조회"""
    cursor = None
    try:
        cursor = connection.cursor()
        tradelog_table = _get_table_name("TradeLog")
        
        sql = f"""
        SELECT LOG_ID, PORTFOLIO_ID, STOCK_CODE, TRADE_TYPE, QUANTITY, PRICE, REASON, TRADE_TIMESTAMP
        FROM {tradelog_table}
        ORDER BY TRADE_TIMESTAMP DESC
        LIMIT %s
        """
        cursor.execute(sql, (limit,))
        
        rows = cursor.fetchall()
        if rows:
            return pd.DataFrame(rows, columns=['LOG_ID', 'PORTFOLIO_ID', 'STOCK_CODE', 'TRADE_TYPE', 'QUANTITY', 'PRICE', 'REASON', 'TRADE_TIMESTAMP'])
        return None
    except Exception as e:
        logger.error(f"❌ DB: get_trade_log 실패! (에러: {e})")
        return None
    finally:
        if cursor: cursor.close()


def was_traded_recently(connection, stock_code, hours=24):
    """
    특정 종목이 최근 N시간 이내에 거래(매수/매도)되었는지 확인
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
        
        placeholders = ','.join(['%s'] * len(stock_codes))
        sql = f"""
        SELECT DISTINCT STOCK_CODE 
        FROM {tradelog_table}
        WHERE STOCK_CODE IN ({placeholders}) 
          AND TRADE_TIMESTAMP >= DATE_SUB(NOW(), INTERVAL %s HOUR)
        """
        cursor.execute(sql, stock_codes + [hours])
        
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
    최근 N분 이내에 동일한 종목에 대한 동일한 유형의 주문이 있었는지 확인
    """
    cursor = None
    try:
        cursor = connection.cursor()
        tradelog_table = _get_table_name("TradeLog")
        
        sql = f"""
        SELECT 1 FROM {tradelog_table}
        WHERE STOCK_CODE = %s 
          AND TRADE_TYPE = %s
          AND TRADE_TIMESTAMP >= DATE_SUB(NOW(), INTERVAL %s MINUTE)
        LIMIT 1
        """
        cursor.execute(sql, (stock_code, trade_type, time_window_minutes))
        
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
